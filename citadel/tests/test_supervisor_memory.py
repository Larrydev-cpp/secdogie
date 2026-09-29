"""Stage 2, Wave A: staged memory wired into the supervised loop. A fake run_task
behaves like the real loop -- it asks the plan gate before acting, then records
the step with its outcome -- so the whole path runs headless: episodic memory is
written per step, consolidation runs after each goal, a repeatedly failing
action is refused by Gate 1 on the next run, a destructive step must state its
rollback, and what the model remembers stays out of the prompt until the
operator confirms it."""
from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from secdogie_citadel.action_gate import IntentContract  # noqa: E402
from secdogie_citadel.authz import action_hash  # noqa: E402
from secdogie_citadel.consolidate import confirm_and_promote, create_confirmation  # noqa: E402
from secdogie_citadel.episodes import episodes_from_events  # noqa: E402
from secdogie_citadel.journal import Journal  # noqa: E402
from secdogie_citadel.loop_gate import make_plan_gate, to_planned  # noqa: E402
from secdogie_citadel.loop_memory import StepCorrelator  # noqa: E402
from secdogie_citadel.supervisor import MemoryConfig, Supervisor  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402

NODE = Identity.generate()
APP = Identity.generate()

CLICK = {"kind": "left_click", "element": None, "x": 10, "y": 20, "text": "", "keys": [], "path": "",
         "high_risk": False, "rollback": "", "irreversible": False}
DELETE = {**CLICK, "kind": "key", "x": None, "y": None, "keys": ["delete"], "high_risk": True}


def _counter():
    n = {"t": 0.0}

    def clock():
        n["t"] += 1.0
        return n["t"]

    return clock


def _supervisor(**memory_kw):
    j = Journal(identity=NODE, allowlist=Allowlist({NODE.did}), clock=_counter())
    cfg = MemoryConfig(confirmers=Allowlist({APP.did}), **memory_kw)
    return Supervisor(j, _fake_task, memory=cfg, unrestricted=True)  # memory is under test, not capabilities


SCRIPT: list = []  # (view, outcome) the fake loop plays each run
SEEN: dict = {}


def _fake_task(task, *, should_stop, on_status, confirm, record_step, plan_gate=None, remember=None,
               recall=None, **_):
    SEEN.setdefault("recalls", []).append(recall() if recall else None)
    SEEN.setdefault("gate", []).append([])
    for view, outcome in SCRIPT:
        allowed, note = plan_gate(view, [])
        SEEN["gate"][-1].append((allowed, note))
        result = "clicked" if allowed else f"refused by plan gate: {note}"
        record_step(observation={"f": 1}, action=view, result=result, outcome=outcome if allowed else "rejected")
    if remember is not None and SEEN.get("remember"):
        remember(*SEEN["remember"])
    return 0, "done"


@pytest.fixture(autouse=True)
def _reset():
    SCRIPT.clear()
    SEEN.clear()


def _run(sup, goal):
    sup.add_goal(goal, "tidy the toolbar")
    return sup.run_goal(goal)


# ---- the loop bridge pieces -------------------------------------------------------------


def test_to_planned_carries_the_models_intent_and_the_callers_purpose():
    p = to_planned({**DELETE, "rollback": "restore from Trash"}, purpose="g1")
    assert p.intent == IntentContract(purpose="g1", rollback="restore from Trash")
    assert to_planned({**DELETE, "irreversible": "yes"}).intent.irreversible is False
    assert to_planned({**DELETE, "irreversible": True}).intent.irreversible is True


def test_the_plan_gate_blocks_known_failures_and_unproven_intent_but_is_unchanged_by_default():
    key = action_hash(to_planned(CLICK))
    assert make_plan_gate((), enforce=False)(CLICK, [])[0]
    assert make_plan_gate((), enforce=False)(DELETE, [])[0]  # default: no intent required
    allowed, note = make_plan_gate((), enforce=False, known_failures={key})(CLICK, [])
    assert not allowed and "failed repeatedly" in note
    allowed, note = make_plan_gate((), enforce=False, require_intent=True, purpose="g1")(DELETE, [])
    assert not allowed and "rollback" in note
    assert make_plan_gate((), enforce=False, require_intent=True, purpose="g1")(
        {**DELETE, "rollback": "undo"}, [])[0]
    allowed, _ = make_plan_gate((), enforce=False, purpose="gone", active_goal_ids={"g1"})(CLICK, [])
    assert not allowed  # serving a goal that is no longer active


