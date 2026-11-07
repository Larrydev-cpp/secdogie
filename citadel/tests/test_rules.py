"""Injected gate rules: strictest-wins, never looser than the built-ins, and
the plan gate honours them (more Gate 2, refused kinds and targets, tighter
limits)."""
from __future__ import annotations

from secdogie_citadel import socratic
from secdogie_citadel.action_gate import GateContext, PlannedAction
from secdogie_citadel.loop_gate import BLOCKED_BY_RULE, make_plan_gate
from secdogie_citadel.rules import GateRules, normalize_phrase

BODY = {
    "instruction": {"max_len": 40, "max_steps": 1,
                    "revise_on": [{"phrases": ["mass register", "批量注册"], "reason": "批量注册不是我们要做的事",
                                   "suggestion": "说说你要处理的那一个账号"}]},
    "plan": {"require_intent": True, "repeat_threshold": 1, "poll_window": 2, "known_failure_min_runs": 2,
             "max_cost": 30, "extra_destructive_kinds": ["save"], "blocked_kinds": ["run"],
             "blocked_targets": [{"name": "terminal"}]},
    "presence": {"ask_before_new_goal": True, "note_every_mutation": True},
}


def test_defaults_change_nothing():
    r = GateRules()
    assert r.instruction_checks() == []
    ctx = GateContext()
    assert r.context(ctx) == ctx
    a = PlannedAction("click", target_id="element:1")
    assert r.mark(a) == a and r.blocked_reason(a) is None


def test_a_body_is_read_and_merging_is_strictest():
    a = GateRules.from_bodies([("b1", BODY)])
    assert (a.max_len, a.max_steps, a.repeat_threshold, a.poll_window, a.known_failure_min_runs, a.max_cost) == \
        (40, 1, 1, 2, 2, 30)
    assert a.require_intent and a.ask_before_new_goal and a.note_every_mutation
    looser = {"instruction": {"max_len": 500}, "plan": {"max_cost": 100, "blocked_kinds": ["drag"]}}
    m = GateRules.from_bodies([("b1", BODY), ("b2", looser)])
    assert m.max_len == 40 and m.max_cost == 30  # the looser bundle cannot undo the stricter one
    assert m.blocked_kinds == {"run", "drag"} and m.sources == ("b1", "b2")


def test_a_looser_value_in_a_body_is_clamped_to_the_built_ins():
    r = GateRules.from_bodies([("x", {"instruction": {"max_len": 9999, "max_steps": 99},
                                      "plan": {"repeat_threshold": 9, "poll_window": 1, "require_intent": False}})])
    assert r.max_len == socratic._MAX_LEN and r.max_steps == socratic._MAX_STEPS
    assert r.repeat_threshold == GateContext().repeat_threshold and r.poll_window == 2
    assert r.require_intent is False


def test_instruction_review_under_the_rules():
    r = GateRules.from_bodies([("b1", BODY)])
    assert r.review("open the notes").accepted
    long = r.review("open the notes and then read them and then summarize")
    assert not long.accepted and "overlong for this node" in long.reasons[0]
    phrase = r.review("请帮我批量注册账号")
    assert not phrase.accepted and phrase.reasons == ("批量注册不是我们要做的事",)
    assert not r.review("Please MASS   Register accounts").accepted  # normalized matching


def test_the_plan_gate_refuses_blocked_kinds_and_targets_before_asking_anyone():
    asked = []
    seen = []
    pg = make_plan_gate((), enforce=False, rules=GateRules.from_bodies([("b1", BODY)]),
                        authorize=lambda planned: asked.append(planned) or None,
                        observer=lambda planned, decision: seen.append(decision.findings))
    ok, why = pg({"kind": "open", "path": "/usr/bin/terminal"}, [])
    assert not ok and "terminal" in why
    ok, why = pg({"kind": "run"}, [])
    assert not ok and "'run'" in why
    assert asked == [] and seen == [(BLOCKED_BY_RULE,), (BLOCKED_BY_RULE,)]


def test_a_kind_the_rules_call_destructive_goes_to_gate_2():
    asked = []
    pg = make_plan_gate((), enforce=False, rules=GateRules.from_bodies([("b1", {"plan": {"extra_destructive_kinds":
                                                                                           ["type"]}})]),
                        authorize=lambda planned: asked.append(planned.high_risk) or None)
    ok, _ = pg({"kind": "type", "text": "hello", "element": "e1", "irreversible": True}, [])
    assert asked == [True] and not ok  # no signature: refused


def test_the_phrase_normal_form():
    assert normalize_phrase("  ＭＡＳＳ   Register ") == "mass register"
    assert normalize_phrase("Straße") == "straße"
