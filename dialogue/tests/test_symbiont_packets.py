"""Packets of the browser link: exact field sets, limits in code points,
canonical bytes only, and errors that never quote the value."""
from __future__ import annotations

import pytest
from secdogie_dialogue import symbiont_packets as sp
from secdogie_identity import canonical

TOKEN = {"type": sp.AUTHORIZATION_TYPE, "action_hash": "a" * 64, "subject": "did:key:z6Mknode",
         "valid_from": 1760000000.5, "expires_at": 1760000120.0, "signer": "did:key:z6Mkop", "sig": "c2ln"}
ACTION = {"kind": "key", "target_id": "", "target_role": "", "target_name": "", "text": "delete",
          "high_risk": True}

GOOD = [
    {"type": "ADD_GOAL", "goal_id": "g-0123456789ab", "intent": "把旧账号注销掉"},
    {"type": "GATE_1_REPLY", "probe_id": "a1b2", "candidate_id": "approve"},
    {"type": "GATE_1_REPLY", "probe_id": "a1b2", "text": "Downloads"},
    {"type": "GATE_2_REPLY", "challenge_id": "c1", "decision": "approve", "authorization": TOKEN},
    {"type": "GATE_2_REPLY", "challenge_id": "c1", "decision": "cancel"},
    {"type": "STOP", "goal_id": "g-1"},
    {"type": "CURRENT_STATUS", "status": "idle", "goal_id": ""},
    {"type": "CURRENT_STATUS", "status": "waiting", "goal_id": "g-1"},
    {"type": "GOAL_UPDATE", "goal_id": "g-1", "state": "done"},
    {"type": "GATE_1_PROBE", "probe_id": "p1", "prompt": "", "about": "step", "expires_at": 1760000300.25,
     "candidates": [{"id": "approve", "label": "Approve"}, {"id": "deny", "label": "Deny"}],
     "step": {"kind": "hotkey", "keys": ["ctrl", "s"]}, "goal_id": "g-1"},
    {"type": "GATE_1_PROBE", "probe_id": "p2", "prompt": "Which folder?", "about": "question",
     "expires_at": 1760000300, "candidates": []},
    {"type": "GATE_2_CHALLENGE", "challenge_id": "c1", "emotional_memory": "这是你今天 10:42 交代的事：『把旧账号注销掉』",
     "target_summary": "key", "action_type": "DESTRUCTIVE_UNBIND", "action": ACTION, "action_hash": "b" * 64,
     "subject": "did:key:z6Mknode", "expires_at": 1760000120.0, "goal_id": "g-1"},
    {"type": "GATE_CLOSED", "kind": "challenge", "id": "c1", "reason": "expired"},
]


@pytest.mark.parametrize("pkt", GOOD, ids=lambda p: p["type"])
def test_good_packets_round_trip_canonically(pkt):
    data = sp.encode(pkt)
    assert data == canonical(pkt)
    assert sp.decode(data) == pkt


def _bad(pkt, path, rule=None):
    with pytest.raises(sp.PacketError) as e:
        sp.validate(pkt)
    assert e.value.path == path, e.value
    if rule:
        assert e.value.rule == rule


def test_field_sets_are_exact():
    _bad({"type": "ADD_GOAL", "goal_id": "g-1"}, "intent", "missing")
    _bad({"type": "ADD_GOAL", "goal_id": "g-1", "intent": "x", "extra": 1}, "extra", "unexpected field")
    _bad({"type": "NOPE"}, "type", "unknown packet type")
    _bad({"type": "GATE_1_REPLY", "probe_id": "p", "candidate_id": "c0", "text": "x"}, "candidate_id")
    _bad({"type": "GATE_1_REPLY", "probe_id": "p"}, "candidate_id")
    _bad({"type": "GATE_2_REPLY", "challenge_id": "c", "decision": "approve"}, "authorization", "missing")
    _bad({"type": "GATE_2_REPLY", "challenge_id": "c", "decision": "cancel", "authorization": TOKEN},
         "authorization", "unexpected field")
    _bad({"type": "GATE_2_REPLY", "challenge_id": "c", "decision": "approve",
          "authorization": {**TOKEN, "extra": 1}}, "authorization.extra")
    _bad({"type": "GATE_2_REPLY", "challenge_id": "c", "decision": "approve",
          "authorization": {**TOKEN, "valid_from": True}}, "authorization.valid_from", "not a number")
    _bad({"type": "GATE_2_REPLY", "challenge_id": "c", "decision": "maybe"}, "decision")
    _bad({"type": "CURRENT_STATUS", "status": "running", "goal_id": ""}, "goal_id", "not an id")


