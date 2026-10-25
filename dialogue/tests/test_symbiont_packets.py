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
REFLECTION = {
    "recall": [{"kind": "said", "quote": "用这个号给阿杰发生日祝福", "at_ms": 1500000000000},
               {"kind": "did", "count": 2, "last_at_ms": 1700003600000},
               {"kind": "saw", "fact": "items", "value": 312, "at_ms": 1759740000000}],
    "why_now": {"quote": "帮我把那个旧账号注销掉", "at_ms": 1759739990000},
    "questions": ["motive", "keep", "later"],
    "paths": ["export", "deactivate", "snooze"],
}

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
    {"type": "GATE_2_CHALLENGE", "challenge_id": "c1", "emotional_memory": "它陪了你 9 年。你曾让它：『用这个号给阿杰发生日祝福』",
     "target_summary": "key", "action_type": "DESTRUCTIVE_UNBIND", "action": ACTION, "action_hash": "b" * 64,
     "subject": "did:key:z6Mknode", "expires_at": 1760000120.0, "goal_id": "g-1", "reflection": REFLECTION},
    {"type": "GATE_CLOSED", "kind": "challenge", "id": "c1", "reason": "expired"},
    {"type": "GATE_2_REPLY", "challenge_id": "c1", "decision": "alternative", "path": "snooze"},
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


def _refl(**changes):
    return {**GOOD[11], "reflection": {**REFLECTION, **changes}}


def test_no_challenge_skips_the_asking():
    _bad({k: v for k, v in GOOD[11].items() if k != "reflection"}, "reflection", "missing")
    _bad(_refl(questions=["keep", "motive", "later"]), "reflection.questions",
         "opens with the motive and closes with later")
    _bad(_refl(questions=["motive", "keep"]), "reflection.questions", "opens with the motive and closes with later")
    _bad(_refl(questions=["motive"]), "reflection.questions", "not a short list")
    _bad(_refl(questions=["motive", "motive", "later"]), "reflection.questions", "repeated")
    _bad(_refl(paths=["export", "deactivate"]), "reflection.paths", "always offers not deciding today")
    _bad(_refl(paths=["snooze", "approve"]), "reflection.paths[1]", "not an allowed value")
    _bad(_refl(arm_after_ms=0), "reflection.arm_after_ms", "unexpected field")  # nothing in it can arm anything


def test_memories_are_typed_bounded_and_quote_only_trusted_words():
    _bad(_refl(recall=REFLECTION["recall"] * 3), "reflection.recall", "not a short list")
    _bad(_refl(recall=[{"kind": "said", "quote": "🙂" * 121, "at_ms": 1}]), "reflection.recall[0].quote")
    assert sp.validate(_refl(recall=[{"kind": "said", "quote": "🙂" * 120, "at_ms": 1}]))
    _bad(_refl(recall=[{"kind": "confirmed", "quote": "a\nb"}]), "reflection.recall[0].quote", "control characters")
    _bad(_refl(recall=[{"kind": "said", "quote": "x", "at_ms": 1.5}]), "reflection.recall[0].at_ms")
    _bad(_refl(recall=[{"kind": "said", "quote": "x", "at_ms": True}]), "reflection.recall[0].at_ms")
    _bad(_refl(recall=[{"kind": "said", "quote": "x", "at_ms": 2**53}]), "reflection.recall[0].at_ms")
    _bad(_refl(recall=[{"kind": "did", "count": 0, "last_at_ms": 1}]), "reflection.recall[0].count")
    _bad(_refl(recall=[{"kind": "saw", "fact": "bio", "value": 1, "at_ms": 1}]), "reflection.recall[0].fact")
    _bad(_refl(recall=[{"kind": "saw", "fact": "items", "value": 1, "at_ms": 1, "label": "approve"}]),
         "reflection.recall[0].label", "unexpected field")
    _bad(_refl(recall=[{"kind": "model_says", "quote": "it is safe"}]), "reflection.recall[0].kind")
    _bad(_refl(why_now={"quote": "x", "at_ms": 1, "urgent": True}), "reflection.why_now.urgent")


def test_a_gentler_path_is_one_of_the_closed_set_and_never_carries_a_token():
    alt = GOOD[13]
    _bad({**alt, "path": "delete-later"}, "path", "not an allowed value")
    _bad({**alt, "authorization": TOKEN}, "authorization", "unexpected field")
    _bad({k: v for k, v in alt.items() if k != "path"}, "path", "missing")


def test_what_the_builder_makes_is_what_the_wire_accepts():
    refl = pytest.importorskip("secdogie_citadel.reflection")
    assert (refl.QUESTIONS, refl.PATHS, refl.SAW_FACTS) == (sp.QUESTIONS, sp.PATHS, sp.SAW_FACTS)
    assert refl.MAX_QUOTE == sp.LIMITS["reflection.quote"] and refl.MAX_RECALL == sp.LIMITS["reflection.recall"]
    assert refl.HEADLINE_MAX == sp.LIMITS["emotional_memory"]
    now = 1_760_000_000_000
    for lang in ("zh", "en"):
        for src, saw in ((refl.gather([], terms=[]), ()),
                         (refl.Sources((refl.Said("g", "旧号\n" + "记" * 300, 1),) * 3, refl.Said("w", "删", 2),
                                       ("相册",) * 2, 9, 3),
                          [refl.Observed(f, 2**53 - 1 if f != "joined_at" else 1, 5) for f in refl.SAW_FACTS])):
            r = refl.build_reflection(src, action_type="DESTRUCTIVE_UNBIND", now_ms=now, lang=lang, observed=saw)
            sp.validate({**GOOD[11], "emotional_memory": r.headline, "reflection": r.to_packet()})


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
