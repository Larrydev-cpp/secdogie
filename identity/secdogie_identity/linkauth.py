"""Browser links: who is on the other end of a WebRTC data channel.

A WebRTC data channel is encrypted by DTLS, but the DTLS certificates are
self-signed and their fingerprints travel through the signaling server -- which
is therefore in a position to sit in the middle. This module adds the missing
step without new cryptography: each end signs, with its DID, the fingerprints
it actually sees in its own SDP (W1, ``secdogie/webrtc-dtls-binding/v1``), and
each end checks the other's statement against what *it* sees. A server in the
middle terminates two DTLS sessions with two different certificates, so one of
the two checks always fails.

Fingerprint rule (both directions, every statement below that carries them)::

    what I see as yours  must be among what you say is yours, and
    what you saw as mine must be among what I actually have,

with every set non-empty and holding at least one sha-256 / sha-384 / sha-512
fingerprint. A subset, not equality: a browser re-serializes a remote SDP with
only the one fingerprint it will check (aiortc lists three), so exact equality
would refuse every honest browser-to-node link. A fingerprint added or swapped
in transit is still outside the other side's statement and is refused.

First contact is a one-time pairing, confirmed on both ends:

  1. the node shows a link holding its DID and a 32-byte secret, on its own
     terminal only (``pairing_link``);
  2. the page meets the node in a room derived from that secret, the node's W1
     statement verifies against the DID in the link, and the page sends a
     ``pair-hello``: its App DID (and, optionally, an operator DID), both
     fingerprint sets, an HMAC under the secret, signed by the App key;
  3. both ends turn the same signed hello into a 12-digit check code
     (``check_code``) -- the terminal asks the owner whether the page shows the
     same digits, the page asks for one tap;
  4. the tap sends a ``pair-confirm`` (and, if offered, the operator key's
     proof); the node enrolls only with both, and answers ``paired`` -- or
     ``refused`` -- signed and bound to the same fingerprints.

Every statement is a ``sign_payload`` object with its own type tag, so none can
be replayed as another (or as a fleet message, a journal event, a token).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass

from .keys import Identity, PublicIdentity
from .signing import canonical, sign_payload, verify_payload

LINK_BINDING_TYPE = "secdogie/webrtc-dtls-binding/v1"
PAIR_HELLO_TYPE = "secdogie/webrtc-pair-hello/v1"
PAIR_CONFIRM_TYPE = "secdogie/webrtc-pair-confirm/v1"
PAIR_OPERATOR_TYPE = "secdogie/webrtc-pair-operator/v1"
PAIRED_TYPE = "secdogie/webrtc-paired/v1"
REFUSED_TYPE = "secdogie/webrtc-refused/v1"

MAX_SKEW = 120.0  # seconds a statement's issued_at may differ from the verifier's clock
SECRET_BYTES = 32
STRONG_ALGORITHMS = frozenset({"sha-256", "sha-384", "sha-512"})
ROOM_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Why the node turned a link away. Machine words: the page words them itself.
REFUSAL_REASONS = frozenset({
    "not-enrolled",          # a W1 statement from a DID that is not on --apps
    "busy",                  # another App is connected right now
    "pairing-unavailable",   # no pairing offer, or it expired / was used / was burned
    "pairing-rejected",      # the owner said no on the terminal, or the request failed a check
})

_FP_LINE = re.compile(r"^a=fingerprint:(\S+)[ \t]+([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2})+)[ \t]*\r?$", re.MULTILINE)
_M_LINE = re.compile(r"^m=(\S+)[ \t]+\S+[ \t]+(\S+)", re.MULTILINE)


# ---- small helpers ------------------------------------------------------------------


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64url_decode(text: str) -> bytes:
    if not isinstance(text, str) or not re.fullmatch(r"[A-Za-z0-9_-]*", text) or len(text) % 4 == 1:
        raise ValueError("not unpadded base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _is_time(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")


def _fresh(issued_at, now: float) -> bool:
    return _is_time(issued_at) and abs(float(now) - float(issued_at)) <= MAX_SKEW


def _fp_list(v) -> tuple[str, ...] | None:
    if not isinstance(v, list) or not v or len(v) > 8:
        return None
    if any(not isinstance(x, str) or not _normalize_ok(x) for x in v):
        return None
    return tuple(v)


def _normalize_ok(fp: str) -> bool:
    alg, _, hexpart = fp.partition(" ")
    return bool(alg) and alg == alg.lower() and bool(re.fullmatch(r"[0-9A-F]{2}(?::[0-9A-F]{2})+", hexpart))


def _now(now, clock) -> float:
    return float(now) if now is not None else float(clock())


# ---- SDP --------------------------------------------------------------------------------


def fingerprints_from_sdp(sdp: str) -> tuple[str, ...]:
    """Every ``a=fingerprint`` line (session or media level) as ``"alg HEX:.."``,
    lower-case algorithm, upper-case hex, de-duplicated and sorted. Raises
    ValueError when there is none, or none of sha-256 / sha-384 / sha-512."""
    if not isinstance(sdp, str):
        raise ValueError("an SDP is text")
    found = {f"{alg.lower()} {hexpart.upper()}" for alg, hexpart in _FP_LINE.findall(sdp)}
    if not found:
        raise ValueError("the SDP carries no DTLS fingerprint")
    if not any(fp.split(" ", 1)[0] in STRONG_ALGORITHMS for fp in found):
        raise ValueError("the SDP carries no sha-256 (or stronger) fingerprint")
    return tuple(sorted(found))


def sdp_is_data_only(sdp: str) -> bool:
    """True when every media section is an SCTP data channel (``m=application
    ... DTLS/SCTP ...``, in either the current or the older SDP form) and there
    is at least one. A link that offers audio or video is refused before it is
    answered."""
    if not isinstance(sdp, str):
        return False
    lines = _M_LINE.findall(sdp)
    if not lines:
        return False
    return all(media == "application" and "DTLS/SCTP" in proto.upper() for media, proto in lines)


def fingerprints_agree(*, declared_local, declared_remote, observed_local, observed_remote) -> str | None:
    """The fingerprint rule. ``declared_*`` come from the other side's signed
    statement, ``observed_*`` from this side's own SDP. None when they agree,
    else the reason."""
    sets = {}
    for name, v in (("declared local", declared_local), ("declared remote", declared_remote),
                    ("observed local", observed_local), ("observed remote", observed_remote)):
        s = set(v or ())
        if not s:
            return f"{name} fingerprints are empty"
        if not any(fp.split(" ", 1)[0] in STRONG_ALGORITHMS for fp in s):
            return f"{name} fingerprints hold no sha-256 or stronger"
        sets[name] = s
    if not sets["observed remote"] <= sets["declared local"]:
        return "the peer's certificate is not the one it signed for"
    if not sets["declared remote"] <= sets["observed local"]:
        return "the peer saw a certificate that is not ours"
    return None


# ---- rooms and the pairing link ---------------------------------------------------------


def derive_room(identity: Identity, epoch: int = 0) -> str:
    """The node's standing signaling room: secret (only the node key can make
    it), stable across restarts, and new for each ``epoch``. The signed message
    is not JSON, so it can never collide with a signature over a payload."""
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
        raise ValueError("epoch must be a non-negative int")
    sig = identity.sign(b"secdogie/webrtc-room/v1\n" + str(epoch).encode("ascii"))
    return b64url(hashlib.sha256(sig).digest())[:32]


def pairing_id(secret: bytes) -> str:
    return hashlib.sha256(b"secdogie/pairing-id/v1" + _secret(secret)).hexdigest()[:32]


def pairing_room(secret: bytes) -> str:
    """The one-time room a pairing meets in, so pairing never takes the slot of
    the standing room."""
    return "p" + b64url(hashlib.sha256(b"secdogie/pairing-room/v1" + _secret(secret)).digest())[:31]


def _secret(secret) -> bytes:
    if not isinstance(secret, (bytes, bytearray)) or len(secret) != SECRET_BYTES:
        raise ValueError(f"a pairing secret is {SECRET_BYTES} bytes")
    return bytes(secret)


def pairing_fragment(*, node_did: str, secret: bytes, expires_at: int) -> str:
    """The URL fragment (after ``#``) of a pairing link. It holds no signaling
    address and no ICE servers: those come from the page's own build, so a
    crafted link cannot point the page anywhere else."""
    PublicIdentity.from_did(node_did)
    body = {"v": 1, "node": node_did, "secret": b64url(_secret(secret)), "exp": int(expires_at)}
    return "pair=" + b64url(canonical(body))


def pairing_link(ui: str, *, node_did: str, secret: bytes, expires_at: int) -> str:
    return f"{ui.split('#', 1)[0]}#{pairing_fragment(node_did=node_did, secret=secret, expires_at=expires_at)}"


@dataclass(frozen=True)
class PairingInvite:
    node_did: str
    secret: bytes
    expires_at: int

    @property
    def pairing_id(self) -> str:
        return pairing_id(self.secret)

    @property
    def room(self) -> str:
        return pairing_room(self.secret)


def parse_pairing_fragment(fragment: str, *, now: float | None = None, clock=time.time) -> PairingInvite:
    """Read a fragment made by ``pairing_fragment`` (with or without the
    leading ``#``). Raises ValueError for anything else, including an expired
    link or any field it does not know."""
    if not isinstance(fragment, str):
        raise ValueError("not a pairing link")
    frag = fragment[1:] if fragment.startswith("#") else fragment
    if not frag.startswith("pair=") or len(frag) > 512:
        raise ValueError("not a pairing link")
    try:
        body = json.loads(b64url_decode(frag[5:]).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise ValueError("a malformed pairing link") from e
    if not isinstance(body, dict) or set(body) != {"v", "node", "secret", "exp"} or body["v"] != 1:
        raise ValueError("an unknown pairing link")
    node, exp = body["node"], body["exp"]
    if not isinstance(node, str) or isinstance(exp, bool) or not isinstance(exp, int):
        raise ValueError("a malformed pairing link")
    PublicIdentity.from_did(node)
    secret = b64url_decode(body["secret"])
    _secret(secret)
    if exp <= _now(now, clock):
        raise ValueError("the pairing link has expired")
    return PairingInvite(node, secret, exp)


# ---- results ------------------------------------------------------------------------------


@dataclass(frozen=True)
class LinkResult:
    ok: bool
    reason: str | None = None
    did: str | None = None


@dataclass(frozen=True)
class HelloResult:
    ok: bool
    reason: str | None = None
    app_did: str | None = None
    operator_did: str | None = None
    mac_failed: bool = False
    code: str | None = None


@dataclass(frozen=True)
class ConfirmResult:
    ok: bool
    reason: str | None = None
    operator_did: str | None = None
    mac_failed: bool = False


@dataclass(frozen=True)
class PairedResult:
    ok: bool
    reason: str | None = None
    app_did: str | None = None
    operator_did: str | None = None
    room: str | None = None


@dataclass(frozen=True)
class RefusalResult:
    ok: bool
    reason: str | None = None
    refusal: str | None = None


def _signed(obj, type_, trust) -> tuple[str | None, str | None, str | None]:
    """(signer, None, signer) for a well-formed statement of ``type_`` whose
    signature verifies (against ``trust`` when given) and whose ``did`` is its
    signer; else (None, reason, the valid signer if there was one)."""
    if not isinstance(obj, dict) or obj.get("type") != type_:
        return None, "wrong or missing type", None
    ok, signer = verify_payload(obj, trust)
    if not ok:
        return None, ("signer not trusted" if signer else "invalid or missing signature"), signer
    if obj.get("did") != signer:
        return None, "did does not match signer", None
    return signer, None, signer


def _check_fps(obj, observed_local, observed_remote) -> str | None:
    local, remote = _fp_list(obj.get("local_fingerprints")), _fp_list(obj.get("remote_fingerprints"))
    if local is None or remote is None:
        return "malformed fingerprints"
    return fingerprints_agree(declared_local=local, declared_remote=remote,
                              observed_local=observed_local, observed_remote=observed_remote)


def _mac(secret: bytes, core: dict) -> str:
    return b64url(hmac.new(_secret(secret), canonical(core), hashlib.sha256).digest())


def _mac_ok(secret: bytes, obj: dict) -> bool:
    mac = obj.get("mac")
    if not isinstance(mac, str):
        return False
    core = {k: v for k, v in obj.items() if k not in ("mac", "signer", "sig")}
    return hmac.compare_digest(mac, _mac(secret, core))


# ---- W1: the DTLS binding -------------------------------------------------------------


def create_link_binding(identity: Identity, *, room: str, local, remote, issued_at: float | None = None,
                        clock=time.time) -> dict:
    """This side's W1 statement: the fingerprints of its own SDP (``local``)
    and of the peer's SDP as it received it (``remote``)."""
    if not ROOM_PATTERN.match(room or ""):
        raise ValueError("a bad room name")
    return sign_payload(identity, {
        "type": LINK_BINDING_TYPE,
        "did": identity.did,
        "room": room,
        "local_fingerprints": sorted(set(local)),
        "remote_fingerprints": sorted(set(remote)),
        "issued_at": float(issued_at if issued_at is not None else clock()),
    })


def verify_link_binding(obj, *, trust, room: str, observed_local, observed_remote, now: float | None = None,
                        clock=time.time) -> LinkResult:
    """The peer's W1 statement against ``trust`` (the node: its ``--apps``; a
    page: exactly the paired node's DID) and this side's own view."""
    signer, why, who = _signed(obj, LINK_BINDING_TYPE, trust)
    if signer is None:
        return LinkResult(False, why, did=who)
    if obj.get("room") != room:
        return LinkResult(False, "room mismatch", did=signer)
    bad = _check_fps(obj, observed_local, observed_remote)
    if bad:
        return LinkResult(False, bad, did=signer)
    if not _fresh(obj.get("issued_at"), _now(now, clock)):
        return LinkResult(False, "stale or future statement", did=signer)
    return LinkResult(True, None, did=signer)


# ---- pairing ------------------------------------------------------------------------------


def create_pair_hello(app: Identity, *, secret: bytes, node_did: str, local, remote, operator_did: str = "",
                      issued_at: float | None = None, clock=time.time) -> dict:
    """The page's first pairing statement, sent before any tap. ``operator_did``
    names an operator key the page offers; it is only a name until the tap's
    ``pair-confirm`` carries that key's own proof."""
    if operator_did:
        PublicIdentity.from_did(operator_did)
        if operator_did == app.did:
            raise ValueError("the operator key must not be the App key")
    core = {
        "type": PAIR_HELLO_TYPE,
        "did": app.did,
        "node": node_did,
        "room": pairing_room(secret),
        "pairing_id": pairing_id(secret),
        "operator": operator_did,
        "local_fingerprints": sorted(set(local)),
        "remote_fingerprints": sorted(set(remote)),
        "issued_at": float(issued_at if issued_at is not None else clock()),
    }
    return sign_payload(app, {**core, "mac": _mac(secret, core)})


def check_code(hello: dict) -> str:
    """The 12-digit code both ends show for one signed hello, in three groups of
    four (``"4821 0937 5512"``). Digits on purpose: the same in every language,
    no word list to keep in step. Both ends hash the very bytes the page signed,
    so they agree even though they see the fingerprints from opposite sides."""
    digest = hashlib.sha256(b"secdogie/pairing-sas/v1" + canonical(hello)).digest()
    n = int.from_bytes(digest[:8], "big") % 10**12
    s = f"{n:012d}"
    return f"{s[0:4]} {s[4:8]} {s[8:12]}"


def hello_hash(hello: dict) -> str:
    return hashlib.sha256(canonical(hello)).hexdigest()


def verify_pair_hello(obj, *, secret: bytes, node_did: str, observed_local, observed_remote,
                      now: float | None = None, clock=time.time) -> HelloResult:
    """The node's check of a hello: shape, App signature, HMAC under the
    secret, the node / room / pairing id, the fingerprints, freshness. A MAC
    failure is flagged so the caller can count it against the offer."""
    signer, why, _ = _signed(obj, PAIR_HELLO_TYPE, None)
    if signer is None:
        return HelloResult(False, why)
    if set(obj) != {"type", "did", "node", "room", "pairing_id", "operator", "local_fingerprints",
                    "remote_fingerprints", "issued_at", "mac", "signer", "sig"}:
        return HelloResult(False, "unexpected fields")
    if not _mac_ok(secret, obj):
        return HelloResult(False, "bad mac", mac_failed=True)
    if obj.get("node") != node_did or obj.get("room") != pairing_room(secret) \
            or obj.get("pairing_id") != pairing_id(secret):
        return HelloResult(False, "for another node or pairing")
    op = obj.get("operator")
    if not isinstance(op, str):
        return HelloResult(False, "malformed operator")
    if op:
        try:
            PublicIdentity.from_did(op)
        except ValueError:
            return HelloResult(False, "malformed operator")
        if op == signer:
            return HelloResult(False, "the operator key is the App key")
    bad = _check_fps(obj, observed_local, observed_remote)
    if bad:
        return HelloResult(False, bad)
    if not _fresh(obj.get("issued_at"), _now(now, clock)):
        return HelloResult(False, "stale or future statement")
    return HelloResult(True, None, app_did=signer, operator_did=op or None, code=check_code(obj))


def create_operator_proof(operator: Identity, *, app_did: str, node_did: str, pairing: str,
                          issued_at: float | None = None, clock=time.time) -> dict:
    """The operator key's own consent to be enrolled for this App, node and
    pairing -- signed at the tap, the only time it signs anything but a Gate 2
    token."""
    return sign_payload(operator, {
        "type": PAIR_OPERATOR_TYPE,
        "did": operator.did,
        "app": app_did,
        "node": node_did,
        "pairing_id": pairing,
        "issued_at": float(issued_at if issued_at is not None else clock()),
    })


def create_pair_confirm(app: Identity, *, secret: bytes, node_did: str, hello: dict, operator_proof: dict | None = None,
                        issued_at: float | None = None, clock=time.time) -> dict:
    """The page's tap: it names the exact hello it confirms and carries the
    operator proof when an operator key was offered."""
    core = {
        "type": PAIR_CONFIRM_TYPE,
        "did": app.did,
        "node": node_did,
        "pairing_id": pairing_id(secret),
        "hello": hello_hash(hello),
        "operator_proof": operator_proof,
        "issued_at": float(issued_at if issued_at is not None else clock()),
    }
    return sign_payload(app, {**core, "mac": _mac(secret, core)})


def verify_pair_confirm(obj, *, secret: bytes, node_did: str, hello: dict, now: float | None = None,
                        clock=time.time) -> ConfirmResult:
    """The node's check of the tap: same App as the hello, same hello, HMAC,
    and -- when the hello offered an operator -- that key's proof for this App,
    node and pairing."""
    signer, why, _ = _signed(obj, PAIR_CONFIRM_TYPE, None)
    if signer is None:
        return ConfirmResult(False, why)
    if set(obj) != {"type", "did", "node", "pairing_id", "hello", "operator_proof", "issued_at", "mac",
                    "signer", "sig"}:
        return ConfirmResult(False, "unexpected fields")
    if not _mac_ok(secret, obj):
        return ConfirmResult(False, "bad mac", mac_failed=True)
    if signer != hello.get("did"):
        return ConfirmResult(False, "confirmed by another App")
    if obj.get("node") != node_did or obj.get("pairing_id") != pairing_id(secret):
        return ConfirmResult(False, "for another node or pairing")
    if obj.get("hello") != hello_hash(hello):
        return ConfirmResult(False, "confirms another hello")
    t = _now(now, clock)
    if not _fresh(obj.get("issued_at"), t):
        return ConfirmResult(False, "stale or future statement")
    offered = hello.get("operator") or None
    proof = obj.get("operator_proof")
    if offered is None:
        if proof is not None:
            return ConfirmResult(False, "an operator proof nobody offered")
        return ConfirmResult(True)
    op, why, _ = _signed(proof, PAIR_OPERATOR_TYPE, None)
    if op is None:
        return ConfirmResult(False, f"operator proof: {why}")
    if op != offered or op == signer:
        return ConfirmResult(False, "operator proof from another key")
    if proof.get("app") != signer or proof.get("node") != node_did or proof.get("pairing_id") != pairing_id(secret):
        return ConfirmResult(False, "operator proof for another pairing")
    if not _fresh(proof.get("issued_at"), t):
        return ConfirmResult(False, "stale operator proof")
    return ConfirmResult(True, None, operator_did=op)


def create_paired(node: Identity, *, app_did: str, operator_did: str = "", room: str, pairing: str, local, remote,
                  issued_at: float | None = None, clock=time.time) -> dict:
    """The node's receipt: who was enrolled (the operator empty when it was
    not), and the standing room to meet in from now on."""
    if not ROOM_PATTERN.match(room or ""):
        raise ValueError("a bad room name")
    return sign_payload(node, {
        "type": PAIRED_TYPE,
        "did": node.did,
        "app": app_did,
        "operator": operator_did,
        "room": room,
        "pairing_id": pairing,
        "local_fingerprints": sorted(set(local)),
        "remote_fingerprints": sorted(set(remote)),
        "issued_at": float(issued_at if issued_at is not None else clock()),
    })


def verify_paired(obj, *, node_did: str, app_did: str, pairing: str, observed_local, observed_remote,
                  now: float | None = None, clock=time.time) -> PairedResult:
    signer, why, _ = _signed(obj, PAIRED_TYPE, None)
    if signer is None:
        return PairedResult(False, why)
    if signer != node_did:
        return PairedResult(False, "signed by another node")
    if obj.get("app") != app_did or obj.get("pairing_id") != pairing:
        return PairedResult(False, "for another App or pairing")
    room, op = obj.get("room"), obj.get("operator")
    if not isinstance(room, str) or not ROOM_PATTERN.match(room) or not isinstance(op, str):
        return PairedResult(False, "malformed receipt")
    bad = _check_fps(obj, observed_local, observed_remote)
    if bad:
        return PairedResult(False, bad)
    if not _fresh(obj.get("issued_at"), _now(now, clock)):
        return PairedResult(False, "stale or future statement")
    return PairedResult(True, None, app_did=app_did, operator_did=op or None, room=room)


def create_refusal(node: Identity, *, reason: str, local, remote, pairing: str = "",
                   issued_at: float | None = None, clock=time.time) -> dict:
    if reason not in REFUSAL_REASONS:
        raise ValueError(f"unknown refusal reason {reason!r}")
    return sign_payload(node, {
        "type": REFUSED_TYPE,
        "did": node.did,
        "reason": reason,
        "pairing_id": pairing,
        "local_fingerprints": sorted(set(local)),
        "remote_fingerprints": sorted(set(remote)),
        "issued_at": float(issued_at if issued_at is not None else clock()),
    })


def verify_refusal(obj, *, node_did: str, observed_local, observed_remote, now: float | None = None,
                   clock=time.time) -> RefusalResult:
    """A refusal only counts when the node itself signed it for this very link
    -- so nobody else can make a page forget its pairing."""
    signer, why, _ = _signed(obj, REFUSED_TYPE, None)
    if signer is None:
        return RefusalResult(False, why)
    if signer != node_did:
        return RefusalResult(False, "signed by another node")
    if obj.get("reason") not in REFUSAL_REASONS or not isinstance(obj.get("pairing_id"), str):
        return RefusalResult(False, "malformed refusal")
    bad = _check_fps(obj, observed_local, observed_remote)
    if bad:
        return RefusalResult(False, bad)
    if not _fresh(obj.get("issued_at"), _now(now, clock)):
        return RefusalResult(False, "stale or future statement")
    return RefusalResult(True, None, refusal=obj["reason"])


__all__ = [
    "LINK_BINDING_TYPE", "PAIR_HELLO_TYPE", "PAIR_CONFIRM_TYPE", "PAIR_OPERATOR_TYPE", "PAIRED_TYPE",
    "REFUSED_TYPE", "REFUSAL_REASONS", "MAX_SKEW", "SECRET_BYTES", "STRONG_ALGORITHMS", "ROOM_PATTERN",
    "LinkResult", "HelloResult", "ConfirmResult", "PairedResult", "RefusalResult", "PairingInvite",
    "b64url", "b64url_decode", "fingerprints_from_sdp", "sdp_is_data_only", "fingerprints_agree",
    "derive_room", "pairing_id", "pairing_room", "pairing_fragment", "pairing_link", "parse_pairing_fragment",
    "create_link_binding", "verify_link_binding", "create_pair_hello", "verify_pair_hello", "check_code",
    "hello_hash", "create_operator_proof", "create_pair_confirm", "verify_pair_confirm", "create_paired",
    "verify_paired", "create_refusal", "verify_refusal",
]
