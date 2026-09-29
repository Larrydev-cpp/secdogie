"""Operator hooks in the plan gate and the Supervisor: the gate asks
``authorize`` for a destructive step and verifies what comes back; the
Supervisor routes ask_user through ``ask`` (journaled) and composes the bridge's
observer with the memory correlator. All headless with fake hooks."""
from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from secdogie_citadel.action_gate import UNAUTHORIZED_ACTION  # noqa: E402
from secdogie_citadel.authz import create_authorization  # noqa: E402
from secdogie_citadel.journal import Journal  # noqa: E402
from secdogie_citadel.loop_gate import BLOCKING, make_plan_gate, to_planned  # noqa: E402
from secdogie_citadel.supervisor import MemoryConfig, OperatorHooks, Supervisor  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402

NODE, OPERATOR = Identity.generate(), Identity.generate()
OPS = Allowlist({OPERATOR.did})

DELETE = {"kind": "key", "element": None, "x": None, "y": None, "text": "", "keys": ["delete"], "path": "",
          "high_risk": True, "rollback": "undo", "irreversible": False}
CLICK = {**DELETE, "kind": "left_click", "x": 1, "y": 2, "keys": [], "high_risk": False}


def _token_for(planned):
    return create_authorization(OPERATOR, planned, NODE.did)


def test_the_gate_asks_for_a_token_only_for_destructive_steps_and_verifies_it():
    asked = []

    def authorize(planned):
        asked.append(planned.kind)
        return _token_for(planned)

    gate = make_plan_gate((), enforce=False, authorize=authorize, operators=OPS, subject_did=NODE.did)
    assert gate(CLICK, [])[0] and asked == []
    assert gate(DELETE, [])[0] and asked == ["key"]


@pytest.mark.parametrize("authorize", [
    lambda planned: None,  # denied / timed out
    lambda planned: {"type": "secdogie/action-authorization/v1"},  # garbage
    lambda planned: create_authorization(OPERATOR, to_planned(CLICK), NODE.did),  # a token for another action
    lambda planned: create_authorization(Identity.generate(), planned, NODE.did),  # not an operator
    lambda planned: create_authorization(OPERATOR, planned, Identity.generate().did),  # for another node
    lambda planned: (_ for _ in ()).throw(RuntimeError("bridge down")),  # raising
])
def test_anything_short_of_a_valid_token_refuses_a_destructive_step(authorize):
    gate = make_plan_gate((), enforce=False, authorize=authorize, operators=OPS, subject_did=NODE.did)
    allowed, note = gate(DELETE, [])
    assert not allowed and "authorization" in note
    assert UNAUTHORIZED_ACTION in BLOCKING


def test_without_an_authorize_hook_the_gate_is_unchanged():
    assert make_plan_gate((), enforce=False)(DELETE, [])[0]


# ---- Supervisor ------------------------------------------------------------------------------


def _counter():
    n = {"t": 0.0}

    def clock():
        n["t"] += 1.0
        return n["t"]

    return clock


def _journal():
    return Journal(identity=NODE, allowlist=Allowlist({NODE.did}), clock=_counter())


def test_ask_user_goes_through_the_bridge_and_is_journaled():
    seen = {}

    def task(t, *, should_stop, on_status, confirm, record_step, ask=None, **kw):
        seen["ask"] = ask
        seen["answer"] = ask("which folder?") if ask else None
        return 0, "done"

    sup = Supervisor(_journal(), task)
    sup.set_operator_hooks(OperatorHooks(ask=lambda q: f"answer to {q}"))
    sup.add_goal("g1")
    sup.run_goal("g1")
    assert seen["answer"] == "answer to which folder?"
    kinds = [e["kind"] for e in sup.journal.events()]
    assert "ask_request" in kinds and "ask_result" in kinds
    result = next(e["body"] for e in sup.journal.events() if e["kind"] == "ask_result")
    assert result == {"goal_id": "g1", "answered": True, "answer": "answer to which folder?"}


def test_without_an_ask_hook_no_ask_is_passed():
    seen = {}

    def task(t, *, should_stop, on_status, confirm, record_step, **kw):
        seen.update(kw)
        return 0, "done"

    sup = Supervisor(_journal(), task)
    sup.add_goal("g1")
    sup.run_goal("g1")
    assert "ask" not in seen


def test_on_targets_reaches_the_task_only_when_given():
    seen = {}

    def task(t, *, should_stop, on_status, confirm, record_step, **kw):
        seen.update(kw)
        return 0, "done"

    sup = Supervisor(_journal(), task)
    sup.add_goal("g1")
    sup.run_goal("g1")
    assert "on_targets" not in seen
    view = []
    sup.set_operator_hooks(OperatorHooks(on_targets=view.append))
    sup.add_goal("g2")
    sup.run_goal("g2")
    assert seen["on_targets"] == view.append


def test_authorize_alone_installs_the_gate():
    seen = {}

    def task(t, *, should_stop, on_status, confirm, record_step, plan_gate=None, **kw):
        seen["gate"] = plan_gate
        return 0, "done"

    sup = Supervisor(_journal(), task)  # no issuers, no memory: the bridge is reason enough
    sup.set_operator_hooks(OperatorHooks(authorize=lambda p: None, operators=OPS))
    sup.add_goal("g1")
    sup.run_goal("g1")
    assert seen["gate"] is not None and seen["gate"](DELETE, [])[0] is False


def test_the_bridge_confirm_replaces_the_plain_handler():
    calls = []
    sup = Supervisor(_journal(), lambda *a, **k: (0, "done"), confirm_handler=lambda p, h: True)
    sup.set_operator_hooks(OperatorHooks(confirm=lambda p, h: calls.append((p, h)) or False))
    assert sup._confirm("g1", "Execute?", True) is False and calls == [("Execute?", True)]


def test_authorize_installs_the_gate_and_the_observers_compose():
    observed = []
    gates = {}

    def task(t, *, should_stop, on_status, confirm, record_step, plan_gate=None, **kw):
        gates["gate"] = plan_gate
        allowed, note = plan_gate(DELETE, [])
        record_step(observation={"f": 1}, action=DELETE, result="ok", outcome="ok")
        gates["allowed"] = allowed
        return 0, "done"

    asked = []
    sup = Supervisor(_journal(), task, memory=MemoryConfig())
    sup.set_operator_hooks(OperatorHooks(authorize=lambda p: asked.append(p.kind) or _token_for(p), operators=OPS,
                                         observe=lambda p, d: observed.append((p.kind, d.verdict))))
    sup.add_goal("g1", "tidy")
    sup.run_goal("g1")
    assert gates["allowed"] and asked == ["key"] and observed == [("key", "allow")]
    # the memory correlator still saw the step: it was recorded with its key
    from secdogie_citadel.episodes import episodes_from_events

    (ep,) = episodes_from_events(sup.journal.events()).values()
    assert ep.steps[0].action_key and ep.steps[0].outcome == "ok"
