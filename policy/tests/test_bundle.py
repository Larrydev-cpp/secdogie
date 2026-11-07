"""Policy bundles end to end: build, sign, verify, chain."""
from __future__ import annotations

import pytest
from secdogie_identity import ALLOW_ANY, Allowlist, Identity, MasterSet
from secdogie_policy import (
    BUNDLE_TYPE,
    assemble,
    bundle_id,
    check_bundle,
    check_successor,
    create_bundle,
    sign_bundle,
    verify_bundle,
)

OP = Identity.generate()
OPS = Allowlist({OP.did})
M = [Identity.generate() for _ in range(3)]
MASTERS = MasterSet([m.did for m in M], 2)


def gate(**kw):
    kw.setdefault("mesh", "lab")
    kw.setdefault("version", 1)
    return create_bundle("gate-rules", body=kw.pop("body", {"instruction": {"max_len": 400}}), **kw)


def test_an_operator_signed_gate_rule_verifies():
    b = sign_bundle(OP, gate())
    v = verify_bundle(b, operators=OPS)
    assert v.ok and v.signers == (OP.did,) and v.errors == ()
    assert b["type"] == BUNDLE_TYPE and b["bundle_id"] == bundle_id(b)


def test_signing_is_idempotent_and_names_what_was_signed():
    b = sign_bundle(OP, sign_bundle(OP, gate()))
    assert len(b["sigs"]) == 1
    assert b["bundle_id"] == bundle_id(b)


def test_create_refuses_a_loosening_bundle():
    with pytest.raises(ValueError, match="loosens:body.instruction.max_len"):
        gate(body={"instruction": {"max_len": 601}})


def test_allow_any_is_never_accepted_for_bundles():
    with pytest.raises(ValueError):
        verify_bundle(sign_bundle(OP, gate()), operators=ALLOW_ANY)


def test_missing_trust_sets_authorize_nothing():
    b = sign_bundle(OP, gate())
    assert verify_bundle(b).errors == ("unauthorized:sigs",)
    dm = create_bundle("decision-model", mesh="lab", version=1, body={"policy": "router/v1"})
    dm = sign_bundle(M[0], sign_bundle(M[1], dm))
    assert verify_bundle(dm, operators=OPS).errors == ("unauthorized:sigs",)
    assert verify_bundle(dm, masters=MASTERS).ok


def test_master_kinds_need_the_threshold_and_operator_kinds_ignore_masters():
    dm = create_bundle("decision-model", mesh="lab", version=1, body={"policy": "router/v1"})
    one = sign_bundle(M[2], dm)
    assert verify_bundle(one, masters=MASTERS).errors == ("threshold:sigs",)
    assert verify_bundle(sign_bundle(M[0], one), masters=MASTERS).signers == tuple(sorted([M[0].did, M[2].did]))
    # a gate rule signed only by masters is not an operator's rule
    g = sign_bundle(M[0], sign_bundle(M[1], gate()))
    assert verify_bundle(g, operators=OPS, masters=MASTERS).errors == ("unauthorized:sigs",)


def test_a_revoked_operator_is_refused_by_a_revocation_aware_trust_set():
    class Revoked:
        def contains(self, did):
            return False

    assert not verify_bundle(sign_bundle(OP, gate()), operators=Revoked()).ok


def test_errors_are_sorted_and_unique_and_descent_stops_at_a_wrong_type():
    b = assemble("gate-rules", mesh="lab", version=1,
                 body={"plan": "strict", "instruction": {"max_len": 9999, "z": 1, "a": 2}})
    assert check_bundle(b) == ["loosens:body.instruction.max_len", "type:body.plan",
                               "unknown-field:body.instruction.a", "unknown-field:body.instruction.z"]


def test_chain():
    v1 = sign_bundle(OP, gate())
    v2 = sign_bundle(OP, gate(version=2, supersedes=v1["bundle_id"], body={}))
    assert check_successor(None, v1) == []
    assert check_successor(v1, v2) == []
    assert check_successor(v2, v1) == ["chain:supersedes", "chain:version"]
    assert check_successor(None, v2) == ["chain:version"]
