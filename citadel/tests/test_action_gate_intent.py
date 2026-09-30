"""Gate 1: intent contract (why / how to back out / do the preconditions hold)
and consolidated memory of known failures. Off by default; memory can only add
caution; Gate 2 never reads it."""
from __future__ import annotations

import dataclasses
import itertools

import pytest
from secdogie_citadel.action_gate import (
    ALLOW,
    INTENT_CONTRADICTION,
    INTENT_UNPROVEN,
    KNOWN_FAILURE,
    PRECONDITION_FAILED,
    REJECT,
    REQUEST_REOBSERVE,
    UNAUTHORIZED_ACTION,
    GateContext,
    IntentContract,
    PlannedAction,
    gate,
)
from secdogie_citadel.authz import action_hash

RANK = {ALLOW: 0, "rewrite": 1, REJECT: 2, REQUEST_REOBSERVE: 3}


def _act(kind="click", target="btn", intent=None, **kw):
    return PlannedAction(kind=kind, target_id=target, target_name="OK",
                         expected_observation="dialog closes", intent=intent or IntentContract(), **kw)


def _ctx(**kw):
    base = dict(requires_verification=False)
    base.update(kw)
    return GateContext(**base)


# ---- off by default ---------------------------------------------------------------


def test_nothing_changes_for_callers_that_do_not_opt_in():
    for a in (_act(), _act(kind="delete"), _act(kind="read")):
        d = gate(a, _ctx())
        assert not {INTENT_UNPROVEN, INTENT_CONTRADICTION, PRECONDITION_FAILED, KNOWN_FAILURE} & set(d.findings)


# ---- intent-unproven ----------------------------------------------------------------


def test_a_mutating_step_must_say_which_goal_it_serves():
    d = gate(_act(), _ctx(require_intent=True))
    assert d.verdict == REJECT and INTENT_UNPROVEN in d.findings and "purpose" in d.reason
    assert gate(_act(intent=IntentContract(purpose="g1")), _ctx(require_intent=True)).verdict == ALLOW


def test_a_read_needs_no_stated_intent():
    assert gate(_act(kind="read"), _ctx(require_intent=True)).verdict == ALLOW


def test_a_destructive_step_needs_a_rollback_or_an_explicit_irreversible():
    ctx = _ctx(require_intent=True)
    bare = _act(kind="delete", intent=IntentContract(purpose="g1"))
    d = gate(bare, ctx)
    assert d.verdict == REJECT and INTENT_UNPROVEN in d.findings and "rollback" in d.reason
    with_rollback = _act(kind="delete", intent=IntentContract(purpose="g1", rollback="restore from Trash"))
    assert gate(with_rollback, ctx).verdict == ALLOW
    acknowledged = _act(kind="delete", intent=IntentContract(purpose="g1", irreversible=True))
    assert gate(acknowledged, ctx).verdict == ALLOW
    # high_risk makes any kind destructive
    risky = _act(kind="click", high_risk=True, intent=IntentContract(purpose="g1"))
    assert INTENT_UNPROVEN in gate(risky, ctx).findings


def test_irreversible_is_an_acknowledgment_not_a_pass_through_gate_2():
    acknowledged = _act(kind="delete", intent=IntentContract(purpose="g1", irreversible=True))
    d = gate(acknowledged, _ctx(require_intent=True, require_authorization=True, operators=frozenset(),
                                subject_did="did:key:node"))
    assert d.verdict == REJECT and UNAUTHORIZED_ACTION in d.findings


# ---- intent-contradiction -----------------------------------------------------------


def test_irreversible_with_a_rollback_contradicts_itself():
    a = _act(kind="delete", intent=IntentContract(purpose="g1", rollback="undo", irreversible=True))
    d = gate(a, _ctx())
    assert d.verdict == REJECT and INTENT_CONTRADICTION in d.findings


