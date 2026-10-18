"""The coordinator end to end against simulated nodes: quorum on honest nodes,
an outlier dropped, predictive migration off a silent node, and an honest
failure when no quorum is reachable. Nodes are simulated by signing fabricated
results directly -- this exercises the distributed logic, not crawling."""

from __future__ import annotations

import pytest
from secdogie_identity import Identity, sign_payload, verify_payload
from secdogie_swarm import (
    DECIDED,
    FAILED,
    HealthPolicy,
    NodeRegistry,
    SwarmCoordinator,
    TaskError,
    assert_public,
    build_crawl,
    build_result,
    output_digest,
    wire,
)

HONEST = {"kind": "page", "content_hash": "abc", "candidate_keys": ["/x"]}
OUTLIER = {"kind": "page", "content_hash": "zzz", "candidate_keys": []}


class Swarm:
    def __init__(self, n_nodes: int, *, policy: HealthPolicy | None = None, **coord_kw) -> None:
        self.core = Identity.generate()
        self.registry = NodeRegistry(policy)
        self.dispatched: list[tuple[str, dict]] = []
        self.decided: list[dict] = []
        self.failed: list[tuple[str, str]] = []
        self.coord = SwarmCoordinator(
            self.core,
            self.registry,
            dispatch=lambda did, task: self.dispatched.append((did, task)),
            on_decided=self.decided.append,
            on_failed=lambda tid, reason: self.failed.append((tid, reason)),
            **coord_kw,
        )
        self.nodes: list[str] = []
        self._ident: dict[str, Identity] = {}
        for _ in range(n_nodes):
            ident = Identity.generate()
            self.nodes.append(ident.did)
            self._ident[ident.did] = ident
            self.coord.enroll(ident.did, now=0.0)

    def submit(self, now: float = 0.0, redundancy: int = 3) -> str:
        task = build_crawl(
            task_id="t-1",
            url="https://docs.example.com/",
            allowed_origins=["https://docs.example.com"],
            issued_at=now,
            deadline_ms=10000,
            redundancy=redundancy,
            epoch=1,
        )
        return self.coord.submit(task, now)

    def _signed_result(self, did: str, task_id: str, output: dict, now: float) -> dict:
        ident = self._ident[did]
        return sign_payload(ident, build_result(task_id=task_id, node_did=did, output=output, produced_at=now))

    def respond(self, did: str, task_id: str, output: dict, now: float) -> None:
        self.coord.on_result(self._signed_result(did, task_id, output, now), now)

    def respond_over_wire(self, did: str, task_id: str, output: dict, now: float) -> None:
        self.coord.feed(wire.encode(wire.result(self._signed_result(did, task_id, output, now))), now)

    def assigned_nodes(self) -> list[str]:
        return [did for did, _ in self.dispatched]

    def last_dispatch_to(self, did: str) -> dict:
        return [msg for d, msg in self.dispatched if d == did][-1]


def test_quorum_on_honest_nodes_yields_a_signed_record() -> None:
    s = Swarm(3)
    tid = s.submit()
    picks = s.assigned_nodes()
    assert len(picks) == 3
    s.respond(picks[0], tid, HONEST, now=1.0)
    assert s.coord.state_of(tid) == "pending"
    s.respond(picks[1], tid, HONEST, now=1.0)
    assert s.coord.state_of(tid) == DECIDED
    assert len(s.decided) == 1
    rec = s.decided[0]
    ok, signer = verify_payload(rec)  # the core signed the consensus record
    assert ok and signer == s.core.did
    assert rec["digest"] == output_digest(HONEST)
    assert rec["output"] == HONEST
    assert rec["nodes"] == sorted(picks[:2])


