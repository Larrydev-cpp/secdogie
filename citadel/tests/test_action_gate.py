"""Tests for the action-plan Socratic gate (Phase 2.6). Pure and headless: every
verdict is a function of the action + context.

The gate JUDGES only. These tests assert it never turns a refusal into an
allowance and that a rewrite is always *more* cautious (attaches verification),
never wider."""
from __future__ import annotations

import dataclasses

from secdogie_citadel import action_gate as ag
from secdogie_citadel.action_gate import GateContext, PlannedAction, gate


def _click(**kw):
    base = dict(kind="click", target_id="id=save", target_role="button", target_name="Save",
                expected_observation="the file is saved")
    base.update(kw)
    return PlannedAction(**base)


# --- allow ------------------------------------------------------------------


def test_clean_action_is_allowed():
    a = _click(generation=5)
    ctx = GateContext(current_generation=5, target_present_ids=frozenset({"id=save"}))
    d = gate(a, ctx)
    assert d.allowed and d.verdict == ag.ALLOW and d.findings == ()


def test_no_context_allows_a_verified_action():
    # With no context and a declared expectation, nothing is flagged.
    assert gate(_click()).verdict == ag.ALLOW


# --- request_reobserve (world moved) ----------------------------------------


def test_stale_generation_requests_reobserve():
    a = _click(generation=4)
    ctx = GateContext(current_generation=6, target_present_ids=frozenset({"id=save"}))
    d = gate(a, ctx)
    assert d.verdict == ag.REQUEST_REOBSERVE
    assert ag.STALE_TARGET in d.findings


def test_absent_target_requests_reobserve():
    a = _click(generation=5)
    ctx = GateContext(current_generation=5, target_present_ids=frozenset({"id=cancel"}))
    d = gate(a, ctx)
    assert d.verdict == ag.REQUEST_REOBSERVE
    assert ag.TARGET_MISMATCH in d.findings


def test_empty_present_set_is_unknown_not_absent():
    # An empty present-set means "presence unknown", so it must not false-reject.
    a = _click(generation=5)
    ctx = GateContext(current_generation=5, target_present_ids=frozenset())
    assert gate(a, ctx).verdict == ag.ALLOW


def test_reobserve_outranks_reject():
    # A stale target AND a destructive chain -> reobserve wins (judging the rest
    # is moot until the world is re-read).
    a = _click(kind="delete", generation=4, high_risk=True, expected_observation="gone")
    prev = _click(kind="delete", high_risk=True, expected_observation="gone")
    ctx = GateContext(current_generation=9, recent_actions=(prev,))
    d = gate(a, ctx)
    assert d.verdict == ag.REQUEST_REOBSERVE


# --- reject -----------------------------------------------------------------


def test_no_op_repeat_is_rejected():
    a = _click(generation=1)
    ctx = GateContext(current_generation=1, target_present_ids=frozenset({"id=save"}),
                      recent_actions=(_click(generation=1),))
    d = gate(a, ctx)
    assert d.verdict == ag.REJECT and ag.NO_OP in d.findings


def test_repeated_action_is_rejected():
    a = _click(generation=1)
    prior = tuple(_click(generation=1) for _ in range(2))  # already ran 2x
    ctx = GateContext(current_generation=1, target_present_ids=frozenset({"id=save"}),
                      recent_actions=prior, repeat_threshold=2)
    d = gate(a, ctx)
    assert d.verdict == ag.REJECT and ag.REPEATED in d.findings


def test_busy_polling_is_rejected():
    read = PlannedAction(kind="read", target_id="id=status")
    ctx = GateContext(recent_actions=(read, read), poll_window=3)
    d = gate(read, ctx)
    assert d.verdict == ag.REJECT and ag.POLLING in d.findings


def test_destructive_chain_is_rejected():
    a = _click(kind="delete", generation=2, high_risk=True, expected_observation="gone")
    prev = _click(kind="delete", generation=2, high_risk=True, expected_observation="gone")
    ctx = GateContext(current_generation=2, recent_actions=(prev,))
    d = gate(a, ctx)
    assert d.verdict == ag.REJECT and ag.DESTRUCTIVE_CHAIN in d.findings


def test_excessive_cost_is_rejected():
    a = _click(generation=1, estimated_cost=50.0)
    ctx = GateContext(current_generation=1, target_present_ids=frozenset({"id=save"}), cost_budget=10.0)
    d = gate(a, ctx)
    assert d.verdict == ag.REJECT and ag.EXCESSIVE_COST in d.findings


def test_out_of_capability_is_rejected_when_model_active():
    a = PlannedAction(kind="run", text="rm -rf /", expected_observation="done")
    ctx = GateContext(capabilities=frozenset({"physical.click"}))  # no process.run granted
    d = gate(a, ctx)
    assert d.verdict == ag.REJECT and ag.OUT_OF_CAPABILITY in d.findings


def test_capability_inactive_when_set_is_empty():
    # No set and no enforcement flag = caller hasn't opted in, so no cap reject.
    a = PlannedAction(kind="run", text="ls", expected_observation="listing shown")
    assert ag.OUT_OF_CAPABILITY not in gate(a, GateContext()).findings


# --- capability enforcement (Phase 2.9) -------------------------------------