def test_an_ungated_step_carries_no_outcome_into_memory():
    j = Journal(identity=NODE, allowlist=Allowlist({NODE.did}), clock=_counter())

    def task(t, *, should_stop, on_status, confirm, record_step, **kw):
        # "done" / "look" / ask_user never pass the gate: whatever outcome the
        # adapter guesses for them must not become evidence about an action
        record_step(observation={"f": 1}, action={"kind": "done"}, result="done", outcome="failed")
        return 0, "done"

    sup = Supervisor(j, task, memory=MemoryConfig())
    sup.add_goal("g1")
    sup.run_goal("g1")
    (ep,) = episodes_from_events(j.events()).values()
    assert ep.steps[0].action_key == "" and ep.steps[0].outcome == "unknown"


def test_the_correlator_hands_a_key_to_the_next_matching_step_once():
    c = StepCorrelator()
    gate = make_plan_gate((), enforce=False, observer=c.observe)
    gate(CLICK, [])
    assert c.take({"kind": "left_click"}) == (action_hash(to_planned(CLICK)), ())
    assert c.take({"kind": "left_click"}) == ("", ())  # used once
    gate(CLICK, [])
    assert c.take({"kind": "done"}) == ("", ())  # a different step: no key
    assert c.take("left_click") == ("", ())


# ---- end to end through the Supervisor ----------------------------------------------------


def test_each_gated_step_is_recorded_with_its_key_and_outcome():
    sup = _supervisor()
    SCRIPT.extend([(CLICK, "no_change")])
    _run(sup, "g1")
    (ep,) = episodes_from_events(sup.journal.events()).values()
    (step,) = ep.steps
    assert step.action_key == action_hash(to_planned(CLICK)) and step.outcome == "no_change" and ep.usable


def test_an_action_that_failed_in_three_runs_is_refused_on_the_fourth():
    sup = _supervisor(min_runs=3)
    SCRIPT.extend([(CLICK, "failed")])
    for i in range(3):
        _run(sup, f"g{i}")
        assert SEEN["gate"][-1] == [(True, "")]
    assert action_hash(to_planned(CLICK)) in sup.memory_view().known_failures
    _run(sup, "g3")
    allowed, note = SEEN["gate"][-1][0]
    assert not allowed and "failed repeatedly" in note


def test_a_destructive_step_must_state_its_rollback():
    sup = _supervisor()
    SCRIPT.extend([(DELETE, "ok"), ({**DELETE, "irreversible": True}, "ok")])
    _run(sup, "g1")
    (bare, stated) = SEEN["gate"][-1]
    assert not bare[0] and "rollback" in bare[1]
    assert stated[0]


def test_what_the_model_remembers_waits_for_the_operator():
    sup = _supervisor(scope="app:cad")
    SEEN["remember"] = ("Save is in the toolbar", "save_button")
    _run(sup, "g1")
    (cand,) = sup._candidates.items()
    assert cand.key == "save_button" and cand.scope == "app:cad"
    _run(sup, "g2")
    assert SEEN["recalls"][-1] == ""  # quarantined: not in the prompt
    confirm_and_promote(sup.journal, sup._candidates, cand.candidate_id,
                        create_confirmation(APP, cand.candidate_id, NODE.did), confirmers=Allowlist({APP.did}))
    _run(sup, "g3")
    assert "save_button: Save is in the toolbar" in SEEN["recalls"][-1]


def test_a_failing_consolidation_never_fails_the_goal(monkeypatch):
    sup = _supervisor()
    monkeypatch.setattr(sup, "consolidate_memory", lambda: (_ for _ in ()).throw(RuntimeError("disk full")))
    SCRIPT.extend([(CLICK, "ok")])
    assert _run(sup, "g1") == (0, "done")


def test_without_memory_nothing_changes():
    j = Journal(identity=NODE, allowlist=Allowlist({NODE.did}), clock=_counter())
    passed = {}

    def task(t, *, should_stop, on_status, confirm, record_step, **kw):
        passed.update(kw)
        record_step(observation={"f": 1}, action=CLICK, result="clicked")
        return 0, "done"

    sup = Supervisor(j, task, unrestricted=True)
    sup.add_goal("g1")
    assert sup.run_goal("g1") == (0, "done")
    assert passed == {}  # no plan gate, no memory hooks
    assert sup.memory_view().records == {} and sup.consolidate_memory() is None
    (ep,) = episodes_from_events(j.events()).values()
    assert ep.steps[0].action_key == "" and ep.steps[0].outcome == "unknown"


def test_intent_purpose_is_the_running_goal(monkeypatch):
    import secdogie_citadel.loop_gate as lg

    seen = []
    real = lg.make_plan_gate
    monkeypatch.setattr(lg, "make_plan_gate", lambda *a, **kw: seen.append(kw.get("purpose")) or real(*a, **kw))
    sup = _supervisor()
    SCRIPT.extend([(CLICK, "ok")])
    _run(sup, "g7")
    assert seen == ["g7"]
