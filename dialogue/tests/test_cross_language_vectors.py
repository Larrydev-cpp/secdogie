"""The golden vectors in fixtures/vectors/ are what the Rust (graph/) and
TypeScript (symbiont/) implementations must reproduce byte for byte. They are
generated from the production Python functions; this test regenerates them and
fails if the committed files drifted -- so a change to canonical JSON, the
authorization token, the dialogue envelope or the Socratic rules cannot land
without the other languages' vectors moving with it.

It also checks the vectors from the Python side: the stored token and the
sealed challenge verify with the real verifiers."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from secdogie_citadel.authz import verify_authorization
from secdogie_dialogue.protocol import (
    Gate2ChallengePacket,
    PacketKind,
    ReplayGuard,
    TargetAction,
    open_envelope,
)
from secdogie_identity import Allowlist

VECTORS = Path(__file__).resolve().parents[2] / "fixtures" / "vectors"


def _generator():
    spec = importlib.util.spec_from_file_location("secdogie_vectors_generate", VECTORS / "generate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_committed_vectors_match_the_python_reference():
    for name, text in _generator().build_all().items():
        committed = (VECTORS / name).read_text(encoding="utf-8")
        assert committed == text, f"{name} is stale: run python fixtures/vectors/generate.py"


def _load(name: str) -> dict:
    return json.loads((VECTORS / name).read_text(encoding="utf-8"))


def test_the_stored_token_verifies_with_the_python_verifier():
    g = _load("gate2.json")
    action = TargetAction(**g["actions"][g["token"]["action_index"]]["action"])
    token = json.loads(g["token"]["wire"])
    res = verify_authorization(token, action, operators=Allowlist({g["operator"]["did"]}),
                               subject=g["node"]["did"], now=float(g["token"]["valid_from"]) + 1)
    assert res.ok, res.reason


def test_the_sealed_challenge_opens_with_the_python_receiver():
    g = _load("gate2.json")
    env = json.loads(g["challenge"]["wire"])
    clock_ns = env["header"]["timestamp_ns"]
    opened = open_envelope(env, trust=Allowlist({g["node"]["did"]}), self_did=g["operator"]["did"],
                           replay=ReplayGuard(clock_ns=lambda: clock_ns))
    assert opened.ok, opened.reason
    assert opened.envelope.kind is PacketKind.GATE2_CHALLENGE
    assert isinstance(opened.envelope.packet, Gate2ChallengePacket)
    assert opened.envelope.header.timestamp_ns > 2**53  # the case a JS number cannot hold


def test_the_canonical_vectors_round_trip_through_python():
    gen = _generator()
    for case in _load("canonical.json")["cases"]:
        assert gen.canonical(json.loads(case["input"])).decode("utf-8") == case["canonical"], case["name"]


# ---- link.json: the browser link, checked by the Python side ------------------------------


def test_link_frames_open_and_the_window_steps_hold():
    from secdogie_transport import mux
    from secdogie_transport.sealed import ReplayWindow
    from secdogie_transport.udp import decode_frame

    v = _load("link.json")
    dids = {"app": v["app"]["did"], "node": v["node"]["did"]}
    for f in v["frames"]:
        sender, recipient = f["name"].split("_to_")
        opened = decode_frame(f["wire"].encode(), Allowlist({dids[sender]}), dids[recipient])
        assert opened == (dids[sender], int(f["ctr"]), bytes.fromhex(f["message_hex"]))
    for case in v["mux"]["cases"]:
        assert mux.decode(bytes.fromhex(case["wire_hex"])) == (case["channel"], bytes.fromhex(case["payload_hex"]))
    for bad in v["mux"]["reject_hex"]:
        assert mux.decode(bytes.fromhex(bad)) is None
    w = ReplayWindow(v["replay_window"]["size"])
    assert [w.accept(s["ctr"]) for s in v["replay_window"]["steps"]] == [s["accept"] for s in v["replay_window"]["steps"]]


def test_link_envelopes_open_with_the_python_receiver():
    from secdogie_dialogue.session import DialogueSession

    v = _load("link.json")
    for e in v["envelopes"]:
        sender, recipient = ("app", "node") if e["from"] == "app" else ("node", "app")
        obj = json.loads(e["wire"])
        ts = obj["header"]["timestamp_ns"]
        opened = open_envelope(obj, trust=Allowlist({v[sender]["did"]}), self_did=v[recipient]["did"],
                               replay=ReplayGuard(clock_ns=lambda ts=ts: ts))
        assert opened.ok, (e["name"], opened.reason)
        assert json.loads(e["canonical"]) == obj
    # the session frames reassemble into the data
    s = v["session"]
    got = []
    from secdogie_identity import Identity

    sess = DialogueSession(Identity.generate(), v["app"]["did"], lambda b: True, trust=Allowlist(),
                           fragment_size=s["fragment_size"])
    sess._open = got.append
    for f in s["frames_hex"]:
        sess.receive(bytes.fromhex(f))
    assert got == [bytes.fromhex(s["data_hex"])]


def test_link_statements_verify_with_the_python_verifiers():
    from secdogie_identity import linkauth as la

    v = _load("link.json")
    node, app, op = v["node"]["did"], v["app"]["did"], v["operator"]["did"]
    at = float(v["issued_at"])
    for fp_name, text in v["sdp"]["texts"].items():
        assert list(la.fingerprints_from_sdp(text)) == v["sdp"]["fingerprints"][fp_name]
    assert la.sdp_is_data_only(v["sdp"]["texts"]["browser_local"])
    assert not la.sdp_is_data_only(v["sdp"]["texts"]["with_video"])
    room = v["w1"]["room"]
    for shape in v["w1"]["shapes"]:
        r = la.verify_link_binding(json.loads(shape["node_binding"]), trust=Allowlist({node}), room=room,
                                   observed_local=shape["page_local"], observed_remote=shape["page_remote"], now=at)
        assert r.ok, (shape["name"], r.reason)
        r = la.verify_link_binding(json.loads(shape["page_binding"]), trust=Allowlist({app}), room=room,
                                   observed_local=shape["node_local"], observed_remote=shape["node_remote"], now=at)
        assert r.ok, (shape["name"], r.reason)
    node_binding = json.loads(v["w1"]["shapes"][0]["node_binding"])
    for bad in v["w1"]["rejects"]:
        r = la.verify_link_binding(node_binding, trust=Allowlist({node}), room=room,
                                   observed_local=bad["page_local"], observed_remote=bad["page_remote"], now=at)
        assert not r.ok and r.reason == bad["reason"]

    p = v["pairing"]
    secret = la.b64url_decode(p["secret_b64url"])
    inv = la.parse_pairing_fragment(p["fragment"], now=at)
    assert (inv.node_did, inv.secret, inv.pairing_id, inv.room) == (node, secret, p["pairing_id"], p["pairing_room"])
    hello = json.loads(p["hello"])
    h = la.verify_pair_hello(hello, secret=secret, node_did=node, observed_local=p["node_local"],
                             observed_remote=p["node_remote"], now=at)
    assert h.ok and h.code == p["check_code"] and (h.app_did, h.operator_did) == (app, op)
    c = la.verify_pair_confirm(json.loads(p["confirm"]), secret=secret, node_did=node, hello=hello, now=at + 5)
    assert c.ok and c.operator_did == op
    r = la.verify_paired(json.loads(p["paired"]), node_did=node, app_did=app, pairing=p["pairing_id"],
                         observed_local=p["page_local"], observed_remote=p["page_remote"], now=at + 6)
    assert r.ok and r.room == v["room"]["room"]
    for reason, text in p["refused"].items():
        r = la.verify_refusal(json.loads(text), node_did=node, observed_local=p["page_local"],
                              observed_remote=p["page_remote"], now=at + 6)
        assert r.ok and r.refusal == reason
