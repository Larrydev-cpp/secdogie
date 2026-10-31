"""The mesh as each participant sees it: signed link state, paths, repairs.

Every node floods a signed heartbeat -- its roles, its neighbours, its load and
(for a model node) its public leases -- the way a link-state routing protocol
floods its advertisements. ``LinkStateDB`` keeps the newest per node. Two rules
keep that view honest without trusting any one node:

  * **A link exists only when both ends say so.** ``a -- b`` is in the graph
    when a's latest heartbeat lists b and b's lists a. A node cannot invent a
    link to someone else, and the coordinator's repair advice only becomes a
    link once both ends confirm it.
  * **Liveness is local.** A node is alive while this participant keeps
    receiving *new* heartbeats from it (a higher ``seq``), timed by its own
    clock -- not by the timestamp the node wrote, which a node could set to
    look alive forever.

Roles in a heartbeat are claims; ``serve`` / ``coordinator`` / ``broker`` count
only for DIDs on the matching trust list.

Paths are shortest by node cost (1 + load), deterministic on ties, and only pass
through transit nodes (serving or forwarding) -- never a model node, the
coordinator or the broker. ``plan_repairs`` is the coordinator's half of the
failure story in the diagram: bypass links around a failed node, links from the
nodes it leaves behind to the central server, and a standby forwarder ("new
P2P") for a model node left with no transit link.
"""
from __future__ import annotations

import heapq
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import NamedTuple

from .protocol import (
    MAX_LISTED,
    ROLE_BROKER,
    ROLE_COORDINATOR,
    ROLE_FORWARD,
    ROLE_MODEL,
    ROLE_SERVE,
    TRANSIT_ROLES,
    check_heartbeat,
)


@dataclass(frozen=True)
class NodeState:
    did: str
    roles: frozenset
    seq: int
    neighbors: frozenset
    load: float
    leases: tuple
    heard_at: float  # this participant's clock when the newest heartbeat arrived


class LinkStateDB:
    def __init__(self, trust, *, fail_after: float):
        self._trust = trust
        self.fail_after = float(fail_after)
        self._nodes: dict[str, NodeState] = {}

    def merge(self, heartbeat, *, now: float) -> bool:
        """Verify and keep a heartbeat when it is newer than what we hold."""
        if check_heartbeat(heartbeat, self._trust, now=now) is not None:
            return False
        did = heartbeat["node"]
        cur = self._nodes.get(did)
        if cur is not None and heartbeat["seq"] <= cur.seq:
            return False
        self._nodes[did] = NodeState(did, self._roles(did, heartbeat["roles"]), heartbeat["seq"],
                                     frozenset(heartbeat["neighbors"]), float(heartbeat["load"]),
                                     tuple(heartbeat["leases"]), float(now))
        return True

    def _roles(self, did: str, claimed) -> frozenset:
        gate = {ROLE_SERVE: self._trust.deciders, ROLE_COORDINATOR: self._trust.coordinators,
                ROLE_BROKER: self._trust.brokers}
        return frozenset(r for r in claimed if r not in gate or gate[r].contains(did))

    def get(self, did: str) -> NodeState | None:
        return self._nodes.get(did)

    def known(self) -> list[str]:
        return sorted(self._nodes)

    def alive(self, did: str, now: float) -> bool:
        st = self._nodes.get(did)
        return st is not None and now - st.heard_at <= self.fail_after

    def roles(self, did: str) -> frozenset:
        st = self._nodes.get(did)
        return st.roles if st is not None else frozenset()

    def with_role(self, role: str, now: float) -> list[str]:
        return sorted(d for d, st in self._nodes.items() if role in st.roles and self.alive(d, now))

    def last_neighbors(self, did: str) -> frozenset:
        st = self._nodes.get(did)
        return st.neighbors if st is not None else frozenset()

    def adjacency(self, now: float, *, exclude: Iterable[str] = ()) -> dict[str, set[str]]:
        """Confirmed links among live, non-excluded nodes."""
        excluded = set(exclude)
        live = {d for d in self._nodes if d not in excluded and self.alive(d, now)}
        adj: dict[str, set[str]] = {d: set() for d in live}
        for a in live:
            for b in self._nodes[a].neighbors:
                if b in live and a in self._nodes[b].neighbors:
                    adj[a].add(b)
        return adj

    def transit(self, now: float, *, exclude: Iterable[str] = ()) -> frozenset:
        excluded = set(exclude)
        return frozenset(d for d, st in self._nodes.items()
                         if st.roles & TRANSIT_ROLES and ROLE_COORDINATOR not in st.roles
                         and d not in excluded and self.alive(d, now))


