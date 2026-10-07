from __future__ import annotations

import hashlib
import os

import pytest
from secdogie_identity import Allowlist, Identity, sign_payload
from secdogie_identity import linkauth as la

NOW = 1_760_000_000.0


def _fp(alg: str, seed: str) -> str:
    n = {"sha-1": 20, "sha-256": 32, "sha-384": 48, "sha-512": 64}[alg]
    raw = hashlib.sha512(seed.encode()).digest() * 2
    return f"{alg} " + ":".join(f"{b:02X}" for b in raw[:n])


# A browser lists the one fingerprint of its own certificate; aiortc lists three for its
# one certificate; a browser re-serializing aiortc's SDP keeps only the one it checks.
BROWSER = (_fp("sha-256", "browser"),)
NODE = (_fp("sha-256", "node"), _fp("sha-384", "node"), _fp("sha-512", "node"))
NODE_AS_BROWSER_SEES_IT = (_fp("sha-256", "node"),)
MITM = (_fp("sha-256", "mitm"),)


def _sdp(fps, *, media="m=application 9 UDP/DTLS/SCTP webrtc-datachannel", eol="\r\n") -> str:
    lines = ["v=0", "o=- 1 2 IN IP4 127.0.0.1", "s=-", "t=0 0", media, "c=IN IP4 0.0.0.0"]
    lines += [f"a=fingerprint:{fp.split(' ')[0]} {fp.split(' ')[1].lower()}" for fp in fps]
    return eol.join(lines) + eol


@pytest.fixture
def ids():
    return Identity.generate(), Identity.generate(), Identity.generate()  # node, app, operator


# ---- SDP ------------------------------------------------------------------------------------


def test_fingerprints_from_sdp_normalizes_dedupes_and_sorts():
    sdp = _sdp(NODE) + "a=fingerprint:SHA-256 " + NODE[0].split(" ")[1] + "\r\n"
    assert la.fingerprints_from_sdp(sdp) == tuple(sorted(NODE))
    assert la.fingerprints_from_sdp(_sdp(BROWSER, eol="\n")) == BROWSER


def test_fingerprints_from_sdp_refuses_none_or_weak_only():
    with pytest.raises(ValueError, match="no DTLS fingerprint"):
        la.fingerprints_from_sdp(_sdp(()))
    with pytest.raises(ValueError, match="sha-256"):
        la.fingerprints_from_sdp(_sdp((_fp("sha-1", "x"),)))
    with pytest.raises(ValueError):
        la.fingerprints_from_sdp(None)  # type: ignore[arg-type]


def test_sdp_is_data_only():
    assert la.sdp_is_data_only(_sdp(BROWSER))
    assert la.sdp_is_data_only(_sdp(BROWSER, media="m=application 9 DTLS/SCTP 5000"))
    assert not la.sdp_is_data_only(_sdp(BROWSER) + "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n")
    assert not la.sdp_is_data_only(_sdp(BROWSER, media="m=audio 9 UDP/TLS/RTP/SAVPF 111"))
    assert not la.sdp_is_data_only("v=0\r\n")


# ---- W1 ---------------------------------------------------------------------------------------


def _node_binding(node, *, local=NODE, remote=BROWSER, room="standing-room", at=NOW):
    return la.create_link_binding(node, room=room, local=local, remote=remote, issued_at=at)


def _page_binding(app, *, local=BROWSER, remote=NODE_AS_BROWSER_SEES_IT, room="standing-room", at=NOW):
    return la.create_link_binding(app, room=room, local=local, remote=remote, issued_at=at)


