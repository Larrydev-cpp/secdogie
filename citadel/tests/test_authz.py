"""Gate 2 authorization tokens: an operator's Ed25519 signature, bound to one
concrete action and one node, is what lets a destructive action through. No
token, wrong token, expired, wrong node, or a revoked operator -> not authorized.

Headless: pure signing + verification, no desktop."""
from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

pytest.importorskip("nacl")

from secdogie_citadel.authz import (
    action_hash,
    create_authorization,
    verify_authorization,
)
from secdogie_identity import (
    Allowlist,
    Identity,
    MasterSet,
    TrustPolicy,
    cosign,
    create_revocation,
    sign_payload,
)


@dataclass(frozen=True)
class FakeAction:
    """The PlannedAction fields authz reads (duck-typed)."""
    kind: str = "delete"
    target_id: str = "file-42"
    target_role: str = "button"
    target_name: str = "Delete"
    text: str = ""
    high_risk: bool = True


def _operators(*dids):
    return Allowlist(set(dids))


def test_valid_token_authorizes_exactly_this_action_on_this_node():
    op, node = Identity.generate(), Identity.generate()
    action = FakeAction()
    token = create_authorization(op, action, node.did)
    res = verify_authorization(token, action, operators=_operators(op.did), subject=node.did)
    assert res.ok and res.signer == op.did and res.action_hash == action_hash(action)


def test_token_does_not_authorize_a_different_action():
    op, node = Identity.generate(), Identity.generate()
    token = create_authorization(op, FakeAction(target_id="file-42"), node.did)
    other = FakeAction(target_id="file-99")  # a different target -> different hash
    res = verify_authorization(token, other, operators=_operators(op.did), subject=node.did)
    assert not res.ok and "different action" in res.reason


def test_changing_any_effect_field_breaks_the_hash():
    base = FakeAction()
    h = action_hash(base)
    for field, value in [("kind", "overwrite"), ("target_id", "x"), ("target_role", "menu"),
                         ("target_name", "Erase"), ("text", "y"), ("high_risk", False)]:
        assert action_hash(replace(base, **{field: value})) != h


def test_token_for_another_node_does_not_apply_here():
    op, node, other_node = Identity.generate(), Identity.generate(), Identity.generate()
    action = FakeAction()
    token = create_authorization(op, action, other_node.did)
    res = verify_authorization(token, action, operators=_operators(op.did), subject=node.did)
    assert not res.ok and "different node" in res.reason


def test_expired_and_not_yet_valid():
    op, node = Identity.generate(), Identity.generate()
    action = FakeAction()
    token = create_authorization(op, action, node.did, valid_from=1000.0, expires_at=1100.0)
    ops = _operators(op.did)
    assert verify_authorization(token, action, operators=ops, subject=node.did, now=1050.0).ok
    assert not verify_authorization(token, action, operators=ops, subject=node.did, now=1100.0).ok
    assert not verify_authorization(token, action, operators=ops, subject=node.did, now=999.0).ok
    with pytest.raises(ValueError):
        create_authorization(op, action, node.did, valid_from=10.0, expires_at=10.0)


def test_a_non_operator_signature_is_refused():
    op, stranger, node = Identity.generate(), Identity.generate(), Identity.generate()
    action = FakeAction()
    token = create_authorization(stranger, action, node.did)
    res = verify_authorization(token, action, operators=_operators(op.did), subject=node.did)
    assert not res.ok and res.signer == stranger.did


def test_a_revoked_operator_can_no_longer_authorize():
    master, op, node = Identity.generate(), Identity.generate(), Identity.generate()
    action = FakeAction()
    token = create_authorization(op, action, node.did)
    policy = TrustPolicy(Allowlist({op.did}), masters=MasterSet([master.did]))
    assert verify_authorization(token, action, operators=policy, subject=node.did).ok
    policy.apply(cosign(master, create_revocation([op.did])))
    res = verify_authorization(token, action, operators=policy, subject=node.did)
    assert not res.ok and res.signer == op.did   # authentic signature, operator now revoked


def test_a_validly_signed_object_of_another_type_is_refused():
    # Domain separation: a real operator signature over a NON-authorization body
    # (e.g. some other signed statement) must not pass as an authorization, even
    # though its signature verifies. This is the type check, not the signature.
    op, node = Identity.generate(), Identity.generate()
    action = FakeAction()
    foreign = sign_payload(op, {
        "type": "secdogie/something-else/v1",
        "action_hash": action_hash(action),
        "subject": node.did,
        "valid_from": 0.0,
        "expires_at": 1e18,
    })
    res = verify_authorization(foreign, action, operators=_operators(op.did), subject=node.did)
    assert not res.ok and "domain separation" in res.reason


def test_no_operators_and_garbage():
    op, node = Identity.generate(), Identity.generate()
    action = FakeAction()
    token = create_authorization(op, action, node.did)
    assert not verify_authorization(token, action, operators=None, subject=node.did).ok
    assert not verify_authorization("nope", action, operators=_operators(op.did), subject=node.did).ok


def test_a_tampered_window_does_not_verify():
    op, node = Identity.generate(), Identity.generate()
    action = FakeAction()
    token = create_authorization(op, action, node.did)
    # move the expiry out without re-signing -> signature no longer covers the body
    forged = {**token, "expires_at": token["expires_at"] + 10_000.0}
    assert not verify_authorization(forged, action, operators=_operators(op.did), subject=node.did).ok
