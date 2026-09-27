"""TrustPolicy: an allowlist narrowed by revocations, and RevocationStore's
persistence. Also that a revoked issuer's grants stop counting."""
from __future__ import annotations

import os
import tempfile

import pytest

pytest.importorskip("nacl")

from secdogie_identity import (
    Allowlist,
    Identity,
    RevocationStore,
    TrustPolicy,
    cosign,
    create_capability,
    create_revocation,
    effective_scopes,
)
from secdogie_identity.revocation import MasterSet


def _setup(threshold=1, masters_n=1):
    master_ids = [Identity.generate() for _ in range(masters_n)]
    masters = MasterSet([m.did for m in master_ids], threshold)
    allow = Allowlist()
    return master_ids, masters, allow


def _revoke(master_ids, dids, k=None):
    rec = create_revocation(dids)
    for m in master_ids[: (k if k is not None else 1)]:
        rec = cosign(m, rec)
    return rec


def test_contains_truth_table():
    master_ids, masters, allow = _setup()
    a, b = Identity.generate().did, Identity.generate().did
    allow.add(a)
    allow.add(b)
    policy = TrustPolicy(allow, masters=masters)
    assert policy.contains(a) and policy.contains(b)

    newly = policy.apply(_revoke(master_ids, [a]))
    assert newly == {a}
    assert not policy.contains(a)                # revoked
    assert policy.contains(b)                    # untouched
    assert policy.dids() == {b}
    assert Identity.generate().did not in policy.dids()  # never on the allowlist


def test_apply_is_idempotent_and_reports_only_new():
    master_ids, masters, allow = _setup()
    a, b = Identity.generate().did, Identity.generate().did
    for d in (a, b):
        allow.add(d)
    policy = TrustPolicy(allow, masters=masters)

    rec = _revoke(master_ids, [a])
    assert policy.apply(rec) == {a}
    assert policy.apply(rec) == frozenset()      # same record again: nothing new
    assert policy.apply(_revoke(master_ids, [a, b])) == {b}   # only b is new


def test_on_change_fires_only_for_new_revocations():
    master_ids, masters, allow = _setup()
    a = Identity.generate().did
    allow.add(a)
    policy = TrustPolicy(allow, masters=masters)
    seen = []
    policy.on_change(seen.append)
    rec = _revoke(master_ids, [a])
    policy.apply(rec)
    policy.apply(rec)                            # duplicate: no second callback
    assert seen == [frozenset({a})]


def test_invalid_revocation_changes_nothing():
    master_ids, masters, allow = _setup(threshold=2, masters_n=3)
    a = Identity.generate().did
    allow.add(a)
    policy = TrustPolicy(allow, masters=masters)
    assert policy.apply(_revoke(master_ids, [a], k=1)) == frozenset()  # under threshold
    assert policy.contains(a)


def test_a_policy_without_masters_holds_no_revocations():
    _, _, allow = _setup()
    a = Identity.generate().did
    allow.add(a)
    policy = TrustPolicy(allow)                   # no masters configured
    # even a would-be-valid record cannot take effect: fail closed, but here that
    # means it simply cannot revoke, so the DID stays authorized until masters are set
    assert policy.apply({"type": "secdogie/revocation/v1", "revoked": [a]}) == frozenset()
    assert policy.contains(a)


def test_version_increments_only_on_real_change():
    master_ids, masters, allow = _setup()
    a = Identity.generate().did
    allow.add(a)
    policy = TrustPolicy(allow, masters=masters)
    assert policy.version == 0
    policy.apply(_revoke(master_ids, [a]))
    assert policy.version == 1
    policy.apply(_revoke(master_ids, [a]))       # duplicate
    assert policy.version == 1


def test_revoked_issuer_grants_stop_counting():
    master_ids, masters, allow = _setup()
    issuer = Identity.generate()
    subject = Identity.generate().did
    allow.add(issuer.did)
    policy = TrustPolicy(allow, masters=masters)
    grant = create_capability(issuer, subject, ["observe.read"])

    # issuers is duck-typed on .contains, so the policy is the trusted-issuer set
    assert effective_scopes([grant], subject=subject, issuers=policy) == {"observe.read"}
    policy.apply(_revoke(master_ids, [issuer.did]))
    assert effective_scopes([grant], subject=subject, issuers=policy) == frozenset()


def test_store_persists_and_reloads():
    master_ids, masters, allow = _setup()
    a = Identity.generate().did
    allow.add(a)

    fd, path = tempfile.mkstemp()
    os.close(fd)
    try:
        store = RevocationStore(path)
        policy = TrustPolicy(allow, masters=masters, store=store)
        policy.apply(_revoke(master_ids, [a]))
        assert not policy.contains(a)

        # a fresh policy over the same store recovers the revocation
        allow2 = Allowlist({a})
        reloaded = TrustPolicy(allow2, masters=masters, store=RevocationStore(path))
        assert not reloaded.contains(a)
    finally:
        os.unlink(path)