def test_binding_between_a_browser_and_aiortc_verifies_both_ways(ids):
    node, app, _ = ids
    # the page checks the node's statement against what the page sees
    r = la.verify_link_binding(_node_binding(node), trust=Allowlist({node.did}), room="standing-room",
                               observed_local=BROWSER, observed_remote=NODE_AS_BROWSER_SEES_IT, now=NOW)
    assert r.ok and r.did == node.did
    # the node checks the page's statement against what the node sees
    r = la.verify_link_binding(_page_binding(app), trust=Allowlist({app.did}), room="standing-room",
                               observed_local=NODE, observed_remote=BROWSER, now=NOW + 60)
    assert r.ok and r.did == app.did


@pytest.mark.parametrize("observed_local, observed_remote, why", [
    (BROWSER, MITM, "not the one it signed for"),                          # a relay's certificate
    (BROWSER, NODE_AS_BROWSER_SEES_IT + MITM, "not the one it signed for"),  # one injected beside it
    (MITM, NODE_AS_BROWSER_SEES_IT, "not ours"),                           # the node saw someone else
    (NODE_AS_BROWSER_SEES_IT, BROWSER, "not the one it signed for"),       # local and remote swapped
    ((), NODE_AS_BROWSER_SEES_IT, "empty"),
])
def test_binding_refuses_a_man_in_the_middle(ids, observed_local, observed_remote, why):
    node, _, _ = ids
    r = la.verify_link_binding(_node_binding(node), trust=Allowlist({node.did}), room="standing-room",
                               observed_local=observed_local, observed_remote=observed_remote, now=NOW)
    assert not r.ok and why in r.reason


def test_binding_refuses_untrusted_wrong_room_stale_and_retyped(ids):
    node, app, _ = ids
    b = _node_binding(node)
    kw = dict(observed_local=BROWSER, observed_remote=NODE_AS_BROWSER_SEES_IT)
    r = la.verify_link_binding(b, trust=Allowlist({app.did}), room="standing-room", now=NOW, **kw)
    assert not r.ok and r.reason == "signer not trusted" and r.did == node.did
    assert la.verify_link_binding(b, trust=Allowlist({node.did}), room="other", now=NOW, **kw).reason == "room mismatch"
    late = la.verify_link_binding(b, trust=Allowlist({node.did}), room="standing-room", now=NOW + 121, **kw)
    assert late.reason == "stale or future statement"
    forged = dict(b, room="other")
    assert la.verify_link_binding(forged, trust=Allowlist({node.did}), room="other", now=NOW, **kw).reason \
        == "invalid or missing signature"
    retyped = sign_payload(node, {**{k: v for k, v in b.items() if k not in ("signer", "sig")},
                                  "type": la.PAIRED_TYPE})
    assert not la.verify_link_binding(retyped, trust=Allowlist({node.did}), room="standing-room", now=NOW, **kw).ok


def test_binding_with_a_lone_surrogate_is_refused_not_a_crash(ids):
    node, _, _ = ids
    b = dict(_node_binding(node), room="\ud800")
    r = la.verify_link_binding(b, trust=Allowlist({node.did}), room="\ud800", observed_local=BROWSER,
                               observed_remote=NODE_AS_BROWSER_SEES_IT, now=NOW)
    assert not r.ok


# ---- rooms and links ----------------------------------------------------------------------------


def test_rooms(ids):
    node, _, _ = ids
    room = la.derive_room(node)
    assert room == la.derive_room(node) and la.ROOM_PATTERN.match(room) and len(room) == 32
    assert la.derive_room(node, 1) != room
    secret = os.urandom(32)
    assert la.pairing_room(secret) != room and la.ROOM_PATTERN.match(la.pairing_room(secret))
    assert len(la.pairing_id(secret)) == 32
    with pytest.raises(ValueError):
        la.derive_room(node, -1)
    with pytest.raises(ValueError):
        la.pairing_id(b"short")


