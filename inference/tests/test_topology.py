"""Link state, paths and repairs: links need both ends, liveness is local,
paths stay on transit nodes and are the same on every node."""
from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_inference.protocol import TrustRoots, make_heartbeat  # noqa: E402
from secdogie_inference.topology import LinkStateDB, plan_repairs, shortest_path  # noqa: E402

NOW = 1_000_000.0


@pytest.fixture
def mesh():
    ids = {n: Identity.generate() for n in ("a", "b", "c", "d", "k", "p", "q", "s")}
    did = {n: i.did for n, i in ids.items()}
    trust = TrustRoots(mesh=Allowlist(set(did.values())), users=Allowlist(), deciders=Allowlist({did["s"]}),
                       coordinators=Allowlist(), brokers=Allowlist(), operators=Allowlist())
    return ids, did, trust


def beat(ids, did, name, roles, neighbors, seq=1, at=NOW):
    return make_heartbeat(ids[name], roles=roles, seq=seq, neighbors=[did[n] for n in neighbors],
                          clock=lambda: at)


def test_a_link_needs_both_ends(mesh):
    ids, did, trust = mesh
    db = LinkStateDB(trust, fail_after=5)
    db.merge(beat(ids, did, "a", ["forward"], ["b", "c"]), now=NOW)
    db.merge(beat(ids, did, "b", ["forward"], ["a"]), now=NOW)
    db.merge(beat(ids, did, "c", ["forward"], []), now=NOW)  # c never agreed to a
    adj = db.adjacency(NOW)
    assert adj[did["a"]] == {did["b"]} and adj[did["c"]] == set()


def test_liveness_is_this_nodes_clock_and_seq_must_grow(mesh):
    ids, did, trust = mesh
    db = LinkStateDB(trust, fail_after=5)
    assert db.merge(beat(ids, did, "a", [], [], seq=2, at=NOW + 60), now=NOW)  # within skew
    assert not db.merge(beat(ids, did, "a", [], [], seq=2), now=NOW + 1)      # not newer
    assert not db.merge(beat(ids, did, "a", [], [], seq=1), now=NOW + 1)
    assert db.alive(did["a"], NOW + 5) and not db.alive(did["a"], NOW + 6)    # its own timestamp is ignored


def test_serve_claims_count_only_for_deciders(mesh):
    ids, did, trust = mesh
    db = LinkStateDB(trust, fail_after=5)
    db.merge(beat(ids, did, "a", ["serve", "forward"], []), now=NOW)
    db.merge(beat(ids, did, "s", ["serve"], []), now=NOW)
    assert db.roles(did["a"]) == {"forward"} and db.roles(did["s"]) == {"serve"}


def test_paths_use_only_transit_nodes_and_honour_avoid():
    adj = {"s": {"a", "p", "q"}, "a": {"s", "b"}, "b": {"a", "p"}, "p": {"s", "b", "q"}, "q": {"s", "p"}}
    transit = {"s", "a", "b"}
    assert shortest_path(adj, "s", "p", transit=transit) == ("s", "p")
    assert shortest_path(adj, "s", "p", transit=transit, avoid={"s"}) == ("s", "p")  # the source is not interior
    no_direct = {k: v - {"p"} if k == "s" else v for k, v in adj.items()}
    no_direct["p"] = adj["p"] - {"s"}
    assert shortest_path(no_direct, "s", "p", transit=transit) == ("s", "a", "b", "p")  # never via model node q
    assert shortest_path(no_direct, "s", "p", transit=transit, avoid={"b"}) is None
    assert shortest_path(adj, "s", "p", transit=transit, avoid={"p"}) is None


def test_ties_break_the_same_way_everywhere():
    adj = {"s": {"x", "y"}, "x": {"s", "p"}, "y": {"s", "p"}, "p": {"x", "y"}}
    assert shortest_path(adj, "s", "p", transit={"x", "y"}) == ("s", "x", "p")


def test_repairs_bypass_the_down_node_and_recruit_for_an_orphan(mesh):
    ids, did, trust = mesh
    trust = TrustRoots(mesh=trust.mesh, users=trust.users, deciders=trust.deciders, coordinators=trust.coordinators,
                       brokers=Allowlist({did["k"]}), operators=trust.operators)
    db = LinkStateDB(trust, fail_after=5)
    # p(model) - a - s(serve) - b - q(model); d is a standby forwarder hanging off k, the key broker
    for name, roles, nbrs in (("p", ["model"], ["a"]), ("a", ["forward"], ["p", "s"]),
                              ("s", ["serve", "forward"], ["a", "b"]), ("d", ["forward"], ["k"]),
                              ("k", ["broker"], ["d"]), ("b", ["forward"], ["s", "q"]),
                              ("q", ["model"], ["b"])):
        db.merge(beat(ids, did, name, roles, nbrs), now=NOW)
    rep = plan_repairs(db, NOW, down={did["a"]}, standby=[did["d"]], brokers=[did["k"]])
    assert rep.bypass == [tuple(sorted((did["p"], did["s"])))]
    assert rep.orphans == [did["p"]]
    assert rep.central == sorted((did["p"], did["s"]))  # what a left behind links to the central server
    assert rep.recruit == [(did["d"], did["p"])]  # d already hangs off the key broker
    # two model nodes are never linked to each other
    rep = plan_repairs(db, NOW, down={did["b"]}, standby=[])
    assert all(did["q"] not in pair or did["s"] in pair for pair in rep.bypass)


def test_a_recruit_is_anchored_to_the_key_broker(mesh):
    ids, did, trust = mesh
    trust = TrustRoots(mesh=trust.mesh, users=trust.users, deciders=trust.deciders, coordinators=trust.coordinators,
                       brokers=Allowlist({did["k"]}), operators=trust.operators)
    db = LinkStateDB(trust, fail_after=5)
    for name, roles, nbrs in (("p", ["model"], ["a", "k"]), ("a", ["forward"], ["p"]), ("d", ["forward"], ["c"]),
                              ("c", ["forward"], ["d"]), ("k", ["broker"], ["p"])):
        db.merge(beat(ids, did, name, roles, nbrs), now=NOW)
    rep = plan_repairs(db, NOW, down={did["a"]}, standby=[did["d"]], brokers=[did["k"]])
    assert rep.recruit == [(did["d"], did["p"]), (did["d"], did["k"])]  # the new P2P: model node and API key