def test_enforced_empty_set_grants_nothing():
    ctx = GateContext(current_generation=1, target_present_ids=frozenset({"id=save"}),
                      enforce_capabilities=True)
    d = gate(_click(generation=1), ctx)
    assert d.verdict == ag.REJECT and ag.OUT_OF_CAPABILITY in d.findings


def test_granted_scope_allows_the_action():
    ctx = GateContext(current_generation=1, target_present_ids=frozenset({"id=save"}),
                      capabilities=frozenset({"physical.click"}), enforce_capabilities=True)
    assert gate(_click(generation=1), ctx).allowed


def test_ungrantable_scope_is_refused_even_if_present():
    a = PlannedAction(kind="run_elevated", text="setup.exe", expected_observation="installed")
    ctx = GateContext(capabilities=frozenset({"process.run_elevated"}), enforce_capabilities=True)
    assert ag.OUT_OF_CAPABILITY in gate(a, ctx).findings


def test_unmapped_mutating_kind_is_refused_when_enforced():
    a = PlannedAction(kind="teleport", text="x", expected_observation="moved")
    ctx = GateContext(capabilities=frozenset({"physical.click"}), enforce_capabilities=True)
    assert ag.OUT_OF_CAPABILITY in gate(a, ctx).findings


def test_pure_observation_needs_no_grant():
    a = PlannedAction(kind="wait", expected_observation="dialog appears")
    ctx = GateContext(enforce_capabilities=True)
    assert ag.OUT_OF_CAPABILITY not in gate(a, ctx).findings


def test_signed_grant_drives_the_gate_end_to_end():
    from secdogie_identity import Allowlist, Identity
    from secdogie_identity.capability import create_capability, effective_scopes

    op, node = Identity.generate(), Identity.generate()
    grant = create_capability(op, node.did, ["physical.click"], valid_from=0.0, ttl=60)
    scopes = effective_scopes([grant], subject=node.did, issuers=Allowlist({op.did}), now=10.0)
    ctx = GateContext(current_generation=1, target_present_ids=frozenset({"id=save"}),
                      capabilities=scopes, enforce_capabilities=True)
    assert gate(_click(generation=1), ctx).allowed                       # click was granted
    typed = _click(kind="type", text="hello", generation=1)
    assert ag.OUT_OF_CAPABILITY in gate(typed, ctx).findings             # typing was not

    # once the grant expires, the same node holds nothing
    later = effective_scopes([grant], subject=node.did, issuers=Allowlist({op.did}), now=61.0)
    ctx_later = dataclasses.replace(ctx, capabilities=later)
    assert ag.OUT_OF_CAPABILITY in gate(_click(generation=1), ctx_later).findings


def test_unattended_posting_instruction_is_rejected():
    a = PlannedAction(kind="post", text="hello world", expected_observation="the post appears")
    ctx = GateContext(instruction="automatically post this to the forum without asking me")
    d = gate(a, ctx)
    assert d.verdict == ag.REJECT and ag.UNATTENDED_POSTING in d.findings


def test_attended_posting_instruction_is_not_flagged_for_that():
    a = PlannedAction(kind="post", text="hello", expected_observation="the post appears")
    ctx = GateContext(instruction="draft a post and let me review it before sending")
    assert ag.UNATTENDED_POSTING not in gate(a, ctx).findings


# --- rewrite (more cautious, never wider) -----------------------------------


def test_missing_verification_is_rewritten_with_an_expectation():
    a = PlannedAction(kind="click", target_id="id=ok", generation=1)  # no expected_observation
    ctx = GateContext(current_generation=1, target_present_ids=frozenset({"id=ok"}))
    d = gate(a, ctx)
    assert d.verdict == ag.REWRITE and ag.MISSING_VERIFICATION in d.findings
    assert d.replacement is not None
    assert d.replacement.expected_observation  # now carries a verification contract
    # the rewrite changes ONLY the expectation -- same kind/target/text (no widening)
    assert d.replacement.signature() == a.signature()


def test_read_action_needs_no_verification():
    a = PlannedAction(kind="read", target_id="id=status")
    assert gate(a, GateContext()).verdict == ag.ALLOW


def test_reject_outranks_rewrite():
    # missing verification (rewrite) AND excessive cost (reject) -> reject wins.
    a = PlannedAction(kind="click", target_id="id=ok", generation=1, estimated_cost=99.0)
    ctx = GateContext(current_generation=1, target_present_ids=frozenset({"id=ok"}), cost_budget=1.0)
    d = gate(a, ctx)
    assert d.verdict == ag.REJECT


# --- audit trail ------------------------------------------------------------


class _FakeJournal:
    def __init__(self):
        self.events = []

    def append(self, kind, body):
        ev = {"kind": kind, "body": body}
        self.events.append(ev)
        return ev


def test_record_gate_decision_appends_signed_event():
    a = _click(generation=1)
    d = gate(a, GateContext(current_generation=1, target_present_ids=frozenset({"id=save"})))
    j = _FakeJournal()
    ev = ag.record_gate_decision(j, a, d)
    assert ev["kind"] == "action-gate"
    assert j.events[0]["body"]["verdict"] == ag.ALLOW