def test_pairing_link_round_trips_and_holds_no_address(ids):
    node, _, _ = ids
    secret = os.urandom(32)
    link = la.pairing_link("https://ui.example/#old", node_did=node.did, secret=secret, expires_at=int(NOW) + 600)
    assert link.startswith("https://ui.example/#pair=") and "#old" not in link
    inv = la.parse_pairing_fragment(link.split("#", 1)[1], now=NOW)
    assert (inv.node_did, inv.secret, inv.expires_at) == (node.did, secret, int(NOW) + 600)
    assert inv.room == la.pairing_room(secret) and inv.pairing_id == la.pairing_id(secret)
    assert "wss" not in la.b64url_decode(link.split("pair=", 1)[1]).decode()


def test_pairing_link_refuses_expired_unknown_and_malformed(ids):
    node, _, _ = ids
    secret = os.urandom(32)
    frag = la.pairing_fragment(node_did=node.did, secret=secret, expires_at=int(NOW))
    with pytest.raises(ValueError, match="expired"):
        la.parse_pairing_fragment(frag, now=NOW)
    extra = "pair=" + la.b64url(b'{"exp":9999999999,"node":"%s","secret":"%s","signal":"wss://evil/ws","v":1}'
                                % (node.did.encode(), la.b64url(secret).encode()))
    with pytest.raises(ValueError, match="unknown"):
        la.parse_pairing_fragment(extra, now=NOW)
    for bad in ("", "demo", "pair=", "pair=!!", "pair=" + la.b64url(b"[]")):
        with pytest.raises(ValueError):
            la.parse_pairing_fragment(bad, now=NOW)


# ---- pairing ---------------------------------------------------------------------------------------


def _pairing(ids, *, operator=True):
    node, app, op = ids
    secret = os.urandom(32)
    hello = la.create_pair_hello(app, secret=secret, node_did=node.did, local=BROWSER,
                                 remote=NODE_AS_BROWSER_SEES_IT, operator_did=op.did if operator else "",
                                 issued_at=NOW)
    return secret, hello


def _verify_hello(ids, secret, hello, **kw):
    node = ids[0]
    args = dict(secret=secret, node_did=node.did, observed_local=NODE, observed_remote=BROWSER, now=NOW)
    args.update(kw)
    return la.verify_pair_hello(hello, **args)


def test_pair_hello_verifies_and_both_ends_get_the_same_code(ids):
    _, app, op = ids
    secret, hello = _pairing(ids)
    r = _verify_hello(ids, secret, hello)
    assert r.ok and r.app_did == app.did and r.operator_did == op.did
    assert r.code == la.check_code(hello)  # the page computes it from the hello it signed
    assert len(r.code) == 14 and r.code.replace(" ", "").isdigit() and r.code.count(" ") == 2


def test_pair_hello_flags_a_bad_mac_and_refuses_the_rest(ids):
    node, app, _ = ids
    secret, hello = _pairing(ids)
    r = _verify_hello(ids, os.urandom(32), hello)
    assert not r.ok and r.mac_failed
    assert _verify_hello(ids, secret, hello, node_did=app.did).reason == "for another node or pairing"
    assert "not the one it signed for" in _verify_hello(ids, secret, hello, observed_remote=MITM).reason
    assert _verify_hello(ids, secret, hello, now=NOW + 500).reason == "stale or future statement"
    padded = sign_payload(app, {**{k: v for k, v in hello.items() if k not in ("signer", "sig")}, "x": 1})
    assert _verify_hello(ids, secret, padded).reason == "unexpected fields"
    with pytest.raises(ValueError):
        la.create_pair_hello(app, secret=secret, node_did=node.did, local=BROWSER, remote=NODE, operator_did=app.did)


def test_pair_confirm_with_the_operator_proof(ids):
    node, app, op = ids
    secret, hello = _pairing(ids)
    proof = la.create_operator_proof(op, app_did=app.did, node_did=node.did, pairing=la.pairing_id(secret),
                                     issued_at=NOW)
    confirm = la.create_pair_confirm(app, secret=secret, node_did=node.did, hello=hello, operator_proof=proof,
                                     issued_at=NOW + 5)
    r = la.verify_pair_confirm(confirm, secret=secret, node_did=node.did, hello=hello, now=NOW + 6)
    assert r.ok and r.operator_did == op.did


