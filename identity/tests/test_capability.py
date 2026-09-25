"""Signed capability grants (Phase 2.9): mint, verify, expire, and match."""
from __future__ import annotations

import pytest
from secdogie_identity import Allowlist, Identity, sign_payload
from secdogie_identity.capability import (
    CAPABILITY_TYPE,
    allows,
    create_capability,
    effective_scopes,
    verify_capability,
)


def _parties():
    op, node = Identity.generate(), Identity.generate()
    return op, node, Allowlist({op.did})


def test_roundtrip():
    op, node, issuers = _parties()
    g = create_capability(op, node.did, ["physical.click", "observe.read"], valid_from=100.0, ttl=60)
    res = verify_capability(g, issuers=issuers, subject=node.did, now=120.0)
    assert res.ok
    assert res.issuer == op.did and res.subject == node.did
    assert res.scopes == ("observe.read", "physical.click")
    assert res.capability_id


def test_untrusted_issuer_is_refused():
    op, node, _ = _parties()
    g = create_capability(op, node.did, ["observe.read"], valid_from=0.0, ttl=60)
    other = Allowlist({Identity.generate().did})
    assert not verify_capability(g, issuers=other, now=1.0).ok


def test_no_issuer_list_means_nothing_verifies():
    op, node, _ = _parties()
    g = create_capability(op, node.did, ["observe.read"], valid_from=0.0, ttl=60)
    res = verify_capability(g, issuers=None, now=1.0)
    assert not res.ok and "no trusted issuers" in res.reason


def test_self_granting_node_is_refused():
    # A node that is not an issuer cannot grant itself anything.
    op, node, issuers = _parties()
    g = create_capability(node, node.did, ["physical.click"], valid_from=0.0, ttl=60)
    assert not verify_capability(g, issuers=issuers, now=1.0).ok


def test_tampered_scopes_are_refused():
    op, node, issuers = _parties()
    g = create_capability(op, node.did, ["observe.read"], valid_from=0.0, ttl=60)
    forged = {**g, "scopes": ["observe.read", "physical.click"]}
    assert not verify_capability(forged, issuers=issuers, now=1.0).ok


def test_wrong_subject_is_refused():
    op, node, issuers = _parties()
    g = create_capability(op, node.did, ["observe.read"], valid_from=0.0, ttl=60)
    stranger = Identity.generate()
    res = verify_capability(g, issuers=issuers, subject=stranger.did, now=1.0)
    assert not res.ok and "different subject" in res.reason


def test_validity_window():
    op, node, issuers = _parties()
    g = create_capability(op, node.did, ["observe.read"], valid_from=100.0, ttl=60)
    assert not verify_capability(g, issuers=issuers, now=99.0).ok    # not yet valid
    assert verify_capability(g, issuers=issuers, now=159.0).ok
    assert not verify_capability(g, issuers=issuers, now=160.0).ok   # expired


def test_default_ttl_is_one_day():
    op, node, issuers = _parties()
    g = create_capability(op, node.did, ["observe.read"], clock=lambda: 1000.0)
    assert g["expires_at"] - g["valid_from"] == 24 * 3600.0


def test_minting_refuses_ungrantable_or_empty_scopes():
    op, node, _ = _parties()
    with pytest.raises(ValueError):
        create_capability(op, node.did, ["process.run_elevated"])
    with pytest.raises(ValueError):
        create_capability(op, node.did, ["admin.all"])
    with pytest.raises(ValueError):
        create_capability(op, node.did, [])
    with pytest.raises(ValueError):
        create_capability(op, "not-a-did", ["observe.read"])


def test_verify_refuses_ungrantable_scope_even_from_a_trusted_issuer():
    # Hand-built grant that skips create_capability's check: verify still refuses.
    op, node, issuers = _parties()
    body = {
        "type": CAPABILITY_TYPE, "issuer": op.did, "subject": node.did,
        "scopes": ["admin.all"], "valid_from": 0.0, "expires_at": 60.0,
        "capability_id": "handmade",
    }
    res = verify_capability(sign_payload(op, body), issuers=issuers, now=1.0)
    assert not res.ok and "not grantable" in res.reason


def test_effective_scopes_unions_only_valid_grants_for_the_subject():
    op, node, issuers = _parties()
    other = Identity.generate()
    grants = [
        create_capability(op, node.did, ["observe.read"], valid_from=0.0, ttl=100),
        create_capability(op, node.did, ["physical.click"], valid_from=0.0, ttl=100),
        create_capability(op, node.did, ["physical.type"], valid_from=0.0, ttl=5),    # expired at t=50
        create_capability(op, other.did, ["network.post"], valid_from=0.0, ttl=100),  # someone else's
        create_capability(node, node.did, ["system.open"], valid_from=0.0, ttl=100),  # untrusted issuer
    ]
    got = effective_scopes(grants, subject=node.did, issuers=issuers, now=50.0)
    assert got == frozenset({"observe.read", "physical.click"})


def test_scopes_do_not_imply_each_other():
    granted = frozenset({"observe.read"})
    assert allows(granted, "observe.read")
    assert not allows(granted, "physical.click")          # observe != execute
    assert not allows(frozenset({"physical.click"}), "physical.type")
    # an ungrantable scope never matches, even if it somehow sits in the set
    assert not allows(frozenset({"process.run_elevated"}), "process.run_elevated")
