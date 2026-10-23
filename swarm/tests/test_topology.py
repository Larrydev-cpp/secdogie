"""The isolation topology: core at the edges, user in the inner, failover inward,
and the user never on an edge."""

from __future__ import annotations

from secdogie_swarm import LineTopology, NodeRegistry


def line(n: int) -> tuple[LineTopology, NodeRegistry, list[str]]:
    reg = NodeRegistry()
    topo = LineTopology(reg)
    nodes = [f"did:key:n{i}" for i in range(n)]
    for d in nodes:
        reg.heartbeat(d, now=0.0, latency_s=0.1)
        topo.place(d)
    return topo, reg, nodes


def test_core_at_edges_user_in_the_inner() -> None:
    topo, _reg, n = line(5)
    assert topo.edges() == [n[0], n[4]]
    assert topo.inner() == [n[1], n[2], n[3]]
    assert topo.core_attach() == [n[0], n[4]]
    assert topo.user_entry() == n[2]  # most central
    assert topo.user_is_isolated_from_core()


def test_a_failed_edge_moves_inward() -> None:
    topo, reg, n = line(5)
    # kill the left end
    for i in range(3):
        reg.record(n[0], ok=False, now=1.0 + i)
    assert reg.status(n[0]) == "dead"
    assert topo.edges() == [n[1], n[4]]  # edge moved inward
    assert topo.inner() == [n[2], n[3]]
    assert n[0] not in topo.inner()


def test_user_is_never_placed_on_an_edge() -> None:
    topo, _reg, n = line(5)
    for _ in range(50):
        entry = topo.user_entry()
        assert entry is not None
        assert entry not in topo.edges()


def test_no_inner_buffer_means_no_user_entry() -> None:
    # two nodes: both are edges, there is no buffer, so the user gets no entry
    # (never put on an edge that can reach the core).
    topo, _reg, n = line(2)
    assert topo.edges() == [n[0], n[1]]
    assert topo.inner() == []
    assert topo.user_entry() is None
    # one node: a single edge, still no inner.
    topo1, _r1, _n1 = line(1)
    assert topo1.user_entry() is None


def test_removing_a_node_reshapes_the_line() -> None:
    topo, _reg, n = line(5)
    topo.remove(n[2])
    assert topo.inner() == [n[1], n[3]]
    assert topo.edges() == [n[0], n[4]]