def test_pair_confirm_refusals(ids):
    node, app, op = ids
    secret, hello = _pairing(ids)
    pid = la.pairing_id(secret)
    kw = dict(secret=secret, node_did=node.did, hello=hello, now=NOW)

    def confirm(proof=None, *, by=app, h=hello):
        return la.create_pair_confirm(by, secret=secret, node_did=node.did, hello=h, operator_proof=proof,
                                      issued_at=NOW)

    good = la.create_operator_proof(op, app_did=app.did, node_did=node.did, pairing=pid, issued_at=NOW)
    other_app = la.create_operator_proof(op, app_did=node.did, node_did=node.did, pairing=pid, issued_at=NOW)
    other_key = la.create_operator_proof(Identity.generate(), app_did=app.did, node_did=node.did, pairing=pid,
                                         issued_at=NOW)
    assert la.verify_pair_confirm(confirm(other_app), **kw).reason == "operator proof for another pairing"
    assert la.verify_pair_confirm(confirm(other_key), **kw).reason == "operator proof from another key"
    assert la.verify_pair_confirm(confirm(None), **kw).reason.startswith("operator proof:")
    assert la.verify_pair_confirm(confirm(good, by=Identity.generate()), **kw).reason == "confirmed by another App"
    _, other_hello = _pairing(ids)
    assert la.verify_pair_confirm(confirm(good, h=other_hello), **kw).reason == "confirms another hello"
    assert la.verify_pair_confirm(confirm(good), **dict(kw, secret=os.urandom(32))).mac_failed
    # no operator offered: no proof allowed
    secret2, plain = _pairing(ids, operator=False)
    c = la.create_pair_confirm(app, secret=secret2, node_did=node.did, hello=plain, operator_proof=None, issued_at=NOW)
    assert la.verify_pair_confirm(c, secret=secret2, node_did=node.did, hello=plain, now=NOW).ok
    c = la.create_pair_confirm(app, secret=secret2, node_did=node.did, hello=plain, operator_proof=good, issued_at=NOW)
    assert la.verify_pair_confirm(c, secret=secret2, node_did=node.did, hello=plain, now=NOW).reason \
        == "an operator proof nobody offered"


def test_paired_and_refused_receipts(ids):
    node, app, op = ids
    pid = "ab" * 16
    paired = la.create_paired(node, app_did=app.did, operator_did=op.did, room="standing-room", pairing=pid,
                              local=NODE, remote=BROWSER, issued_at=NOW)
    kw = dict(observed_local=BROWSER, observed_remote=NODE_AS_BROWSER_SEES_IT, now=NOW)
    r = la.verify_paired(paired, node_did=node.did, app_did=app.did, pairing=pid, **kw)
    assert r.ok and r.room == "standing-room" and r.operator_did == op.did
    assert la.verify_paired(paired, node_did=app.did, app_did=app.did, pairing=pid, **kw).reason \
        == "signed by another node"
    assert not la.verify_paired(paired, node_did=node.did, app_did=app.did, pairing=pid,
                                observed_local=BROWSER, observed_remote=MITM, now=NOW).ok

    refusal = la.create_refusal(node, reason="not-enrolled", local=NODE, remote=BROWSER, issued_at=NOW)
    r = la.verify_refusal(refusal, node_did=node.did, **kw)
    assert r.ok and r.refusal == "not-enrolled"
    assert not la.verify_refusal(refusal, node_did=app.did, **kw).ok
    assert not la.verify_refusal(refusal, node_did=node.did, observed_local=MITM,
                                 observed_remote=NODE_AS_BROWSER_SEES_IT, now=NOW).ok
    with pytest.raises(ValueError):
        la.create_refusal(node, reason="go away", local=NODE, remote=BROWSER)