def shortest_path(adj: dict[str, set[str]], src: str, dst: str, *, transit, avoid=frozenset(),
                  cost: Callable[[str], float] | None = None) -> tuple[str, ...] | None:
    """Cheapest path ``src -> dst`` whose interior nodes are all in ``transit``
    and none in ``avoid``. ``cost(node)`` is paid on entering a node (default
    1). Ties break on the path's DIDs, so every node computes the same path."""
    if src not in adj or dst not in adj or dst in avoid:
        return None
    cost = cost or (lambda _d: 1.0)
    heap: list[tuple[float, tuple[str, ...]]] = [(0.0, (src,))]
    settled: set[str] = set()
    while heap:
        dist, path = heapq.heappop(heap)
        node = path[-1]
        if node == dst:
            return path
        if node in settled:
            continue
        settled.add(node)
        for nxt in sorted(adj.get(node, ())):
            if nxt in settled or nxt in path:
                continue
            if nxt != dst and (nxt not in transit or nxt in avoid):
                continue
            heapq.heappush(heap, (dist + float(cost(nxt)), path + (nxt,)))
    return None


class Repairs(NamedTuple):
    """What the coordinator advises and does when nodes go down."""

    bypass: list[tuple[str, str]]   # links to open around the down nodes
    recruit: list[tuple[str, str]]  # (standby forwarder, model node or key broker) to link
    orphans: list[str]              # model nodes with no live transit link
    central: list[str]              # live nodes a down node leaves behind: they link to the coordinator


def plan_repairs(lsdb: LinkStateDB, now: float, *, down: set[str], standby: Iterable[str],
                 brokers: Iterable[str] = ()) -> Repairs:
    """What the coordinator advises when nodes go down -- drawing 2, "when a
    node fails, connect to the central server":

      * ``bypass``  -- links between the live neighbours a down node leaves
        behind (the arcs that skip a "wrong" node in the diagram), never
        directly between two model nodes;
      * ``central`` -- those same live neighbours (model and transit nodes):
        each links to the coordinator, so a model node it cut off is reachable
        over ``decider -> coordinator -> model node`` and a decider it cut off
        can send such a leg;
      * ``orphans`` -- model nodes with no live transit link, which the
        coordinator also attaches to itself for the central fallback;
      * ``recruit`` -- for each orphan, a standby forwarder (the "new P2P") to
        attach to it and to a live key broker, as drawn: linked to the model
        node and to the API key, ready to carry the user's pins in lockdown.
    """
    adj = lsdb.adjacency(now, exclude=down)
    transit = lsdb.transit(now, exclude=down)

    def attachable(d: str) -> bool:
        return d in transit or ROLE_MODEL in lsdb.roles(d)

    bypass: list[tuple[str, str]] = []
    central: set[str] = set()
    for gone in sorted(down):
        around = sorted(n for n in lsdb.last_neighbors(gone) if n in adj and attachable(n))
        central.update(around)
        for i, a in enumerate(around):
            for b in around[i + 1:]:
                if b not in adj[a] and (a in transit or b in transit):
                    bypass.append((a, b))

    live_brokers = sorted(d for d in brokers if d in adj and ROLE_BROKER in lsdb.roles(d))
    pool = [d for d in standby if d in transit and ROLE_FORWARD in lsdb.roles(d)
            and ROLE_SERVE not in lsdb.roles(d)]
    recruit: list[tuple[str, str]] = []
    orphans: list[str] = []
    used: set[str] = set()
    for p in sorted(d for d in adj if ROLE_MODEL in lsdb.roles(d)):
        if any(n in transit for n in adj[p]):
            continue
        orphans.append(p)
        helper = next((r for r in pool if r not in used and r not in adj[p]), None)
        if helper is None:
            continue
        used.add(helper)
        recruit.append((helper, p))
        anchor = next((k for k in live_brokers if k not in adj[helper]), None)
        if anchor is not None:
            recruit.append((helper, anchor))
    return Repairs(bypass[:MAX_LISTED], recruit[:MAX_LISTED], orphans, sorted(central))


__all__ = ["NodeState", "LinkStateDB", "Repairs", "shortest_path", "plan_repairs"]
