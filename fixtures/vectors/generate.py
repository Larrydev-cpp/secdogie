"""Golden vectors shared by the Python, Rust (graph/) and TypeScript (symbiont/)
implementations of secdogie's canonical JSON, Gate 2 authorization, state-graph
deltas and the Socratic instruction review.

Python is the reference: every expected value here is produced by the real
production functions (``secdogie_identity.signing.canonical`` / ``sign_payload``,
``secdogie_citadel.authz``, ``secdogie_citadel.socratic``,
``secdogie_dialogue.protocol.seal``), never written by hand. The Rust and TS test
suites read these files and must agree byte for byte; the Python suite
(``dialogue/tests/test_cross_language_vectors.py``) regenerates them and fails if
the committed files drifted.

Every float, and every integer past 2**53, appears only inside a JSON *text*
string, so reading the vector files with a plain JSON parser never loses the
int/float distinction the cases are about.

The generator imports only identity, citadel, dialogue and the transport's
pure modules (never ``secdogie_transport.webrtc`` or ``secdogie_node``), so the
dialogue CI job -- which installs exactly those, with the transport's
``[linkcipher]`` extra for AES-GCM -- can regenerate everything.

    python fixtures/vectors/generate.py          # rewrite every file
    python fixtures/vectors/generate.py --check  # exit 1 if any file is stale

The seeds below are public test keys. Never use them for anything real.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

from secdogie_citadel import socratic
from secdogie_citadel.authz import action_hash, create_authorization
from secdogie_dialogue import guard
from secdogie_dialogue.protocol import (
    PROTOCOL_VERSION,
    Gate2ChallengePacket,
    Header,
    RiskLevel,
    TargetAction,
    Verdict,
    seal,
)
from secdogie_identity import Identity, sign_payload
from secdogie_identity.signing import canonical

HERE = Path(__file__).resolve().parent

GRAPH_DELTA_TYPE = "secdogie/state-graph-delta/v1"
STATE_KEY_TYPE = "secdogie/state-key/v1"


def _seed(label: str) -> bytes:
    return hashlib.sha256(f"secdogie/vectors/{label}".encode()).digest()


def _identity(label: str) -> tuple[Identity, str]:
    seed = _seed(label)
    return Identity.from_seed_b64(base64.b64encode(seed).decode("ascii")), seed.hex()


def _text(obj) -> str:
    """The canonical JSON text of ``obj`` -- also its wire form."""
    return canonical(obj).decode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---- canonical JSON ---------------------------------------------------------

# (name, wire text). Each is parsed with json.loads and re-encoded with canonical().
_CANONICAL_CASES = [
    ("scalars", '[null,true,false,0,1,-1,"",[],{}]'),
    ("whitespace is not significant", '{ "b" : 1 ,\n\t"a" : [ 1 , 2 ] }'),
    ("keys sort by code point, not UTF-16", '{"\\uffff":1,"\\ud83d\\ude00":2,"a":3,"B":4,"\\u00e9":5,"":6}'),
    ("nested objects sort at every level", '{"z":{"y":1,"x":[{"b":1,"a":2}]},"a":null}'),
    ("integral float keeps .0", "[1.0,100.0,123456789.0,1759740120.0]"),
    ("negative zero float", "[-0.0,-1e-400]"),
    ("integer -0 is 0", "-0"),
    ("exponent threshold: 1e16 and up", "[1e16,1.5e16,1e22,1.7976931348623157e308]"),
    ("fixed below 1e16", "[1e15,9999999999999998.0,1000000000000000.5]"),
    ("exponent threshold: below 1e-4", "[0.0001,0.00001,1e-7,1.23e-18,5e-324]"),
    ("shortest round trip digits", "[0.30000000000000004,0.1000000000000000055511151231257827,2.5,1759740120.123456]"),
    ("upper-case exponent and explicit plus", "[1E2,1.5E+3,2e-0]"),
    ("underflow to zero is a float", "1e-400"),
    ("integer past 2**53 stays exact", "[9007199254740993,1759740000123456789,-123456789012345678901234567890]"),
    ("non-ASCII is emitted raw", '"\\u00e9 \\u4e2d\\u6587 caf\\u00e9 \\ud83d\\ude00"'),
    ("line and paragraph separators are raw", '"\\u2028\\u2029"'),
    ("short escapes", '"\\" \\\\ \\/ \\b \\f \\n \\r \\t"'),
    ("other control characters use lower-case \\u00xx", '"\\u0000\\u0001\\u001f\\u007f"'),
]

# Inputs the Rust and TS implementations refuse. ``python`` records what the
# reference does with them, so the gap is written down rather than hidden.
_CANONICAL_REJECT = [
    ("NaN", "[NaN]", "non-finite number", "json.loads accepts it and canonical() emits NaN"),
    ("Infinity", '{"a":Infinity}', "non-finite number", "json.loads accepts it and canonical() emits Infinity"),
    ("overflow to infinity", "1e400", "non-finite number", "parses as inf; canonical() emits Infinity"),
    ("lone surrogate", '"\\ud800"', "lone surrogate", "canonical() raises UnicodeEncodeError"),
    ("duplicate key", '{"a":1,"a":2}', "duplicate key", "json.loads keeps the last value"),
    ("leading zero", "[01]", "invalid number", "json.loads raises"),
    ("trailing data", "{} {}", "trailing data", "json.loads raises"),
    ("bare control character in string", '"a\nb"', "control character in string", "json.loads raises"),
    ("single quotes", "{'a':1}", "invalid JSON", "json.loads raises"),
]


def _python_reject_behavior(text: str) -> str:
    try:
        value = json.loads(text)
    except ValueError:
        return "rejected"
    try:
        canonical(value)
    except (UnicodeEncodeError, ValueError):
        return "rejected"
    return "accepted"


def canonical_vectors() -> dict:
    cases = []
    for name, text in _CANONICAL_CASES:
        out = canonical(json.loads(text))
        cases.append({"name": name, "input": text, "canonical": out.decode("utf-8"), "sha256": _sha(out)})
    reject = []
    for name, text, reason, python in _CANONICAL_REJECT:
        reject.append({
            "name": name, "input": text, "reason": reason,
            "python": python, "python_result": _python_reject_behavior(text),
        })
    return {
        "spec": "canonical(json.loads(input)) with canonical = json.dumps(sort_keys=True, "
                "separators=(',', ':'), ensure_ascii=False).encode('utf-8')",
        "cases": cases,
        "reject": reject,
    }


# ---- Gate 2: secdogie/action-authorization/v1 ----------------------------------

_VALID_FROM = "1759740000.0"
_EXPIRES_AT = "1759740120.5"
_CHALLENGE_EXPIRES_AT = "1759740120.0"
_CHALLENGE_TS_NS = 1759740000123456789  # past 2**53: a JS number cannot hold it
_RESPONSE_TS_NS = 1759740005987654321


def gate2_vectors() -> dict:
    operator, operator_seed = _identity("operator")
    node, node_seed = _identity("node")
    actions = [
        TargetAction(kind="click", target_id="ax:42", target_role="button",
                     target_name="Delete account", text="", high_risk=True),
        TargetAction(kind="type", target_id="", target_role="textbox",
                     target_name="Reason — 原因", text="no longer needed ✅\n", high_risk=False),
        TargetAction(kind="submit"),
    ]
    action_cases = []
    for a in actions:
        action_cases.append({
            "action": {"kind": a.kind, "target_id": a.target_id, "target_role": a.target_role,
                       "target_name": a.target_name, "text": a.text, "high_risk": a.high_risk},
            "hash_input": _text({k: getattr(a, k) for k in
                                 ("kind", "target_id", "target_role", "target_name", "text", "high_risk")}),
            "action_hash": action_hash(a),
        })

    action = actions[0]
    token = create_authorization(operator, action, node.did,
                                 valid_from=float(_VALID_FROM), expires_at=float(_EXPIRES_AT))
    signed_body = {k: v for k, v in token.items() if k not in ("signer", "sig")}

    challenge = Gate2ChallengePacket(
        challenge_id="c0ffee0000000001",
        target_action=action,
        risk_level=RiskLevel.IRREVERSIBLE,
        risk_explanation="deletes the account and its data; there is no undo",
        action_hash=action_hash(action),
        subject_did=node.did,
        expires_at=float(_CHALLENGE_EXPIRES_AT),
    )
    challenge_header = Header(PROTOCOL_VERSION, node.did, operator.did, "s-vectors-0001", 7, _CHALLENGE_TS_NS)
    challenge_env = seal(node, challenge_header, challenge)

    # The operator's answer, exactly as the Dialogue App's guard builds it.
    now = float(_VALID_FROM) + guard.CLOCK_LEEWAY
    response = guard.respond(challenge, Verdict.APPROVE, peer_did=node.did, operator=operator, now=now)
    response_header = Header(PROTOCOL_VERSION, operator.did, node.did, "s-vectors-0002", 3, _RESPONSE_TS_NS)
    response_env = seal(operator, response_header, response)

    return {
        "authorization_type": "secdogie/action-authorization/v1",
        "authorized_fields": ["kind", "target_id", "target_role", "target_name", "text", "high_risk"],
        "operator": {"seed_hex": operator_seed, "did": operator.did},
        "node": {"seed_hex": node_seed, "did": node.did},
        "actions": action_cases,
        "token": {
            "action_index": 0,
            "subject": node.did,
            "valid_from": _VALID_FROM,
            "expires_at": _EXPIRES_AT,
            "signed_body": _text(signed_body),
            "wire": _text(token),
        },
        "challenge": {
            "now": "1759740010.0",
            "wire": _text(challenge_env),
        },
        "response": {
            "guard_now": repr(now),
            "token_wire": _text(response.authorization),
            "wire": _text(response_env),
        },
    }


# ---- state-graph deltas -------------------------------------------------------


def state_key(origin: str, route: str, query_keys: list[str]) -> str:
    return _sha(canonical({"type": STATE_KEY_TYPE, "origin": origin, "route": route, "query_keys": query_keys}))


def _delta(author: Identity, parents: list[str], lamport, ops: list[dict]) -> tuple[dict, dict]:
    payload = {"type": GRAPH_DELTA_TYPE, "author": author.did, "parents": parents, "lamport": lamport, "ops": ops}
    return payload, sign_payload(author, payload)


def graph_delta_vectors() -> dict:
    agent, agent_seed = _identity("agent")
    stranger, stranger_seed = _identity("stranger")
    origin = "https://docs.example.com"
    install = state_key(origin, "/guide/install", ["lang"])
    delete = state_key(origin, "/account/delete", [])
    content = _sha(b"<html>install guide</html>")

    root_payload, root = _delta(agent, [], 1, [
        {"op": "add_state", "origin": origin, "route": "/guide/install", "query_keys": ["lang"]},
        {"op": "add_observation", "state": install, "content_hash": content, "byte_len": 26},
    ])
    root_cid = _sha(canonical(root_payload))
    child_payload, child = _delta(agent, [root_cid], 2, [
        {"op": "add_state", "origin": origin, "route": "/account/delete", "query_keys": []},
        {"op": "add_reference", "from": install, "to": delete, "via": "form", "method": "post",
         "fields": ["confirm", "reason"]},
    ])
    child_cid = _sha(canonical(child_payload))

    bad_sig = dict(root)
    raw = bytearray(base64.b64decode(root["sig"]))
    raw[0] ^= 0x01
    bad_sig["sig"] = base64.b64encode(bytes(raw)).decode("ascii")

    _, tombstone = _delta(agent, [root_cid], 2, [{"op": "remove_state", "state": install}])
    _, wrong_lamport = _delta(agent, [root_cid], 5, [
        {"op": "add_state", "origin": origin, "route": "/a", "query_keys": []}])
    _, float_lamport = _delta(agent, [], 1.0, [
        {"op": "add_state", "origin": origin, "route": "/b", "query_keys": []}])
    _, unsorted_parents = _delta(agent, sorted([root_cid, child_cid], reverse=True), 3, [
        {"op": "add_state", "origin": origin, "route": "/c", "query_keys": []}])
    _, untrusted = _delta(stranger, [], 1, [
        {"op": "add_state", "origin": origin, "route": "/d", "query_keys": []}])
    _, http_origin = _delta(agent, [], 1, [
        {"op": "add_state", "origin": "http://docs.example.com", "route": "/e", "query_keys": []}])
    forged_author = dict(sign_payload(stranger, {**root_payload}))  # signer != author

    return {
        "delta_type": GRAPH_DELTA_TYPE,
        "state_key_type": STATE_KEY_TYPE,
        "agent": {"seed_hex": agent_seed, "did": agent.did},
        "stranger": {"seed_hex": stranger_seed, "did": stranger.did},
        "state_keys": [
            {"origin": origin, "route": "/guide/install", "query_keys": ["lang"], "key": install},
            {"origin": origin, "route": "/account/delete", "query_keys": [], "key": delete},
        ],
        "valid": [
            {"name": "root", "wire": _text(root), "cid": root_cid, "payload": _text(root_payload)},
            {"name": "child", "wire": _text(child), "cid": child_cid, "payload": _text(child_payload)},
        ],
        "invalid": [
            {"name": "signature bit flipped", "wire": _text(bad_sig), "reason": "invalid signature"},
            {"name": "tombstone op", "wire": _text(tombstone), "reason": "unknown op"},
            {"name": "lamport not 1 + max(parents)", "wire": _text(wrong_lamport), "reason": "lamport"},
            {"name": "float lamport", "wire": _text(float_lamport), "reason": "lamport must be an integer"},
            {"name": "parents not sorted", "wire": _text(unsorted_parents), "reason": "parents"},
            {"name": "author not trusted", "wire": _text(untrusted), "reason": "untrusted author"},
            {"name": "plain http origin", "wire": _text(http_origin), "reason": "origin"},
            {"name": "signer is not the author", "wire": _text(forged_author), "reason": "signer is not the author"},
        ],
    }


# ---- Socratic instruction review (Gate 1, wording level) -------------------------

_SOCRATIC_INSTRUCTIONS = [
    "",
    "   \t",
    "Map the public docs on docs.example.com",
    "read-only: delete the temp files",
    "Delete the cache but don't modify anything",
    "只读查看，然后删除日志",
    "请delete文件，只读",
    "wrİte the file, read-only",
    "poll the inbox every 1 second",
    "poll the inbox every 3 seconds",
    "check every 3s",
    "every 10 sec",
    "every 500 ms",
    "every ٣ sec",
    "每 2 秒 刷新一次",
    "每500毫秒检查",
    "retry forever until it works",
    "poll\rforever",
    "一直轮询直到成功",
    "automatically post a reply on my behalf",
    "ſend automatically",
    "自动发布这条推文",
    "fill the form and click submit",
    "open it and then read it and then copy it and then paste it and then save it and then close it "
    "and then reopen it and then check it",
    "x" * 601,
    "中" * 600,
    "read-only, and then post the summary automatically, every 2 seconds",
]


def socratic_vectors() -> dict:
    cases = []
    for text in _SOCRATIC_INSTRUCTIONS:
        r = socratic.review(text)
        cases.append({"instruction": text, "verdict": r.verdict, "reasons": list(r.reasons),
                      "suggestion": r.suggestion})
    return {"cases": cases}


# ---- browser link v2: crypto_kx, records, handshake, packets -------------------------

_V2_AT_MS = 1759740000250  # the handshake's timestamp


def _blake2b_cases() -> list[dict]:
    lengths = [0, 1, 63, 64, 95, 96, 97, 127, 128, 129, 255, 256, 257]
    out = []
    for n in lengths:
        data = (hashlib.sha512(f"secdogie/vectors/blake2b/{n}".encode()).digest() * 5)[:n]
        out.append({"input_hex": data.hex(), "digest_hex": hashlib.blake2b(data, digest_size=64).hexdigest()})
    for i in range(3):  # the crypto_kx shape: q || client_pk || server_pk
        data = (hashlib.sha512(f"secdogie/vectors/blake2b/kx/{i}".encode()).digest() * 2)[:96]
        out.append({"input_hex": data.hex(), "digest_hex": hashlib.blake2b(data, digest_size=64).hexdigest()})
    return out


def symbiont_link_vectors() -> dict:
    import nacl.bindings as nb
    from secdogie_dialogue import symbiont_packets as sp
    from secdogie_identity import linkauth as la
    from secdogie_transport.linkcipher import MAX_PLAINTEXT, MAX_RECORD, KxKeypair, RecordCipher, SessionKeys

    app, app_seed = _identity("app")
    node, node_seed = _identity("node")
    other, _ = _identity("operator")
    at = float(_LINK_AT)

    # -- crypto_kx from fixed seeds (public test keys)
    cpk, csk = nb.crypto_kx_seed_keypair(_seed("kx/client"))
    spk, ssk = nb.crypto_kx_seed_keypair(_seed("kx/server"))
    crx, ctx = nb.crypto_kx_client_session_keys(cpk, csk, spk)
    kx = {"client_secret_hex": csk.hex(), "client_public_hex": cpk.hex(), "server_secret_hex": ssk.hex(),
          "server_public_hex": spk.hex(), "client_rx_hex": crx.hex(), "client_tx_hex": ctx.hex(),
          # peer keys libsodium refuses: the shared secret would be all zero
          "refused_peer_public_hex": ["00" * 32, "01" + "00" * 31]}

    # -- the handshake, after W1 on a browser <-> aiortc link
    browser_fps = [_fp("sha-256", "browser")]
    node_fps = [_fp("sha-256", "node"), _fp("sha-384", "node"), _fp("sha-512", "node")]
    room = "vectors-room-1"
    node_binding = la.create_link_binding(node, room=room, local=node_fps, remote=browser_fps, issued_at=at)
    page_binding = la.create_link_binding(app, room=room, local=browser_fps, remote=node_fps[:1], issued_at=at)
    link = la.link_digest(node_binding, page_binding)
    init = la.create_handshake_init(app, client_pubkey=cpk, link=link, lang="zh", timestamp_ms=_V2_AT_MS)
    accept = la.create_handshake_accept(node, server_pubkey=spk, client_pubkey=cpk, link=link,
                                        timestamp_ms=_V2_AT_MS)

    def resign(who, obj, **changes):
        body = {k: v for k, v in obj.items() if k not in ("signer", "sig")}
        return sign_payload(who, {**body, **changes})

    init_rejects = [
        ("signed by another key", resign(other, init, did=other.did)),
        ("another link", resign(app, init, link="0" * 64)),
        ("stale", resign(app, init, timestamp=_V2_AT_MS - 120_001)),
        ("future", resign(app, init, timestamp=_V2_AT_MS + 120_001)),
        ("a changed field", {**init, "lang": "en"}),
        ("an extra field", {**init, "extra": 1}),
        ("another version", resign(app, init, v="secdogie/symbiont-link/v1")),
        ("an unknown language", resign(app, init, lang="fr")),
        ("an upper-case key", resign(app, init, client_pubkey=cpk.hex().upper())),
        ("a float timestamp", resign(app, init, timestamp=float(_V2_AT_MS))),
        ("an accept", accept),
    ]
    accept_rejects = [
        ("signed by the app", resign(app, accept, did=app.did)),
        ("answers another init", resign(node, accept, client_pubkey=spk.hex())),
        ("another link", resign(node, accept, link="f" * 64)),
        ("stale", resign(node, accept, timestamp=_V2_AT_MS + 120_001)),
        ("a missing field", {k: v for k, v in accept.items() if k != "server_pubkey"}),
    ]

    def reason_init(obj):
        return la.verify_handshake_init(obj, app_did=app.did, link=link, now_ms=_V2_AT_MS).reason

    def reason_accept(obj):
        return la.verify_handshake_accept(obj, node_did=node.did, client_pubkey=cpk, link=link,
                                          now_ms=_V2_AT_MS).reason

    assert la.verify_handshake_init(init, app_did=app.did, link=link, now_ms=_V2_AT_MS).ok
    assert la.verify_handshake_accept(accept, node_did=node.did, client_pubkey=cpk, link=link,
                                      now_ms=_V2_AT_MS).ok
    handshake = {
        "node": {"did": node.did, "seed_hex": node_seed}, "app": {"did": app.did, "seed_hex": app_seed},
        "node_binding": _text(node_binding), "page_binding": _text(page_binding), "link": link,
        "now_ms": _V2_AT_MS, "init": _text(init), "accept": _text(accept),
        "init_rejects": [{"name": n, "text": _text(o), "reason": reason_init(o)} for n, o in init_rejects],
        "accept_rejects": [{"name": n, "text": _text(o), "reason": reason_accept(o)} for n, o in accept_rejects],
    }
    assert all(r["reason"] for r in handshake["init_rejects"] + handshake["accept_rejects"])

    # -- records under those keys, on that link
    def ciphers():
        page = RecordCipher(SessionKeys.derive("client", KxKeypair.from_secret(csk), spk), link=link)
        nodec = RecordCipher(SessionKeys.derive("server", KxKeypair.from_secret(ssk), cpk), link=link)
        return page, nodec

    page_c, node_c = ciphers()
    to_node = [b"", "把旧账号注销掉".encode(), b"x" * 1000]
    to_page = [b"{}", bytes(range(256))]
    page_records = [page_c.seal(m) for m in to_node]
    node_records = [node_c.seal(m) for m in to_page]
    big = page_c.seal(b"y" * MAX_PLAINTEXT)
    assert len(big) == MAX_RECORD
    r0 = page_records[0]
    rejects = [
        ("a replay", [page_records[0], page_records[0]]),
        ("a gap", [page_records[1]]),
        ("a flipped tag bit", [r0[:-1] + bytes([r0[-1] ^ 1])]),
        ("a flipped header bit", [r0[:8] + bytes([r0[8] ^ 1]) + r0[9:]]),
        ("not a record", [b"T" + r0[1:]]),
        ("too short", [r0[:24]]),
        ("a record from the other direction", [node_records[0]]),
    ]
    for name, seq in rejects:
        _, rx = ciphers()
        for i, rec in enumerate(seq):
            try:
                rx.open(rec)
            except Exception:
                assert i == len(seq) - 1, name
                break
        else:
            raise AssertionError(f"record reject case accepted: {name}")
    records = {
        "max_plaintext": MAX_PLAINTEXT, "max_record": MAX_RECORD,
        "page_to_node": [{"plaintext_hex": m.hex(), "record_hex": r.hex()} for m, r in zip(to_node, page_records, strict=True)],
        "node_to_page": [{"plaintext_hex": m.hex(), "record_hex": r.hex()} for m, r in zip(to_page, node_records, strict=True)],
        "largest": {"seq": len(to_node), "record_sha256": _sha(big)},
        "rejects": [{"name": n, "records_hex": [r.hex() for r in seq]} for n, seq in rejects],
    }

    # -- packets
    token = create_authorization(_identity("operator")[0], TargetAction("key", text="delete", high_risk=True),
                                 node.did, valid_from=at, expires_at=at + 120)
    action = {"kind": "key", "target_id": "", "target_role": "", "target_name": "", "text": "delete",
              "high_risk": True}
    good = [
        ("add goal", {"type": "ADD_GOAL", "goal_id": "g-0123456789ab", "intent": "把旧账号注销掉 — close it ✅"}),
        ("add goal at the limit", {"type": "ADD_GOAL", "goal_id": "g-1", "intent": "😀" * 2000}),
        ("a chip", {"type": "GATE_1_REPLY", "probe_id": "0a1b2c3d4e5f6a7b", "candidate_id": "approve"}),
        ("typed answer", {"type": "GATE_1_REPLY", "probe_id": "p-1", "text": "Downloads\n下载"}),
        ("approve", {"type": "GATE_2_REPLY", "challenge_id": "c-1", "decision": "approve", "authorization": token}),
        ("cancel", {"type": "GATE_2_REPLY", "challenge_id": "c-1", "decision": "cancel"}),
        ("stop", {"type": "STOP", "goal_id": "g-1"}),
        ("idle", {"type": "CURRENT_STATUS", "status": "idle", "goal_id": ""}),
        ("waiting", {"type": "CURRENT_STATUS", "status": "waiting", "goal_id": "g-1"}),
        ("goal done", {"type": "GOAL_UPDATE", "goal_id": "g-1", "state": "done"}),
        ("step probe", {"type": "GATE_1_PROBE", "probe_id": "p-1", "prompt": "", "about": "step",
                        "candidates": [{"id": "approve", "label": "Approve"}, {"id": "deny", "label": "Deny"}],
                        "step": {"kind": "hotkey", "keys": ["ctrl", "s"]}, "goal_id": "g-1",
                        "expires_at": at + 300}),
        ("question probe at the limits",
         {"type": "GATE_1_PROBE", "probe_id": "p-2", "prompt": "问" * 500, "about": "question",
          "candidates": [{"id": f"c{i}", "label": "选" * 80} for i in range(8)], "expires_at": 1759740300}),
        ("challenge", {"type": "GATE_2_CHALLENGE", "challenge_id": "c-1",
                       "emotional_memory": "这是你今天 10:42 交代的事：『把旧账号注销掉』", "target_summary": "key",
                       "action_type": sp.DESTRUCTIVE_UNBIND, "action": action, "action_hash": action_hash(
                           TargetAction("key", text="delete", high_risk=True)),
                       "subject": node.did, "expires_at": at + 120, "goal_id": "g-1"}),
        ("challenge with a 160-code-point memory",
         {"type": "GATE_2_CHALLENGE", "challenge_id": "c-2", "emotional_memory": "🙂" * 160, "target_summary": "",
          "action_type": "SOMETHING_NEW", "action": action, "action_hash": "0" * 64, "subject": node.did,
          "expires_at": at}),
        ("closed", {"type": "GATE_CLOSED", "kind": "probe", "id": "p-1", "reason": "answered"}),
    ]
    probe = good[10][1]
    challenge = good[12][1]
    bad = [
        ("unknown type", {"type": "HELLO"}),
        ("missing field", {"type": "ADD_GOAL", "goal_id": "g-1"}),
        ("extra field", {"type": "STOP", "goal_id": "g-1", "why": "x"}),
        ("empty intent", {"type": "ADD_GOAL", "goal_id": "g-1", "intent": ""}),
        ("intent too long", {"type": "ADD_GOAL", "goal_id": "g-1", "intent": "😀" * 2001}),
        ("upper-case id", {"type": "STOP", "goal_id": "G-1"}),
        ("id too long", {"type": "STOP", "goal_id": "g" * 41}),
        ("both answers", {"type": "GATE_1_REPLY", "probe_id": "p-1", "candidate_id": "c0", "text": "x"}),
        ("no answer", {"type": "GATE_1_REPLY", "probe_id": "p-1"}),
        ("approve without a token", {"type": "GATE_2_REPLY", "challenge_id": "c-1", "decision": "approve"}),
        ("cancel with a token", {"type": "GATE_2_REPLY", "challenge_id": "c-1", "decision": "cancel",
                                 "authorization": token}),
        ("a token with an extra field", {"type": "GATE_2_REPLY", "challenge_id": "c-1", "decision": "approve",
                                         "authorization": {**token, "x": 1}}),
        ("a token of another type", {"type": "GATE_2_REPLY", "challenge_id": "c-1", "decision": "approve",
                                     "authorization": {**token, "type": "secdogie/other/v1"}}),
        ("a boolean time", {"type": "GATE_2_REPLY", "challenge_id": "c-1", "decision": "approve",
                            "authorization": {**token, "expires_at": True}}),
        ("running without a goal", {"type": "CURRENT_STATUS", "status": "running", "goal_id": ""}),
        ("unknown state", {"type": "GOAL_UPDATE", "goal_id": "g-1", "state": "exploded"}),
        ("a confirmation without approve and deny", {**probe, "candidates": [{"id": "c0", "label": "OK"}]}),
        ("a step on a question", {**good[11][1], "step": {"kind": "key"}}),
        ("a repeated candidate", {**good[11][1], "candidates": [{"id": "c0", "label": "a"},
                                                               {"id": "c0", "label": "b"}]}),
        ("nine candidates", {**good[11][1], "candidates": [{"id": f"c{i}", "label": "x"} for i in range(9)]}),
        ("a label too long", {**good[11][1], "candidates": [{"id": "c0", "label": "选" * 81}]}),
        ("a label with a newline", {**good[11][1], "candidates": [{"id": "c0", "label": "a\nb"}]}),
        ("a prompt too long", {**good[11][1], "prompt": "问" * 501}),
        ("an upper-case step kind", {**probe, "step": {"kind": "Key"}}),
        ("an unknown about", {**probe, "about": "whatever"}),
        ("a memory too long", {**challenge, "emotional_memory": "🙂" * 161}),
        ("a memory on two lines", {**challenge, "emotional_memory": "a\nb"}),
        ("a memory with a tab", {**challenge, "emotional_memory": "a\tb"}),
        ("a lower-case action type", {**challenge, "action_type": "destructive"}),
        ("a string high_risk", {**challenge, "action": {**action, "high_risk": "yes"}}),
        ("an action without a role", {**challenge, "action": {k: v for k, v in action.items()
                                                             if k != "target_role"}}),
        ("an upper-case hash", {**challenge, "action_hash": "A" * 64}),
        ("a float as a string", {**challenge, "expires_at": "1759740120.25"}),
        ("unknown close reason", {"type": "GATE_CLOSED", "kind": "probe", "id": "p-1", "reason": "withdrawn"}),
    ]
    bad_out = []
    for name, pkt in bad:
        try:
            sp.validate(pkt)
        except sp.PacketError as e:
            bad_out.append({"name": name, "text": _text(pkt), "path": e.path, "rule": e.rule})
        else:
            raise AssertionError(f"packet reject case accepted: {name}")
    for _, pkt in good:
        sp.decode(canonical(pkt))
    not_canonical = [
        ("whitespace", " " + _text(good[6][1])),
        ("unsorted keys", '{"type":"STOP","goal_id":"g-1"}'),
        ("duplicate key", '{"goal_id":"g-1","goal_id":"g-2","type":"STOP"}'),
        ("NaN", '{"expires_at":NaN,"id":"p-1","kind":"probe","reason":"answered","type":"GATE_CLOSED"}'),
        ("an escaped character that canonical writes raw",
         '{"goal_id":"g-1","intent":"\\u00e9","type":"ADD_GOAL"}'),
        ("a lone surrogate", '{"goal_id":"g-1","intent":"\\ud800","type":"ADD_GOAL"}'),
    ]
    for name, text in not_canonical:
        try:
            sp.decode(text.encode("utf-8"))
        except sp.PacketError:
            continue
        raise AssertionError(f"non-canonical packet accepted: {name}")
    packets = {
        "limits": dict(sp.LIMITS),
        "max_packet_bytes": sp.MAX_PACKET_BYTES,
        "good": [{"name": n, "text": _text(p)} for n, p in good],
        "bad": bad_out,
        "not_canonical": [{"name": n, "text": t} for n, t in not_canonical],
    }
    return {
        "spec": "secdogie/symbiont-link/v2: W1, then a signed crypto_kx handshake, then AES-256-GCM records "
                "of canonical JSON packets. See transport/secdogie_transport/linkcipher.py, "
                "identity/secdogie_identity/linkauth.py and dialogue/secdogie_dialogue/symbiont_packets.py.",
        "blake2b_512": _blake2b_cases(),
        "kx": kx,
        "handshake": handshake,
        "records": records,
        "packets": packets,
    }


# ---- files ----------------------------------------------------------------------


# ---- browser link: frames, mux, session, dialogue/v1, W1, pairing ---------------------

_LINK_AT = "1759740000.25"           # issued_at of every link statement
_LINK_TS_NS = 1759740000250000000    # dialogue headers
_PAIR_EXPIRES = 1759740600
_FRAME_CTR = 1759740000250000001     # a frame counter starts at time.time_ns()


def _fp(alg: str, label: str) -> str:
    n = {"sha-256": 32, "sha-384": 48, "sha-512": 64}[alg]
    raw = hashlib.sha512(f"secdogie/vectors/cert/{label}".encode()).digest()
    raw = (raw * 2)[:n]
    return f"{alg} " + ":".join(f"{b:02X}" for b in raw)


def _sdp(kind: str, fps: list[str], *, media: str) -> str:
    """A data-channel SDP in the shape the named stack writes (fingerprints
    lower-case in the text, as browsers write them; the parser normalizes)."""
    lines = ["v=0", "o=- 4611731400430051336 2 IN IP4 127.0.0.1", "s=-", "t=0 0",
             "a=group:BUNDLE 0", "a=msid-semantic: WMS" if kind == "browser" else "a=ice-lite", media,
             "c=IN IP4 0.0.0.0", "a=ice-ufrag:abcd", "a=ice-pwd:0123456789abcdef0123456789",
             *[f"a=fingerprint:{fp.split(' ')[0]} {fp.split(' ')[1].lower()}" for fp in fps],
             "a=setup:actpass", "a=mid:0", "a=sctp-port:5000", "a=max-message-size:262144"]
    return "\r\n".join(lines) + "\r\n"


def link_vectors() -> dict:
    from secdogie_dialogue.dialogue import system_status
    from secdogie_dialogue.protocol import (
        ControlOp,
        ControlPacket,
        DialoguePacket,
        DialogueType,
        MemoryCandidatePacket,
        NodeDelta,
        NodeOp,
        SessionEvent,
        SessionPacket,
        StateSnapshotPacket,
    )
    from secdogie_dialogue.session import CHANNEL, encode_envelope, fragments
    from secdogie_identity import linkauth as la
    from secdogie_transport import mux
    from secdogie_transport.sealed import ReplayWindow
    from secdogie_transport.udp import decode_frame, encode_frame

    app, app_seed = _identity("app")
    operator, operator_seed = _identity("operator")
    node, node_seed = _identity("node")
    at = float(_LINK_AT)

    # -- mux and session framing
    mux_cases = [{"channel": c, "payload_hex": p.hex(), "wire_hex": mux.encode(c, p).hex()}
                 for c, p in ((CHANNEL, b"D\x00"), ("graph/v1", b""), ("a", bytes(range(5))))]
    data = ("x" * 100 + "中文").encode("utf-8")
    msg_id = 2**62 - 5  # past 2**53: a JS number cannot hold it
    session = {
        "fragment_size": 64, "msg_id": str(msg_id), "reliable": True, "data_hex": data.hex(),
        "frames_hex": [f.hex() for f in fragments(msg_id, data, reliable=True, size=64)],
        "unreliable_frame_hex": fragments(7, b"hb", reliable=False)[0].hex(),
        "ack_hex": (b"A" + msg_id.to_bytes(8, "big")).hex(),
    }

    # -- dialogue/v1 packets the browser sends and receives
    def env(sender, recipient, seq, packet, session_id):
        header = Header(PROTOCOL_VERSION, sender.did, recipient.did, session_id, seq, _LINK_TS_NS + seq)
        e = seal(sender, header, packet)
        return {"canonical": _text(e), "wire": encode_envelope(e).decode("ascii")}

    app_out = [
        ("hello", SessionPacket(SessionEvent.HELLO)),
        ("add_goal", ControlPacket("r-0001", ControlOp.ADD_GOAL, goal_id="g-0001",
                                   title="把旧账号注销掉 — close the old account ✅")),
        ("stop", ControlPacket("r-0002", ControlOp.STOP, goal_id="g-0001")),
        ("clarification", DialoguePacket("q-reply-1", DialogueType.USER_CLARIFICATION, "Downloads",
                                         in_reply_to="p-0001")),
        ("bye", SessionPacket(SessionEvent.BYE)),
    ]
    node_out = [
        ("status_running", system_status("status: running", about="g-0001")),
        ("status_waiting", system_status("status: waiting for operator", about="g-0001")),
        ("status_idle", system_status("status: idle")),
        ("accepted", DialoguePacket("st-0002", DialogueType.SYSTEM_STATUS, "accepted: goal g-0001 queued",
                                    in_reply_to="r-0001")),
        ("question", DialoguePacket("p-0001", DialogueType.SOCRATIC_QUESTION, "Which folder should old files go to?",
                                    suggested_options=("Downloads", "Desktop"), gate_finding="intent-unproven")),
        ("memory_candidate", MemoryCandidatePacket("ab" * 32, "fact", "global", "report-folder",
                                                   "reports go to ~/Reports", "remember")),
        ("snapshot", StateSnapshotPacket(42, 7, 1, (NodeDelta(NodeOp.ADD, 0, role="AXWindow", name="Mail"),),
                                         full=True)),
    ]
    envelopes = []
    for i, (name, pkt) in enumerate(app_out, 1):
        envelopes.append({"name": name, "from": "app", "seq": i, "session_id": "s-app-0001",
                          **env(app, node, i, pkt, "s-app-0001")})
    for i, (name, pkt) in enumerate(node_out, 1):
        if name.startswith("status") or name == "accepted":
            pkt = DialoguePacket(f"st-{i:04d}", pkt.dialogue_type, pkt.content, in_reply_to=pkt.in_reply_to)
        envelopes.append({"name": name, "from": "node", "seq": i, "session_id": "s-node-0001",
                          **env(node, app, i, pkt, "s-node-0001")})

    # -- direct/v1 frames (one per data-channel message)
    message = mux.encode(CHANNEL, fragments(9, b'{"x":1}', reliable=True)[0])
    frames = []
    for name, sender, recipient in (("app_to_node", app, node), ("node_to_app", node, app)):
        raw = encode_frame(sender, recipient.did, message, _FRAME_CTR)
        obj = json.loads(raw)
        frames.append({
            "name": name, "ctr": str(_FRAME_CTR), "message_hex": message.hex(),
            "signed_body": _text({k: v for k, v in obj.items() if k not in ("signer", "sig")}),
            "sig": obj["sig"], "wire": raw.decode("ascii"),
        })
        assert decode_frame(raw, _trust(sender), recipient.did) is not None
    window = ReplayWindow(8)
    steps = [{"ctr": c, "accept": window.accept(c)} for c in (100, 100, 99, 105, 98, 97, 92, 104, 113, 105, 106)]

    # -- W1 and pairing, for each SDP shape a browser and aiortc produce
    browser_fps = [_fp("sha-256", "browser")]
    node_fps = [_fp("sha-256", "node"), _fp("sha-384", "node"), _fp("sha-512", "node")]
    dc = "m=application 9 UDP/DTLS/SCTP webrtc-datachannel"
    sdp = {
        "browser_local": _sdp("browser", browser_fps, media=dc),
        "aiortc_local": _sdp("aiortc", node_fps, media="m=application 9 DTLS/SCTP 5000"),
        "aiortc_as_browser_keeps_sha256": _sdp("browser", node_fps[:1], media=dc),
        "aiortc_as_browser_keeps_sha512": _sdp("browser", node_fps[2:], media=dc),
        "with_video": _sdp("browser", browser_fps, media=dc) + "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n",
    }
    parsed = {k: list(la.fingerprints_from_sdp(v)) for k, v in sdp.items()}
    room = la.derive_room(node)
    shapes = []
    for shape in ("aiortc_as_browser_keeps_sha256", "aiortc_as_browser_keeps_sha512"):
        page_remote = parsed[shape]
        node_b = la.create_link_binding(node, room=room, local=node_fps, remote=browser_fps, issued_at=at)
        page_b = la.create_link_binding(app, room=room, local=browser_fps, remote=page_remote, issued_at=at)
        assert la.verify_link_binding(node_b, trust=_trust(node), room=room, observed_local=browser_fps,
                                      observed_remote=page_remote, now=at).ok
        assert la.verify_link_binding(page_b, trust=_trust(app), room=room, observed_local=node_fps,
                                      observed_remote=browser_fps, now=at).ok
        shapes.append({"name": shape, "page_local": browser_fps, "page_remote": page_remote,
                       "node_local": node_fps, "node_remote": browser_fps,
                       "node_binding": _text(node_b), "page_binding": _text(page_b)})
    mitm = [_fp("sha-256", "mitm")]
    w1_rejects = [
        {"name": "relay certificate", "page_local": browser_fps, "page_remote": mitm,
         "reason": "the peer's certificate is not the one it signed for"},
        {"name": "injected beside the real one", "page_local": browser_fps, "page_remote": node_fps[:1] + mitm,
         "reason": "the peer's certificate is not the one it signed for"},
        {"name": "the node saw someone else", "page_local": mitm, "page_remote": node_fps[:1],
         "reason": "the peer saw a certificate that is not ours"},
    ]

    secret = _seed("pairing-secret")
    pid = la.pairing_id(secret)
    page_remote = parsed["aiortc_as_browser_keeps_sha512"]
    hello = la.create_pair_hello(app, secret=secret, node_did=node.did, local=browser_fps, remote=page_remote,
                                 operator_did=operator.did, issued_at=at)
    proof = la.create_operator_proof(operator, app_did=app.did, node_did=node.did, pairing=pid, issued_at=at + 5)
    confirm = la.create_pair_confirm(app, secret=secret, node_did=node.did, hello=hello, operator_proof=proof,
                                     issued_at=at + 5)
    paired = la.create_paired(node, app_did=app.did, operator_did=operator.did, room=room, pairing=pid,
                              local=node_fps, remote=browser_fps, issued_at=at + 6)
    refusals = {r: _text(la.create_refusal(node, reason=r, local=node_fps, remote=browser_fps, pairing=pid,
                                           issued_at=at + 6))
                for r in sorted(la.REFUSAL_REASONS)}
    pairing = {
        "secret_b64url": la.b64url(secret),
        "pairing_id": pid,
        "pairing_room": la.pairing_room(secret),
        "expires_at": _PAIR_EXPIRES,
        "fragment": la.pairing_fragment(node_did=node.did, secret=secret, expires_at=_PAIR_EXPIRES),
        "page_local": browser_fps, "page_remote": page_remote, "node_local": node_fps, "node_remote": browser_fps,
        "hello_core_mac_input": _text({k: v for k, v in hello.items() if k not in ("mac", "signer", "sig")}),
        "hello": _text(hello),
        "check_code": la.check_code(hello),
        "hello_hash": la.hello_hash(hello),
        "operator_proof": _text(proof),
        "confirm": _text(confirm),
        "paired": _text(paired),
        "refused": refusals,
    }

    from secdogie_citadel.action_gate import FINDING_KINDS

    return {
        "finding_kinds": list(FINDING_KINDS),
        "app": {"seed_hex": app_seed, "did": app.did},
        "operator": {"seed_hex": operator_seed, "did": operator.did},
        "node": {"seed_hex": node_seed, "did": node.did},
        "issued_at": _LINK_AT,
        "room": {"epoch": 0, "room": room, "epoch1": la.derive_room(node, 1)},
        "mux": {"cases": mux_cases, "reject_hex": ["", "05616263", "0261e9", "0120"]},
        "session": session,
        "envelopes": envelopes,
        "frames": frames,
        "replay_window": {"size": 8, "steps": steps},
        "sdp": {"texts": sdp, "fingerprints": {k: v for k, v in parsed.items()}},
        "w1": {"room": room, "shapes": shapes, "rejects": w1_rejects},
        "pairing": pairing,
    }


def _trust(*ids):
    from secdogie_identity import Allowlist

    return Allowlist({i.did for i in ids})


def build_all() -> dict[str, str]:
    files = {
        "canonical.json": canonical_vectors(),
        "gate2.json": gate2_vectors(),
        "graph_delta.json": graph_delta_vectors(),
        "link.json": link_vectors(),
        "socratic.json": socratic_vectors(),
        "symbiont_link.json": symbiont_link_vectors(),
    }
    return {name: json.dumps(obj, indent=2, ensure_ascii=False) + "\n" for name, obj in files.items()}


def main(argv: list[str]) -> int:
    check = "--check" in argv
    stale = []
    for name, text in build_all().items():
        path = HERE / name
        if check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(name)
        else:
            path.write_text(text, encoding="utf-8")
    if stale:
        print("stale vector files (run fixtures/vectors/generate.py): " + ", ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