def test_serving_a_goal_that_is_not_active():
    a = _act(intent=IntentContract(purpose="g-done"))
    d = gate(a, _ctx(active_goal_ids=frozenset({"g1", "g2"})))
    assert d.verdict == REJECT and INTENT_CONTRADICTION in d.findings and "g-done" in d.reason
    assert gate(_act(intent=IntentContract(purpose="g1")), _ctx(active_goal_ids=frozenset({"g1"}))).verdict == ALLOW
    # unknown active goals is not "no goals": no finding
    assert gate(a, _ctx()).verdict == ALLOW


# ---- precondition-failed ------------------------------------------------------------


def test_preconditions_are_checked_against_the_current_observation():
    a = _act(target="btn", intent=IntentContract(purpose="g1", requires_present=("btn", "dialog")))
    d = gate(a, _ctx(target_present_ids=frozenset({"btn"})))
    assert d.verdict == REQUEST_REOBSERVE and PRECONDITION_FAILED in d.findings and "dialog" in d.reason
    assert gate(a, _ctx(target_present_ids=frozenset({"btn", "dialog"}))).verdict == ALLOW
    assert gate(a, _ctx()).verdict == ALLOW  # nothing known about the screen: no judgment


# ---- known-failure (consolidated memory) --------------------------------------------


def test_an_action_that_failed_repeatedly_is_refused():
    a = _act()
    d = gate(a, _ctx(known_failures=frozenset({action_hash(a)})))
    assert d.verdict == REJECT and KNOWN_FAILURE in d.findings


def test_known_failure_is_about_the_effect_not_the_reasons_given():
    a = _act(intent=IntentContract(purpose="g1"))
    reworded = dataclasses.replace(a, intent=IntentContract(purpose="g2", rollback="undo"))
    memory = frozenset({action_hash(a)})
    assert KNOWN_FAILURE in gate(reworded, _ctx(known_failures=memory)).findings
    other_target = _act(target="other-btn")
    assert KNOWN_FAILURE not in gate(other_target, _ctx(known_failures=memory)).findings


# ---- invariants ---------------------------------------------------------------------


def _grid():
    kinds = ("click", "delete", "read", "type", "post")
    intents = (IntentContract(), IntentContract(purpose="g1"), IntentContract(purpose="g1", rollback="undo"),
               IntentContract(purpose="gX", irreversible=True), IntentContract(requires_present=("gone",)))
    ctxs = (_ctx(), _ctx(require_intent=True), _ctx(active_goal_ids=frozenset({"g1"})),
            _ctx(target_present_ids=frozenset({"btn"})), _ctx(require_intent=True, requires_verification=True))
    for kind, intent, ctx, risky in itertools.product(kinds, intents, ctxs, (False, True)):
        yield _act(kind=kind, intent=intent, high_risk=risky), ctx


def test_memory_only_ever_adds_caution():
    for a, ctx in _grid():
        base = gate(a, ctx)
        for memory in (frozenset({action_hash(a)}), frozenset({"0" * 64}), frozenset()):
            with_memory = gate(a, dataclasses.replace(ctx, known_failures=memory))
            assert RANK[with_memory.verdict] >= RANK[base.verdict], (a, ctx, memory)
            assert set(base.findings) <= set(with_memory.findings)


@pytest.mark.parametrize("token", [None, {"type": "secdogie/action-authorization/v1"}])
def test_gate_2_never_reads_memory(token):
    a = _act(kind="delete", intent=IntentContract(purpose="g1", rollback="undo"))
    ctx = _ctx(require_authorization=True, authorization=token, operators=frozenset(), subject_did="did:key:n")
    without = UNAUTHORIZED_ACTION in gate(a, ctx).findings
    for memory in (frozenset({action_hash(a)}), frozenset({"x"})):
        assert (UNAUTHORIZED_ACTION in gate(a, dataclasses.replace(ctx, known_failures=memory)).findings) == without
