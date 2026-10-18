"""Untrusted-node results: verify the signature, decide by quorum, drop outliers."""

from __future__ import annotations

from secdogie_identity import Allowlist, Identity, sign_payload
from secdogie_swarm import Tally, build_result, output_digest, quorum_for, verify_result


def signed_result(identity: Identity, task_id: str, output: dict, now: float = 0.0) -> dict:
    return sign_payload(identity, build_result(task_id=task_id, node_did=identity.did, output=output, produced_at=now))


def test_verify_accepts_an_assigned_enrolled_signed_result() -> None:
    node = Identity.generate()
    allow = Allowlist({node.did})
    r = signed_result(node, "t-1", {"content_hash": "abc"})
    ok, reason, signer, digest = verify_result(r, task_id="t-1", assigned={node.did}, allowlist=allow)
    assert ok and reason is None and signer == node.did
    assert digest == output_digest({"content_hash": "abc"})


def test_verify_rejects_unenrolled_wrong_task_and_unassigned() -> None:
    node = Identity.generate()
    empty = Allowlist(set())
    r = signed_result(node, "t-1", {"content_hash": "abc"})
    # not enrolled
    ok, *_ = verify_result(r, task_id="t-1", assigned={node.did}, allowlist=empty)
    assert not ok
    allow = Allowlist({node.did})
    # wrong task
    ok, *_ = verify_result(r, task_id="t-OTHER", assigned={node.did}, allowlist=allow)
    assert not ok
    # not assigned
    ok, *_ = verify_result(r, task_id="t-1", assigned=set(), allowlist=allow)
    assert not ok


def test_verify_rejects_tampering_after_signing() -> None:
    node = Identity.generate()
    allow = Allowlist({node.did})
    r = signed_result(node, "t-1", {"content_hash": "abc"})
    r["output"] = {"content_hash": "tampered"}  # altered in transit
    ok, *_ = verify_result(r, task_id="t-1", assigned={node.did}, allowlist=allow)
    assert not ok  # the signature no longer covers it


def test_verify_rejects_a_node_signing_as_another_node() -> None:
    # A validly-signed result that *claims* to be from a different node: node A
    # signs a payload naming node B. The signature is fine, the identity is not.
    a, b = Identity.generate(), Identity.generate()
    allow = Allowlist({a.did, b.did})
    forged = sign_payload(a, build_result(task_id="t-1", node_did=b.did, output={"content_hash": "x"}, produced_at=0.0))
    ok, reason, *_ = verify_result(forged, task_id="t-1", assigned={a.did, b.did}, allowlist=allow)
    assert not ok and "node_did" in reason


def test_tally_decides_by_quorum_and_names_outliers() -> None:
    t = Tally()
    t.add("a", "D1")
    t.add("b", "D1")
    t.add("c", "D2")  # the odd one out
    assert quorum_for(3) == 2
    decided = t.decide(quorum_for(3))
    assert decided is not None
    digest, agree = decided
    assert digest == "D1" and agree == frozenset({"a", "b"})
    assert t.outliers("D1") == frozenset({"c"})


def test_tally_waits_without_quorum() -> None:
    t = Tally()
    t.add("a", "D1")
    t.add("b", "D2")
    assert t.decide(quorum_for(3)) is None
