"""The core-side coordinator: hand a task to several nodes, move its work off a
node that is failing or going quiet *before* the deadline, collect signed
results, and decide by quorum.

It owns no clock and no sockets. `dispatch(node_did, signed_task)` is a callback
the caller wires to its transport (the test harness calls node stubs directly).
`tick(now)` drives timeouts and predictive migration. A decided task yields a
**core-signed consensus record** -- the thing the core anchors into the graph
DAG -- carrying the agreed output and the nodes that agreed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from secdogie_identity import Allowlist, Identity, sign_payload

from .consensus import Tally, quorum_for, verify_result
from .registry import NodeRegistry
from .tasks import PROTOCOL, assert_public, sign_task, validate_task

PENDING = "pending"
DECIDED = "decided"
FAILED = "failed"

Dispatch = Callable[[str, dict[str, Any]], None]


@dataclass
class _Job:
    task: dict[str, Any]
    signed_task: dict[str, Any]
    quorum: int
    assigned: dict[str, float] = field(default_factory=dict)  # node -> deadline
    ever: set[str] = field(default_factory=set)
    returned: set[str] = field(default_factory=set)
    tally: Tally = field(default_factory=Tally)
    first_output: dict[str, dict[str, Any]] = field(default_factory=dict)  # digest -> output
    migrations: int = 0
    state: str = PENDING


class SwarmCoordinator:
    def __init__(
        self,
        core_identity: Identity,
        registry: NodeRegistry,
        *,
        dispatch: Dispatch,
        assign_timeout_s: float = 10.0,
        max_migrations: int = 3,
        on_decided: Callable[[dict[str, Any]], None] | None = None,
        on_failed: Callable[[str, str], None] | None = None,
    ) -> None:
        self.core = core_identity
        self.registry = registry
        self._dispatch = dispatch
        self.assign_timeout_s = assign_timeout_s
        self.max_migrations = max_migrations
        self._on_decided = on_decided
        self._on_failed = on_failed
        self._enrolled: set[str] = set()
        self._jobs: dict[str, _Job] = {}

    # -- node enrollment / liveness ----------------------------------------
    def enroll(self, node_did: str, now: float) -> None:
        """Admit a node: it may now be assigned work and its signatures count."""
        self._enrolled.add(node_did)
        self.registry.heartbeat(node_did, now)

    @property
    def _allowlist(self) -> Allowlist:
        return Allowlist(set(self._enrolled))

    # -- submit -------------------------------------------------------------
    def submit(self, task: dict[str, Any], now: float) -> str:
        """Validate, assert it carries public work only, core-sign it, and
        assign `redundancy` healthy nodes."""
        validate_task(task)
        assert_public(task)
        signed = sign_task(self.core, task)
        job = _Job(task=task, signed_task=signed, quorum=quorum_for(task["redundancy"]))
        self._jobs[task["task_id"]] = job
        self._assign(job, task["redundancy"], now)
        self._check_feasible(job, now)
        return task["task_id"]

    def _assign(self, job: _Job, count: int, now: float) -> int:
        picks = self.registry.pick(count, exclude=job.ever)
        for did in picks:
            job.assigned[did] = now + self.assign_timeout_s
            job.ever.add(did)
            self._dispatch(did, job.signed_task)
        return len(picks)

    # -- results ------------------------------------------------------------
    def on_result(self, result: dict[str, Any], now: float) -> None:
        task_id = result.get("task_id") if isinstance(result, dict) else None
        job = self._jobs.get(task_id) if isinstance(task_id, str) else None
        if job is None or job.state != PENDING:
            return
        ok, _reason, signer, digest = verify_result(
            result, task_id=task_id, assigned=job.ever, allowlist=self._allowlist
        )
        if not ok or signer is None or digest is None:
            if signer is not None and signer in self.registry:
                self.registry.record(signer, ok=False, now=now)
            return
        if signer in job.returned:
            return  # a node votes once
        job.assigned.pop(signer, None)
        job.returned.add(signer)
        self.registry.record(signer, ok=True, now=now)
        job.tally.add(signer, digest)
        job.first_output.setdefault(digest, result["output"])
        self._try_decide(job, now)
        if job.state == PENDING:
            self._check_feasible(job, now)

    def _try_decide(self, job: _Job, now: float) -> None:
        decided = job.tally.decide(job.quorum)
        if decided is None:
            return
        digest, agree = decided
        job.state = DECIDED
        for loser in job.tally.outliers(digest):
            self.registry.record(loser, ok=False, now=now)  # disagreed with consensus
        record = sign_payload(
            self.core,
            {
                "protocol": PROTOCOL,
                "task_id": job.task["task_id"],
                "epoch": job.task["epoch"],
                "digest": digest,
                "output": job.first_output[digest],
                "nodes": sorted(agree),
                "decided_at": now,
            },
        )
        if self._on_decided:
            self._on_decided(record)

    # -- churn: timeouts + predictive migration -----------------------------
    def tick(self, now: float) -> None:
        self.registry.sweep(now)
        for job in list(self._jobs.values()):
            if job.state != PENDING:
                continue
            for did, deadline in list(job.assigned.items()):
                status = self.registry.status(did)
                degrading = status in ("suspect", "dead")
                timed_out = now > deadline
                if degrading or timed_out:
                    # This slot is failing. Drop it and, if we still can, move
                    # the work to a fresh healthy node before giving up.
                    job.assigned.pop(did, None)
                    self.registry.record(did, ok=False, now=now)
                    if job.migrations < self.max_migrations:
                        repl = self.registry.pick(1, exclude=job.ever)
                        if repl:
                            node = repl[0]
                            job.assigned[node] = now + self.assign_timeout_s
                            job.ever.add(node)
                            job.migrations += 1
                            self._dispatch(node, job.signed_task)
            self._check_feasible(job, now)

    def _check_feasible(self, job: _Job, now: float) -> None:
        """Fail a job that can no longer reach quorum (no outstanding slots and
        no digest at quorum)."""
        if job.state != PENDING:
            return
        if not job.assigned:
            if job.tally.decide(job.quorum) is None:
                job.state = FAILED
                if self._on_failed:
                    self._on_failed(job.task["task_id"], "no quorum: nodes dropped or disagreed")

    # -- introspection ------------------------------------------------------
    def state_of(self, task_id: str) -> str | None:
        job = self._jobs.get(task_id)
        return job.state if job else None
