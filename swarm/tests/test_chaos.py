"""The running system under real instability: lying (Byzantine) nodes, silent
nodes, a node that dies mid-task, and a split with no reachable quorum. The core
must reach the HONEST result when it can and fail HONESTLY when it cannot -- it
must never accept a wrong result, and never hang.

Skips without the native route adapter / agent / cryptography; runs in runtime-tests.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from secdogie_identity import Allowlist, Identity, sign_payload
from secdogie_swarm import build_result, wire
from secdogie_swarm._localsite import TlsSite
from secdogie_swarm.localnet import CoreServer, NodeClient
from secdogie_swarm.registry import HealthPolicy
from secdogie_swarm.service import SwarmCore, SwarmNode


def _ready() -> bool:
    try:
        import cryptography  # noqa: F401
        import secdogie_agent.browse  # noqa: F401
        import secdogie_graph  # noqa: F401

        secdogie_graph.library_path()
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="agent.browse / native route adapter / cryptography not available")


class LyingNode(SwarmNode):
    """Validly signs a tampered output -- the signature passes, the content is a
    lie. Consensus must drop it as an outlier."""

    async def _handle_dispatch(self, signed_task: dict) -> None:
        task_id = signed_task["task_id"]
        bogus = build_result(
            task_id=task_id,
            node_did=self.id.did,
            output={"kind": "page", "content_hash": "dead" * 16, "document": None, "candidates": []},
            produced_at_ms=int(time.time() * 1000),
        )
        self.crawls += 1
        self.client.send(wire.encode(wire.result(sign_payload(self.id, bogus))))


class SilentNode(SwarmNode):
    """Takes work and never answers. Its slot must time out and migrate."""

    async def _handle_dispatch(self, signed_task: dict) -> None:
        return


class DifferentLie(SwarmNode):
    """Each instance lies differently, so no two ever agree."""

    async def _handle_dispatch(self, signed_task: dict) -> None:
        bogus = build_result(
            task_id=signed_task["task_id"],
            node_did=self.id.did,
            output={"kind": "page", "content_hash": self.id.did[-16:], "document": None, "candidates": []},
            produced_at_ms=int(time.time() * 1000),
        )
        self.client.send(wire.encode(wire.result(sign_payload(self.id, bogus))))


async def _until(pred, what: str, ms: int = 12000) -> None:
    end = asyncio.get_event_loop().time() + ms / 1000
    while not pred():
        if asyncio.get_event_loop().time() > end:
            raise AssertionError(f"timed out: {what}")
        await asyncio.sleep(0.02)


class Harness:
    def __init__(self) -> None:
        self.core_id = Identity.generate()
        self.server = CoreServer()
        self.nodes: list[tuple[SwarmNode, NodeClient]] = []

    async def __aenter__(self):
        from secdogie_agent.browse import https_opener
        from secdogie_graph import DagStore

        self.site = TlsSite(b'<a href="/a">a</a><a href="/b">b</a>').__enter__()
        self.store = DagStore([self.core_id.did])
        self.core = SwarmCore(
            self.core_id, self.server, dag_store=self.store, assign_timeout_s=2.0,
            health_policy=HealthPolicy(suspect_after_s=1.0, dead_after_s=2.0),
        )
        self.port = await self.server.start()
        self.core.start_ticks(0.15)
        self.allow = Allowlist({self.core_id.did})
        self.opener = https_opener(self.site.client_context())
        return self

    async def add(self, cls=SwarmNode):
        client = NodeClient("127.0.0.1", self.port)
        await client.start()
        node = cls(Identity.generate(), client, core_allow=self.allow, opener=self.opener, heartbeat_s=0.5)
        node.start()
        self.nodes.append((node, client))
        return node

    async def __aexit__(self, *exc):
        for node, client in self.nodes:
            await node.stop()
            await client.stop()
        await self.core.stop()
        await self.server.stop()
        self.store.close()
        self.site.__exit__()


def test_byzantine_and_silent_nodes_still_reach_the_honest_result() -> None:
    async def scenario() -> None:
        async with Harness() as h:
            honest = [await h.add(SwarmNode) for _ in range(3)]
            liar = await h.add(LyingNode)
            await h.add(SilentNode)
            await _until(lambda: h.core.node_count == 5, "5 nodes")
            tid = h.core.submit_crawl(h.site.url, [h.site.origin], redundancy=5)
            await _until(lambda: h.core.state_of(tid) == "decided", "decided despite a liar + a silent node")

            rec = h.core.decided[-1]
            assert len(rec["output"]["candidates"]) == 2  # the REAL page, not the lie
            assert liar.id.did not in rec["nodes"]  # the liar is not among the agreeing nodes
            assert set(rec["nodes"]) <= {x.id.did for x in honest}  # only honest nodes agreed
            await _until(lambda: h.core.registry.get(liar.id.did).error_ewma > 0, "liar penalized")
            assert h.core.anchored  # honest result written to the DAG

    asyncio.run(scenario())


def test_no_reachable_quorum_fails_honestly() -> None:
    async def scenario() -> None:
        async with Harness() as h:
            for _ in range(3):
                await h.add(DifferentLie)  # each disagrees with the others
            await _until(lambda: h.core.node_count == 3, "3 nodes")
            tid = h.core.submit_crawl(h.site.url, [h.site.origin], redundancy=3)
            await _until(lambda: h.core.state_of(tid) == "failed", "failed honestly, not hung")
            assert not h.core.anchored  # nothing wrong was written
            assert h.core.failed and h.core.failed[-1][0] == tid

    asyncio.run(scenario())


def test_a_node_that_dies_mid_flight_is_covered() -> None:
    async def scenario() -> None:
        async with Harness() as h:
            await h.add(SwarmNode)
            await h.add(SwarmNode)
            await h.add(SwarmNode)
            victim = await h.add(SwarmNode)
            await _until(lambda: h.core.node_count == 4, "4 nodes")
            tid = h.core.submit_crawl(h.site.url, [h.site.origin], redundancy=3)
            # kill one node immediately -- it may be mid-crawl.
            await victim.stop()
            await h.nodes[-1][1].stop()
            await _until(lambda: h.core.state_of(tid) == "decided", "decided despite a mid-flight death")
            assert len(h.core.decided[-1]["output"]["candidates"]) == 2

    asyncio.run(scenario())
