"""The whole thing actually running: a core and several nodes as asyncio tasks
over real TCP sockets, each node doing a real bounded HTTPS crawl of a real
local server, the core verifying and deciding by quorum and writing the result
into a real DAG -- then losing a node and still completing.

Skips where the native route adapter / agent are not built; runs in runtime-tests.
"""

from __future__ import annotations

import asyncio

import pytest
from secdogie_identity import Allowlist, Identity
from secdogie_swarm._localsite import TlsSite
from secdogie_swarm.localnet import CoreServer, NodeClient
from secdogie_swarm.registry import HealthPolicy
from secdogie_swarm.service import SwarmCore, SwarmNode


def _ready() -> bool:
    try:
        import cryptography  # noqa: F401 - the local HTTPS test site needs it
        import secdogie_agent.browse  # noqa: F401
        import secdogie_graph  # noqa: F401

        secdogie_graph.library_path()
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="agent.browse / native route adapter not available")


async def _until(pred, what: str, ms: int = 10000) -> None:
    deadline = asyncio.get_event_loop().time() + ms / 1000
    while not pred():
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.02)


async def _scenario() -> None:
    from secdogie_agent.browse import https_opener
    from secdogie_graph import DagStore

    html = b'<a href="/account">a</a><a href="/settings">s</a>'
    with TlsSite(html) as site:
        core_id = Identity.generate()
        server = CoreServer()
        store = DagStore([core_id.did])
        core = SwarmCore(
            core_id,
            server,
            dag_store=store,
            assign_timeout_s=4.0,
            health_policy=HealthPolicy(suspect_after_s=1.5, dead_after_s=3.0),
        )
        port = await server.start()
        core.start_ticks(0.2)

        allow = Allowlist({core_id.did})
        opener = https_opener(site.client_context())
        nodes = []
        for _ in range(4):
            client = NodeClient("127.0.0.1", port)
            await client.start()
            node = SwarmNode(Identity.generate(), client, core_allow=allow, opener=opener)
            node.start()
            nodes.append((node, client))

        try:
            await _until(lambda: core.node_count == 4, "4 nodes enrolled")

            # 1) a real crawl of the real local page reaches quorum and is anchored.
            t1 = core.submit_crawl(site.url, [site.origin], redundancy=3)
            await _until(lambda: core.state_of(t1) == "decided", "t1 decided")
            assert core.anchored, "decided consensus was written into the DAG"
            view = store.view()
            assert len(view["states"]) >= 1 and len(view["references"]) >= 1

            # 2) lose a node; a fresh crawl still decides on the survivors.
            dead_node, dead_client = nodes.pop()
            await dead_node.stop()
            await dead_client.stop()
            await _until(lambda: core.node_count == 3, "a node dropped")
            t2 = core.submit_crawl(site.url, [site.origin], redundancy=3)
            await _until(lambda: core.state_of(t2) == "decided", "t2 decided after a loss")

            assert sum(n.crawls for n, _ in nodes) >= 2  # nodes really ran crawls
        finally:
            for node, client in nodes:
                await node.stop()
                await client.stop()
            await core.stop()
            await server.stop()
            store.close()


def test_core_and_nodes_run_end_to_end() -> None:
    asyncio.run(_scenario())
