"""Link state, paths and repairs: links need both ends and both ends'
witness, liveness is local, chains that fork are caught, paths stay on transit
nodes and are the same on every node."""
from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_inference.protocol import GENESIS, HEAD, TrustRoots, digest, make_heartbeat  # noqa: E402
from secdogie_inference.topology import LinkStateDB, plan_repairs, shortest_path  # noqa: E402

NOW = 1_000_000.0


@pytest.fixture
def mesh():
    ids = {n: Identity.generate() for n in ("a", "b", "c", "d", "k", "p", "q", "s")}
    did = {n: i.did for n, i in ids.items()}
    trust = TrustRoots(mesh=Allowlist(set(did.values())), users=Allowlist(), deciders=Allowlist({did["s"]}),
                       coordinators=Allowlist(), brokers=Allowlist(), operators=Allowlist())
    return ids, did, trust


def beat(ids, did, name, roles, neighbors, seq=1, at=NOW, prev=GENESIS, heard=None):
    """``heard``: name -> the heartbeat of that neighbour this node heard directly."""
    heard = heard or {}
    witness = {did[n]: ((heard[n]["seq"], digest(heard[n])[:HEAD]) if n in heard else (0, "")) for n in neighbors}
    return make_heartbeat(ids[name], roles=roles, seq=seq, neighbors=witness, prev=prev, clock=lambda: at)


def settle(db, ids, did, spec, at=NOW):
    """Two rounds of heartbeats: the second witnesses the first, as a running mesh does."""
    first = {name: beat(ids, did, name, roles, nbrs, seq=1, at=at) for name, roles, nbrs in spec}
    for hb in first.values():
        db.merge(hb, now=at)
    second = {}
    for name, roles, nbrs in spec:
        heard = {n: first[n] for n in nbrs if n in first}
        second[name] = beat(ids, did, name, roles, nbrs, seq=2, at=at, prev=digest(first[name]), heard=heard)
        db.merge(second[name], now=at)
    return first, second


def test_a_link_needs_both_ends(mesh):
    ids, did, trust = mesh
    db = LinkStateDB(trust, fail_after=5)
    settle(db, ids, did, (("a", ["forward"], ["b", "c"]), ("b", ["forward"], ["a"]),
                          ("c", ["forward"], [])))  # c never agreed to a
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
    settle(db, ids, did, (("p", ["model"], ["a"]), ("a", ["forward"], ["p", "s"]),
                          ("s", ["serve", "forward"], ["a", "b"]), ("d", ["forward"], ["k"]),
                          ("k", ["broker"], ["d"]), ("b", ["forward"], ["s", "q"]),
                          ("q", ["model"], ["b"])))
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
    settle(db, ids, did, (("p", ["model"], ["a", "k"]), ("a", ["forward"], ["p"]), ("d", ["forward"], ["c"]),
                          ("c", ["forward"], ["d"]), ("k", ["broker"], ["p"])))
    rep = plan_repairs(db, NOW, down={did["a"]}, standby=[did["d"]], brokers=[did["k"]])
    assert rep.recruit == [(did["d"], did["p"]), (did["d"], did["k"])]  # the new P2P: model node and API key


# -- the Merkle DAG of heartbeats --------------------------------------------------------

def rounds(db, ids, did, spec, n, *, deaf=(), deaf_from=1):
    """``n`` rounds of heartbeats; each node witnesses what it heard from each
    neighbour the round before, except that from ``deaf_from`` on, node x no
    longer hears y for each (x, y) in ``deaf``."""
    latest, heard_by = {}, {}
    for r in range(1, n + 1):
        out = {}
        for name, roles, nbrs in spec:
            heard = {y: heard_by[(name, y)] for y in nbrs if (name, y) in heard_by}
            prev = digest(latest[name]) if name in latest else GENESIS
            out[name] = beat(ids, did, name, roles, nbrs, seq=r, prev=prev, heard=heard)
        for name, hb in out.items():
            db.merge(hb, now=NOW)
            latest[name] = hb
        for name, _roles, nbrs in spec:
            for y in nbrs:
                if not ((name, y) in deaf and r >= deaf_from):
                    heard_by[(name, y)] = latest[y]
    return latest


def test_a_link_needs_each_end_to_witness_the_other_recently(mesh):
    ids, did, trust = mesh
    spec = (("a", ["forward"], ["b", "c"]), ("b", ["forward"], ["a", "d"]), ("c", ["forward"], ["a", "d"]),
            ("d", ["forward"], ["b", "c"]))
    via, other = sorted(("b", "c"), key=lambda n: did[n])  # ties break on DIDs: the healthy route uses `via`
    transit = {did["b"], did["c"]}

    db = LinkStateDB(trust, fail_after=60, max_witness_lag=3)
    rounds(db, ids, did, spec, 8)
    assert shortest_path(db.adjacency(NOW), did["a"], did["d"], transit=transit) == (did["a"], did[via], did["d"])

    # `via` keeps beating and lists a, but from round 3 on it no longer hears a directly
    db = LinkStateDB(trust, fail_after=60, max_witness_lag=3)
    rounds(db, ids, did, spec, 8, deaf={(via, "a")}, deaf_from=3)
    assert db.witnessed(did["a"], did[via]) and not db.witnessed(did[via], did["a"])
    adj = db.adjacency(NOW)
    assert did[via] not in adj[did["a"]] and did[via] in adj[did["d"]]  # only that link drops
    assert shortest_path(adj, did["a"], did["d"], transit=transit) == (did["a"], did[other], did["d"])


def test_a_witness_must_name_the_heartbeat_it_claims(mesh):
    ids, did, trust = mesh
    db = LinkStateDB(trust, fail_after=60)
    first, second = settle(db, ids, did, (("a", ["forward"], ["b"]), ("b", ["forward"], ["a"])))
    assert db.witnessed(did["a"], did["b"])
    lie = make_heartbeat(ids["a"], roles=["forward"], seq=3, prev=digest(second["a"]),
                         neighbors={did["b"]: (2, "f" * HEAD)}, clock=lambda: NOW)
    db.merge(lie, now=NOW)
    assert not db.witnessed(did["a"], did["b"]) and did["b"] not in db.adjacency(NOW)[did["a"]]


def test_a_chain_fork_is_caught_and_a_restart_is_not(mesh):
    ids, did, trust = mesh
    db = LinkStateDB(trust, fail_after=60)
    one = beat(ids, did, "a", [], [], seq=5)
    two = beat(ids, did, "a", [], [], seq=6, prev=digest(one))
    assert db.merge(one, now=NOW) and db.merge(two, now=NOW) and db.take_forks() == []
    twin = beat(ids, did, "a", ["forward"], [], seq=6, prev=digest(one))   # a restored backup's next beat
    assert not db.merge(twin, now=NOW)
    assert db.take_forks() == [(two, twin)] and db.take_forks() == []
    same_seq = beat(ids, did, "a", ["forward"], [], seq=5, prev=digest(two))
    assert not db.merge(same_seq, now=NOW) and len(db.take_forks()) == 1
    restart = beat(ids, did, "a", [], [], seq=100, prev=GENESIS)            # a new chain is not a fork
    again = beat(ids, did, "a", ["forward"], [], seq=101, prev=GENESIS)
    assert db.merge(restart, now=NOW) and db.merge(again, now=NOW) and db.take_forks() == []
    assert db.gaps[did["a"]] == 2  # each GENESIS broke the chain it followed