def test_an_outlier_is_dropped_and_penalized() -> None:
    s = Swarm(3)
    tid = s.submit()
    picks = s.assigned_nodes()
    s.respond(picks[0], tid, OUTLIER, now=1.0)
    s.respond(picks[1], tid, HONEST, now=1.0)
    s.respond(picks[2], tid, HONEST, now=1.0)
    assert s.coord.state_of(tid) == DECIDED
    assert s.decided[0]["output"] == HONEST
    # the outlier disagreed with consensus -> its error rate rose
    assert s.registry.get(picks[0]).error_ewma > 0.0


def test_predictive_migration_off_a_silent_node_still_decides() -> None:
    s = Swarm(4, assign_timeout_s=5.0)  # 3 assigned + 1 spare
    tid = s.submit()
    picks = s.assigned_nodes()
    assert len(picks) == 3
    spare = next(d for d in s.nodes if d not in picks)
    s.respond(picks[0], tid, HONEST, now=0.0)  # one honest answer in
    # picks[1], picks[2] stay silent; past their deadline the coordinator migrates.
    s.coord.tick(now=10.0)
    assert spare in s.assigned_nodes()  # work moved to the fresh node
    s.respond(spare, tid, HONEST, now=11.0)  # spare agrees -> quorum of 2
    assert s.coord.state_of(tid) == DECIDED
    assert s.decided[0]["output"] == HONEST


def test_no_quorum_fails_honestly() -> None:
    s = Swarm(3)
    tid = s.submit()
    picks = s.assigned_nodes()
    s.respond(picks[0], tid, {"content_hash": "d1"}, now=1.0)
    s.respond(picks[1], tid, {"content_hash": "d2"}, now=1.0)
    s.respond(picks[2], tid, {"content_hash": "d3"}, now=1.0)
    assert s.coord.state_of(tid) == FAILED
    assert s.failed and s.failed[0][0] == tid


def test_dispatched_message_carries_no_secret() -> None:
    s = Swarm(3)
    s.submit()
    for _did, msg in s.dispatched:
        assert msg["type"] == wire.DISPATCH
        assert_public(msg)  # the whole DISPATCH (task + any resume_from) is public
        assert_public(msg["task"])


def test_migration_hands_the_latest_checkpoint_to_the_replacement() -> None:
    s = Swarm(4, assign_timeout_s=5.0)  # 3 assigned + 1 spare
    tid = s.submit()
    picks = s.assigned_nodes()
    spare = next(d for d in s.nodes if d not in picks)
    # an assigned node reports partial public progress over the wire, then goes quiet.
    ckpt = {"pages_done": ["/a", "/b"]}
    s.coord.feed(wire.encode(wire.checkpoint(tid, picks[0], ckpt)), now=1.0)
    s.coord.tick(now=10.0)  # picks time out -> migrate to the spare
    assert spare in s.assigned_nodes()
    assert s.last_dispatch_to(spare)["resume_from"] == ckpt  # resumed, not restarted


def test_feed_routes_result_and_heartbeat_over_the_wire() -> None:
    s = Swarm(3)
    tid = s.submit()
    picks = s.assigned_nodes()
    s.respond_over_wire(picks[0], tid, HONEST, now=1.0)
    s.respond_over_wire(picks[1], tid, HONEST, now=1.0)
    assert s.coord.state_of(tid) == DECIDED
    # an unsolicited heartbeat keeps a node live
    s.coord.feed(wire.encode(wire.heartbeat(picks[2])), now=2.0)
    assert s.registry.get(picks[2]).last_seen == 2.0


def test_submit_refuses_a_task_someone_slipped_a_secret_into() -> None:
    s = Swarm(3)
    task = build_crawl(
        task_id="t-x",
        url="https://docs.example.com/",
        allowed_origins=["https://docs.example.com"],
        issued_at=0.0,
        deadline_ms=10000,
        redundancy=3,
        epoch=1,
    )
    task["operator_api_key"] = "sk-secret"  # someone tries to smuggle a credential
    with pytest.raises(TaskError):
        s.coord.submit(task, now=0.0)
