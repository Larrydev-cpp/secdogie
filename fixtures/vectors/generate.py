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

    python fixtures/vectors/generate.py          # rewrite the four files
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


# ---- files ----------------------------------------------------------------------


def build_all() -> dict[str, str]:
    files = {
        "canonical.json": canonical_vectors(),
        "gate2.json": gate2_vectors(),
        "graph_delta.json": graph_delta_vectors(),
        "socratic.json": socratic_vectors(),
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
