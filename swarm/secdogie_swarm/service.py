"""The running core and node, wired over a byte transport.

`SwarmCore` is the resident core: it enrolls nodes as they connect, dispatches
crawl tasks, runs the churn loop, verifies and decides by quorum, and anchors a
decided consensus into the DAG. `SwarmNode` is a contributor: it heartbeats,
runs a real bounded crawl on a DISPATCH, and signs the result back. Neither owns
a socket -- the transport (`localnet` in a local run, WebRTC in production) is
injected -- but everything else is real: real wire bytes, real signatures, real
fetch, real consensus, real DAG.

Time is real (`time.monotonic`); the coordinator's clock is seconds, and only
integer-millisecond stamps ever reach the wire.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any, Protocol

from secdogie_identity import Allowlist, Identity

from . import wire
from .anchor import DagAnchor
from .coordinator import SwarmCoordinator
from .registry import HealthPolicy, NodeRegistry
from .tasks import build_crawl
from .topology import LineTopology


class ServerTransport(Protocol):
    on_message: Callable[[int, bytes], None] | None
    on_disconnect: Callable[[int], None] | None

    def send(self, conn_id: int, data: bytes) -> None: ...


class ClientTransport(Protocol):
    on_message: Callable[[bytes], None] | None

    def send(self, data: bytes) -> None: ...


def _goal_id(seq: int) -> str:
    return f"task-{seq:08x}"


class SwarmCore:
    def __init__(
        self,
        identity: Identity,
        server: ServerTransport,
        *,
        dag_store: Any | None = None,
        health_policy: HealthPolicy | None = None,
        assign_timeout_s: float = 8.0,
        max_migrations: int = 3,
    ) -> None:
        self.core = identity
        self.server = server
        server.on_message = self._on_message
        server.on_disconnect = self._on_disconnect
        self.registry = NodeRegistry(health_policy)
        self.topology = LineTopology(self.registry)
        self._anchor = DagAnchor(identity, dag_store) if dag_store is not None else None
        self.coordinator = SwarmCoordinator(
            identity,
            self.registry,
            dispatch=self._dispatch,
            assign_timeout_s=assign_timeout_s,
            max_migrations=max_migrations,
            on_decided=self._on_decided,
            on_failed=self._on_failed,
        )
        self._conn_to_did: dict[int, str] = {}
        self._did_to_conn: dict[str, int] = {}
        self.decided: list[dict[str, Any]] = []
        self.failed: list[tuple[str, str]] = []
        self.anchored: list[dict[str, Any]] = []
        self._seq = 0
        self._tick_task: asyncio.Task | None = None

    @staticmethod
    def _now() -> float:
        return time.monotonic()

    # -- transport callbacks ------------------------------------------------
    def _dispatch(self, node_did: str, msg: dict[str, Any]) -> None:
        conn = self._did_to_conn.get(node_did)
        if conn is not None:
            self.server.send(conn, wire.encode(msg))

    def _on_message(self, conn_id: int, data: bytes) -> None:
        try:
            msg = wire.decode(data)
        except wire.WireError:
            return
        if msg["type"] == wire.HEARTBEAT and conn_id not in self._conn_to_did:
            did = msg["node_did"]
            self._conn_to_did[conn_id] = did
            self._did_to_conn[did] = conn_id
            self.coordinator.enroll(did, self._now())
            self.topology.place(did)
            return
        self.coordinator.feed(data, self._now())

    def _on_disconnect(self, conn_id: int) -> None:
        did = self._conn_to_did.pop(conn_id, None)
        if did is not None:
            self._did_to_conn.pop(did, None)
            self.registry.record(did, ok=False, now=self._now())  # churn migrates its work

    def _on_decided(self, record: dict[str, Any]) -> None:
        self.decided.append(record)
        if self._anchor is not None:
            out = self._anchor.anchor_crawl(record["output"])
            if out is not None:
                self.anchored.append({"task_id": record["task_id"], "outcome": out})

    def _on_failed(self, task_id: str, reason: str) -> None:
        self.failed.append((task_id, reason))

    # -- public API ---------------------------------------------------------
    @property
    def node_count(self) -> int:
        return len(self._did_to_conn)

    def submit_crawl(self, url: str, allowed_origins: list[str], *, redundancy: int = 3) -> str:
        self._seq += 1
        task = build_crawl(
            task_id=_goal_id(self._seq),
            url=url,
            allowed_origins=allowed_origins,
            issued_at_ms=int(time.time() * 1000),
            deadline_ms=10000,
            redundancy=redundancy,
            epoch=1,
        )
        return self.coordinator.submit(task, self._now())

    def state_of(self, task_id: str) -> str | None:
        return self.coordinator.state_of(task_id)

    async def run(self, tick_interval: float = 0.5) -> None:
        self._tick_task = asyncio.current_task()
        while True:
            self.coordinator.tick(self._now())
            await asyncio.sleep(tick_interval)

    def start_ticks(self, tick_interval: float = 0.5) -> asyncio.Task:
        self._tick_task = asyncio.create_task(self.run(tick_interval))
        return self._tick_task

    async def stop(self) -> None:
        if self._tick_task is not None:
            self._tick_task.cancel()


class SwarmNode:
    def __init__(
        self,
        identity: Identity,
        client: ClientTransport,
        *,
        core_allow: Allowlist,
        opener: Any | None = None,
        heartbeat_s: float = 2.0,
    ) -> None:
        self.id = identity
        self.client = client
        client.on_message = self._on_message
        self.core_allow = core_allow
        self.opener = opener
        self.heartbeat_s = heartbeat_s
        self.crawls = 0
        self._hb_task: asyncio.Task | None = None

    def start(self) -> None:
        self.client.send(wire.encode(wire.heartbeat(self.id.did)))  # announce first
        self._hb_task = asyncio.create_task(self._heartbeats())

    async def _heartbeats(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_s)
            self.client.send(wire.encode(wire.heartbeat(self.id.did)))

    def _on_message(self, data: bytes) -> None:
        try:
            msg = wire.decode(data)
        except wire.WireError:
            return
        if msg["type"] == wire.DISPATCH:
            asyncio.create_task(self._handle_dispatch(msg["task"]))

    async def _handle_dispatch(self, signed_task: dict[str, Any]) -> None:
        from .worker import run_crawl

        now_ms = int(time.time() * 1000)
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: run_crawl(
                    signed_task, self.id, now_ms=now_ms, opener=self.opener, core_allow=self.core_allow
                ),
            )
        except Exception:  # noqa: BLE001 - a bad task / fetch failure: just don't answer
            return
        self.crawls += 1
        self.client.send(wire.encode(wire.result(result)))

    async def stop(self) -> None:
        if self._hb_task is not None:
            self._hb_task.cancel()