def test_limits_are_code_points():
    ok = {"type": "ADD_GOAL", "goal_id": "g-1", "intent": "😀" * 2000}  # 2000 code points, 4000 UTF-16 units
    assert sp.validate(ok)
    _bad({**ok, "intent": "😀" * 2001}, "intent")
    _bad({**ok, "intent": ""}, "intent")
    _bad({**ok, "goal_id": "G-1"}, "goal_id", "not an id")
    _bad({**ok, "goal_id": "g" * 41}, "goal_id", "not an id")


def test_probe_rules():
    probe = GOOD[9]
    _bad({**probe, "candidates": [{"id": "c0", "label": "x"}]}, "candidates",
         "a confirmation offers approve and deny")
    _bad({**GOOD[10], "step": {"kind": "key"}}, "step", "only for a step")
    _bad({**GOOD[10], "candidates": [{"id": "c0", "label": "a"}, {"id": "c0", "label": "b"}]},
         "candidates[1].id", "repeated")
    _bad({**GOOD[10], "candidates": [{"id": f"c{i}", "label": "x"} for i in range(9)]}, "candidates")
    _bad({**GOOD[10], "candidates": [{"id": "c0", "label": "line\nbreak"}]}, "candidates[0].label",
         "control characters")
    _bad({**probe, "step": {"kind": "Key"}}, "step.kind")
    _bad({**probe, "about": "other-thing"}, "about")


def test_challenge_rules():
    ch = GOOD[11]
    _bad({**ch, "emotional_memory": "x" * 161}, "emotional_memory")
    _bad({**ch, "emotional_memory": "two\nlines"}, "emotional_memory", "control characters")
    _bad({**ch, "action": {**ACTION, "high_risk": "yes"}}, "action.high_risk")
    _bad({**ch, "action": {k: v for k, v in ACTION.items() if k != "target_role"}}, "action.target_role")
    _bad({**ch, "action_type": "destructive"}, "action_type")
    _bad({**ch, "action_hash": "B" * 64}, "action_hash")
    assert sp.validate({**ch, "action_type": "SOMETHING_ELSE"})  # rendered, but only 再想想 is offered


def test_decode_refuses_non_canonical_duplicate_keys_and_nan():
    pkt = GOOD[0]
    data = canonical(pkt)
    with pytest.raises(sp.PacketError, match="not canonical"):
        sp.decode(b" " + data)
    with pytest.raises(sp.PacketError, match="not canonical"):
        sp.decode(data.replace(b'"type"', b'"type" '))
    with pytest.raises(sp.PacketError, match="duplicate key"):
        sp.decode(b'{"goal_id":"g-1","goal_id":"g-2","intent":"x","type":"ADD_GOAL"}')
    with pytest.raises(sp.PacketError, match="not JSON"):
        sp.decode(b'{"expires_at":NaN}')
    with pytest.raises(sp.PacketError, match="not JSON"):
        sp.decode(b"\xff\xfe")
    with pytest.raises(sp.PacketError, match="not canonical"):
        sp.decode(b'{"goal_id":"g-1","intent":"\\ud800","type":"ADD_GOAL"}')
    with pytest.raises(sp.PacketError, match="too large"):
        sp.decode(b"x" * (sp.MAX_PACKET_BYTES + 1))


def test_direction_and_errors_never_quote_values():
    with pytest.raises(sp.PacketError, match="wrong direction"):
        sp.decode(canonical(GOOD[0]), direction="to-page")
    with pytest.raises(sp.PacketError) as e:
        sp.validate({"type": "ADD_GOAL", "goal_id": "g-1", "intent": "my secret words" * 200})
    assert "secret" not in str(e.value)
