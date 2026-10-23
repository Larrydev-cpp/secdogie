"""Run the swarm end to end, locally, so you can watch it work:

    python -m secdogie_swarm.demo

A core and N contributor nodes start as asyncio tasks over real TCP sockets. A
real local HTTPS page is served. The core hands each node a crawl task; the
nodes fetch the page (real TLS), compress it to candidate states, and sign the
result back; the core verifies the signatures, decides by quorum, and writes the
agreed topology into the DAG. Then a node is dropped and a second crawl still
completes on the survivors.

Needs the native route adapter (cargo build --release in graph/) and the agent's
read-only web perception. Cloudflare Workers and real browser/WebGPU nodes are
the production transport and compute; everything printed here is real locally.
"""

from __future__ import annotations

import asyncio
import time

from secdogie_identity import Allowlist, Identity

from ._localsite import TlsSite
from .localnet import CoreServer, NodeClient
from .registry import HealthPolicy
from .service import SwarmCore, SwarmNode


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


async def _until(pred, what: str, ms: int = 10000) -> None:
    end = asyncio.get_event_loop().time() + ms / 1000
    while not pred():
        if asyncio.get_event_loop().time() > end:
            raise TimeoutError(f"timed out waiting for {what}")
        await asyncio.sleep(0.02)


async def run(n_nodes: int = 4, redundancy: int = 3) -> None:
    from secdogie_agent.browse import https_opener
    from secdogie_graph import DagStore

    html = b'<a href="/account">account</a> <a href="/settings">settings</a> <a href="/billing">billing</a>'
    with TlsSite(html) as site:
        _log(f"local HTTPS page at {site.url}")
        core_id = Identity.generate()
        server = CoreServer()
        store = DagStore([core_id.did])
        core = SwarmCore(
            core_id, server, dag_store=store, assign_timeout_s=4.0,
            health_policy=HealthPolicy(suspect_after_s=1.5, dead_after_s=3.0),
        )
        port = await server.start()
        core.start_ticks(0.2)
        _log(f"core {core_id.did[:24]}… listening on 127.0.0.1:{port}")

        allow = Allowlist({core_id.did})
        opener = https_opener(site.client_context())
        nodes = []
        for i in range(n_nodes):
            client = NodeClient("127.0.0.1", port)
            await client.start()
            node = SwarmNode(Identity.generate(), client, core_allow=allow, opener=opener)
            node.start()
            nodes.append((node, client))
            _log(f"  node {i} {node.id.did[:24]}… connected")

        await _until(lambda: core.node_count == n_nodes, "all nodes enrolled")
        _log(f"{core.node_count} nodes enrolled; core at edges {[d[:12] for d in core.topology.edges()]}, "
             f"user entry {str(core.topology.user_entry())[:12]}…")

        _log(f"submit crawl (redundancy {redundancy}) …")
        t1 = core.submit_crawl(site.url, [site.origin], redundancy=redundancy)
        await _until(lambda: core.state_of(t1) == "decided", "first crawl decided")
        rec = core.decided[-1]
        _log(f"DECIDED by {len(rec['nodes'])} agreeing nodes; "
             f"{len(rec['output']['candidates'])} candidate states found")
        view = store.view()
        _log(f"anchored into DAG: {len(view['states'])} states, {len(view['references'])} references")

        dead, dead_client = nodes.pop()
        await dead.stop()
        await dead_client.stop()
        await _until(lambda: core.node_count == n_nodes - 1, "a node dropped")
        _log(f"dropped a node; {core.node_count} remain. submit another crawl …")
        t2 = core.submit_crawl(site.url, [site.origin], redundancy=redundancy)
        await _until(lambda: core.state_of(t2) == "decided", "second crawl decided")
        _log("DECIDED again on the survivors — the network tolerated the loss.")

        for node, client in nodes:
            await node.stop()
            await client.stop()
        await core.stop()
        await server.stop()
        store.close()
        _log("done.")


def main() -> int:
    try:
        asyncio.run(run())
    except (ImportError, OSError) as e:
        print(f"demo unavailable: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
