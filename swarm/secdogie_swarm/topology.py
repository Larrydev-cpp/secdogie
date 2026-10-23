"""The connection topology: who attaches to whom, as a line of nodes.

Three layers, as drawn: the central core, a line of P2P nodes, and the user.

  * the core attaches only to the **edge** nodes -- the outermost healthy node
    at each end of the line;
  * the user attaches only to an **inner** node -- never an edge -- so the user
    is always at least one node away from an edge and two from the core, and so
    cannot reach (or attack) the core directly;
  * when an edge node fails, the edge moves **inward** to the next healthy node.

This is a routing/role layer over the health in `NodeRegistry`; it decides
connection roles, not task dispatch (that is the coordinator). It owns no clock
-- health is read from the registry, which the caller sweeps with `now`.
"""

from __future__ import annotations

from .registry import HEALTHY, NodeRegistry


class LineTopology:
    def __init__(self, registry: NodeRegistry) -> None:
        self.registry = registry
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

    def edges(self) -> list[str]:
        """The outermost healthy node at each end -- what the core attaches to.
        A dead end is skipped inward; one healthy node is a single edge; none is
        empty."""
        h = self._healthy()
        if not h:
            return []
        if len(h) == 1:
            return [h[0]]
        return [h[0], h[-1]]

    def inner(self) -> list[str]:
        """Healthy nodes that are not edges -- the only nodes the user may use."""
        e = set(self.edges())
        return [d for d in self._healthy() if d not in e]

    def core_attach(self) -> list[str]:
        """The edge nodes the core connects to (both ends; inward on failure)."""
        return self.edges()

    def user_entry(self, *, exclude: set[str] | None = None) -> str | None:
        """A healthy inner node for the user -- the most central one. None when
        there is no inner buffer (fewer than 3 healthy nodes): the user is never
        placed on an edge, so with no buffer the user simply has no entry."""
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
