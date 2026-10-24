"""The isolation topology: core at the edges, user in the inner, failover inward,
and the user never on an edge."""

from __future__ import annotations

from secdogie_swarm import LineTopology, NodeRegistry


def line(n: int, *, edge_depth: int = 1) -> tuple[LineTopology, NodeRegistry, list[str]]:
    reg = NodeRegistry()
    topo = LineTopology(reg, edge_depth=edge_depth)
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


def test_warm_edges_hold_a_standby_per_side() -> None:
    # depth 2 on a long line: the core keeps two live edges per side, so the
    # outer edge and a hot standby are both already attached.
    topo, _reg, n = line(6, edge_depth=2)
    assert topo.edges() == [n[0], n[1], n[4], n[5]]
    assert topo.standbys() == [n[1], n[4]]  # the inner of each warm pair
    assert topo.inner() == [n[2], n[3]]
    assert topo.user_entry() in (n[2], n[3])
    assert topo.user_is_isolated_from_core()


def test_warm_failover_is_gap_free() -> None:
    # with a hot standby already attached, killing the outer edge leaves the
    # standby as the edge immediately -- no moment with a bare end.
    topo, reg, n = line(6, edge_depth=2)
    assert n[1] in topo.edges()  # standby is live before the failure
    for i in range(3):
        reg.record(n[0], ok=False, now=1.0 + i)
    assert reg.status(n[0]) == "dead"
    # n1 was already connected, so the core never lost its left edge
    assert topo.edges() == [n[1], n[2], n[4], n[5]]
    assert n[1] in topo.edges()


def test_edge_depth_never_eats_the_inner_buffer() -> None:
    # a short line cannot afford two edges per side without stranding the user,
    # so depth auto-reduces: the inner buffer is kept.
    topo, _reg, n = line(4, edge_depth=2)
    assert topo.edges() == [n[0], n[3]]  # reduced to depth 1
    assert topo.inner() == [n[1], n[2]]
    assert topo.user_entry() is not None
    assert topo.user_is_isolated_from_core()


def test_suspect_edge_is_dropped_before_it_dies() -> None:
    # a slow (SUSPECT) end is already excluded from healthy, so the edge has
    # moved inward predictively -- not only after a hard timeout.
    topo, reg, n = line(5)
    for _ in range(3):  # drive the latency EWMA over the suspect bar
        reg.heartbeat(n[0], now=0.0, latency_s=10.0)
    assert reg.status(n[0]) == "suspect"
    assert topo.edges() == [n[1], n[4]]
    assert n[0] not in topo.inner()
