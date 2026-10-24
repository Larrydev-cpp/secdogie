"""The connection topology: who attaches to whom, as a line of nodes.

Three layers, as drawn: the central core, a line of P2P nodes, and the user.

  * the core attaches to the **edge** nodes -- the outermost healthy nodes at
    each end of the line. It holds up to `edge_depth` of them per side as warm,
    live connections, so one edge failing never leaves the core with a gap: the
    next is already attached;
  * the user attaches only to an **inner** node -- never an edge -- so the user
    is always at least one node away from an edge and two from the core, and so
    cannot reach (or attack) the core directly;
  * when an edge node degrades, the edge moves **inward** continuously. Health is
    EWMA (`NodeRegistry`), and a SUSPECT node is already excluded from `healthy`,
    so the switch happens *before* a hard failure, not only after a timeout.

The flexibility knobs:

  * `edge_depth` -- how many warm edges the core wants per side (default 1). With
    depth >= 2 the core keeps a hot standby already connected, so failover is
    gap-free rather than a reconnect. It is auto-reduced on a short line so the
    user always keeps at least `min_inner` inner nodes to attach to -- redundancy
    never costs the isolation buffer.
  * `min_inner` -- the inner buffer the user is guaranteed when the line can
    afford it (default 1). Below it there is no user entry at all: the user is
    never placed on an edge, so with no buffer the user simply has no entry.

This is a routing/role layer over the health in `NodeRegistry`; it decides
connection roles, not task dispatch (that is the coordinator). It owns no clock
-- health is read from the registry, which the caller sweeps with `now`.
"""

from __future__ import annotations

from .registry import HEALTHY, NodeRegistry


class LineTopology:
    def __init__(self, registry: NodeRegistry, *, edge_depth: int = 1, min_inner: int = 1) -> None:
        self.registry = registry
        self.edge_depth = max(1, edge_depth)
        self.min_inner = max(0, min_inner)
        self._line: list[str] = []

    def place(self, node_did: str) -> None:
        """Add a node to the end of the line (idempotent)."""
        if node_did not in self._line:
            self._line.append(node_did)

    def remove(self, node_did: str) -> None:
        if node_did in self._line:
            self._line.remove(node_did)

    def __contains__(self, node_did: str) -> bool:
        return node_did in self._line

    def _healthy(self) -> list[str]:
        return [d for d in self._line if self.registry.status(d) == HEALTHY]

    def _depth_for(self, n: int) -> int:
        """The per-side edge depth we can afford on `n` healthy nodes while still
        leaving `min_inner` inner nodes -- redundancy, reduced so it never eats
        the user's buffer. Always at least 1."""
        d = self.edge_depth
        while d > 1 and n - 2 * d < self.min_inner:
            d -= 1
        return min(d, (n + 1) // 2)

    def edges(self, *, depth: int | None = None) -> list[str]:
        """The outermost healthy nodes at each end -- what the core attaches to,
        up to `edge_depth` per side (warm standbys included). A degraded end is
        skipped inward; a short line collapses to the ends; none is empty."""
        h = self._healthy()
        if not h:
            return []
        k = self._depth_for(len(h)) if depth is None else min(max(1, depth), (len(h) + 1) // 2)
        out: list[str] = []
        for d in h[:k] + h[-k:]:  # overlaps on a short line -> deduped below
            if d not in out:
                out.append(d)
        return out

    def inner(self) -> list[str]:
        """Healthy nodes that are not edges -- the only nodes the user may use."""
        e = set(self.edges())
        return [d for d in self._healthy() if d not in e]

    def standbys(self) -> list[str]:
        """The warm edges beyond the single outermost at each end: already
        connected, so a failing outer edge is replaced with no reconnect. Empty
        when `edge_depth` is 1 or the line is too short to spare one."""
        outer = set(self.edges(depth=1))
        return [d for d in self.edges() if d not in outer]

    def core_attach(self) -> list[str]:
        """The edge nodes the core connects to (both ends; inward on failure)."""
        return self.edges()

    def user_entry(self, *, exclude: set[str] | None = None) -> str | None:
        """A healthy inner node for the user -- the most central one. None when
        there is no inner buffer: the user is never placed on an edge, so with no
        buffer the user simply has no entry."""
        ex = exclude or set()
        h = self._healthy()
        inner = [d for d in self.inner() if d not in ex]
        if not inner:
            return None
        center = (len(h) - 1) / 2
        inner.sort(key=lambda d: abs(h.index(d) - center))
        return inner[0]

    def user_is_isolated_from_core(self) -> bool:
        """The invariant this layer exists for: no healthy node is both a
        user-reachable inner node and a core-reachable edge."""
        return not (set(self.inner()) & set(self.edges()))

    def roles(self) -> dict[str, object]:
        """A one-call snapshot of the current layout -- for status/anchoring."""
        return {
            "edges": self.edges(),
            "standbys": self.standbys(),
            "inner": self.inner(),
            "user_entry": self.user_entry(),
            "isolated": self.user_is_isolated_from_core(),
        }
