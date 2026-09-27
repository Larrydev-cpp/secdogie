"""Gate 2 wired into the action-plan gate: with enforcement on, a destructive
action is rejected unless it carries a valid operator-signed authorization
token. Off by default, and only destructive actions are gated."""
from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from secdogie_citadel.action_gate import (
    ALLOW,
    REJECT,
    UNAUTHORIZED_ACTION,
    GateContext,
    PlannedAction,
    gate,
)
from secdogie_citadel.authz import create_authorization
from secdogie_identity import (
    Allowlist,
    Identity,
    MasterSet,
    TrustPolicy,
    cosign,
    create_revocation,
)

OP = Identity.generate()
NODE = Identity.generate()


def _destructive():
    # destructive (kind in _DESTRUCTIVE_KINDS); expected_observation set so the
    # only thing that can reject it is the authorization check.
    return PlannedAction(kind="delete", target_id="f1", target_name="Delete",
                         expected_observation="the file is gone")


def _ctx(**kw):
    base = dict(require_authorization=True, operators=Allowlist({OP.did}), subject_did=NODE.did,
                requires_verification=False)
    base.update(kw)
    return GateContext(**base)


def test_destructive_without_a_token_is_rejected():
    d = gate(_destructive(), _ctx(authorization=None))
    assert d.verdict == REJECT and UNAUTHORIZED_ACTION in d.findings


def test_destructive_with_a_valid_operator_token_is_authorized():
    action = _destructive()
    token = create_authorization(OP, action, NODE.did)
    d = gate(action, _ctx(authorization=token))
    assert UNAUTHORIZED_ACTION not in d.findings
    assert d.verdict == ALLOW


def test_a_token_for_a_different_action_does_not_authorize():
    token = create_authorization(OP, PlannedAction(kind="delete", target_id="OTHER"), NODE.did)
    d = gate(_destructive(), _ctx(authorization=token))
    assert d.verdict == REJECT and UNAUTHORIZED_ACTION in d.findings


def test_a_revoked_operator_token_is_rejected():
    master = Identity.generate()
    policy = TrustPolicy(Allowlist({OP.did}), masters=MasterSet([master.did]))
    action = _destructive()
    token = create_authorization(OP, action, NODE.did)
    assert gate(action, _ctx(authorization=token, operators=policy)).verdict == ALLOW
    policy.apply(cosign(master, create_revocation([OP.did])))
    d = gate(action, _ctx(authorization=token, operators=policy))
    assert d.verdict == REJECT and UNAUTHORIZED_ACTION in d.findings


def test_non_destructive_actions_are_not_gated_by_authorization():
    click = PlannedAction(kind="left_click", target_id="btn", target_name="OK",
                          expected_observation="dialog closes")
    d = gate(click, _ctx(authorization=None))
    assert UNAUTHORIZED_ACTION not in d.findings and d.verdict == ALLOW


def test_authorization_is_off_by_default():
    # A gate context without require_authorization must behave as before: a
    # destructive action is not rejected for lack of a token (other checks still
    # apply; here it passes because verification is provided).
    d = gate(_destructive(), GateContext(requires_verification=False))
    assert UNAUTHORIZED_ACTION not in d.findings and d.verdict == ALLOW
