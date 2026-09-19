"""A DAG goal tree, projected from journal `goal` events.

The journal is the source of truth; the goal tree is a pure fold over its events
in total order. Goal events are `kind="goal"` with a body:

    {"op": "add",      "id": "g1", "title": "...", "deps": ["g0"]}
    {"op": "update",   "id": "g1", "title"/"status"/"deps": ...}
    {"op": "complete", "id": "g1"}
    {"op": "remove",   "id": "g1"}

`build_goal_tree(events)` returns a GoalTree; malformed goal events are skipped
so a bad body never corrupts the projection. Nodes form a dependency DAG:
`ready()` are the goals whose dependencies are all done, `topo_order()` is a
dependency-respecting order, and cycles are detectable.
"""
from __future__ import annotations

from dataclasses import dataclass, field

_STATUSES = ("pending", "active", "done", "failed")


@dataclass
class Goal:
    id: str
    title: str = ""
    status: str = "pending"
    deps: tuple[str, ...] = ()


@dataclass
class GoalTree:
    nodes: dict[str, Goal] = field(default_factory=dict)

    # -- fold operations (applied in journal total order) -------------------

    def apply(self, body: dict) -> None:
        if not isinstance(body, dict):
            return
        op = body.get("op")
        gid = body.get("id")
        if not isinstance(gid, str) or not gid:
            return
        if op == "add":
            self.nodes[gid] = Goal(
                id=gid,
                title=str(body.get("title", "")),
                status=_norm_status(body.get("status", "pending")),
                deps=_norm_deps(body.get("deps")),
            )
        elif op == "update":
            node = self.nodes.get(gid)
            if node is None:
                return
            if "title" in body:
                node.title = str(body["title"])
            if "status" in body:
                node.status = _norm_status(body["status"])
            if "deps" in body:
                node.deps = _norm_deps(body["deps"])
        elif op == "complete":
            node = self.nodes.get(gid)
            if node is not None:
                node.status = "done"
        elif op == "remove":
            self.nodes.pop(gid, None)

    # -- queries ------------------------------------------------------------

    def ready(self) -> list[str]:
        """Pending goals whose every dependency exists and is done."""
        out = []
        for gid, node in self.nodes.items():
            if node.status != "pending":
                continue
            if all(d in self.nodes and self.nodes[d].status == "done" for d in node.deps):
                out.append(gid)
        return out

    def has_cycle(self) -> bool:
        try:
            self.topo_order()
            return False
        except ValueError:
            return True

    def topo_order(self) -> list[str]:
        """Ids in dependency order (a goal after its deps). Raises ValueError on
        a cycle. Edges to missing deps are ignored (a removed dep is not a cycle)."""
        indeg = {gid: 0 for gid in self.nodes}
        for gid, node in self.nodes.items():
            for d in node.deps:
                if d in self.nodes:
                    indeg[gid] += 1
        # deterministic: process ready nodes in sorted id order
        queue = sorted(g for g, n in indeg.items() if n == 0)
        order: list[str] = []
        while queue:
            gid = queue.pop(0)
            order.append(gid)
            for other, node in self.nodes.items():
                if gid in node.deps and other in indeg:
                    indeg[other] -= 1
                    if indeg[other] == 0:
                        queue.append(other)
            queue.sort()
        if len(order) != len(self.nodes):
            raise ValueError("goal graph has a cycle")
        return order


def _norm_status(value) -> str:
    s = str(value)
    return s if s in _STATUSES else "pending"


def _norm_deps(value) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(d) for d in value if isinstance(d, str) and d)


def build_goal_tree(events: list[dict]) -> GoalTree:
    """Fold journal events (in total order) into a GoalTree."""
    tree = GoalTree()
    for e in events:
        if e.get("kind") == "goal":
            tree.apply(e.get("body") or {})
    return tree
