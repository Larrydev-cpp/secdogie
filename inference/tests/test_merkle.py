"""The Merkle DAG of heartbeats: RFC 6962 roots, chain forks isolated (not
locked down), links that stop being witnessed, and ledger anti-entropy."""
from __future__ import annotations

import pytest

pytest.importorskip("nacl")

import sim  # noqa: E402
from secdogie_identity import Identity  # noqa: E402
from secdogie_inference import (  # noqa: E402
    MODE_DEGRADED,
    MODE_LOCKDOWN,
    MODE_NORMAL,
    OUTCOME_ACCEPTED,
    make_incident,
    make_reset,
)
from secdogie_inference.lease import make_lease, new_canary  # noqa: E402
from secdogie_inference.merkle import EMPTY_ROOT, merkle_root  # noqa: E402
from secdogie_inference.mutiny import (  # noqa: E402
    DECIDER_SELF_REPORTED,
    NODE_HEARTBEAT_FORK,
    fork_evidence,
    node_target,
    verify_incident,
)
from secdogie_inference.protocol import (  # noqa: E402
    HEAD,
    HEARTBEAT,
    MAX_LEASES,
    MAX_NEIGHBORS,
    digest,
    frame,
    make_heartbeat,
)

# RFC 6962 / Certificate Transparency reference tree heads for the first n of these leaves.
CT_LEAVES = [b"", b"\x00", b"\x10", b"\x20\x21", b"\x30\x31", b"\x40\x41\x42\x43",
             bytes(range(0x50, 0x58)), bytes(range(0x60, 0x70))]
CT_ROOTS = [
    "6e340b9cffb37a989ca544e6bb780a2c78901d3fb33738768511a30617afa01d",
    "fac54203e7cc696cf0dfcb42c92a1d9dbaf70ad9e621f4bd8d98662f00e3c125",
    "aeb6bcfe274b70a14fb067a5e5578264db0fa9b51af5e0ba159158f329e06e77",
    "d37ee418976dd95753c1c73862b9398fa2a2cf9b4ff0fdfe8b30cd95209614b7",
    "4e3bbb1f7b478dcfe71fb631631519a3bca12c9aefca1612bfce4c13a86264d4",
    "76e67dadbcdf1e10e1b74ddc608abd2f98dfb16fbce75277b5232a127f2087ef",
    "ddb89be403809e325750d3d263cd78929c2942b7942a34b77e122c9594a74c8c",
    "5dc9da79a70659a9ad559cb701ded9a2ab9d823aad2f4960cfe370eff4604328",
]


@pytest.mark.parametrize("n", range(1, 9))
def test_merkle_root_matches_rfc6962(n):
    assert merkle_root(CT_LEAVES[:n]) == CT_ROOTS[n - 1]


def test_merkle_root_basics():
    assert merkle_root([]) == EMPTY_ROOT
    assert merkle_root(["a|b"]) == merkle_root([b"a|b"])
    assert merkle_root(["x", "y"]) != merkle_root(["y", "x"])  # order is the caller's: the ledger sorts


def test_a_heartbeat_with_every_neighbour_and_lease_fits_one_frame():
    me, broker = Identity.generate(), Identity.generate()
    neighbors = {Identity.generate().did: (2**62, "ab" * (HEAD // 2)) for _ in range(MAX_NEIGHBORS)}
    leases = [make_lease(broker, me.did, f"vendor/model-{i}", key_version=7, canary=new_canary())
              for i in range(MAX_LEASES)]
    hb = make_heartbeat(me, roles=["forward", "model", "serve"], seq=2**62, neighbors=neighbors, prev="1" * 64,
                        leases=leases, ledger_root="2" * 64)
    assert hb["type"] == HEARTBEAT and len(hb["neighbors"]) == MAX_NEIGHBORS
    assert len(frame("flood", obj=hb)) < 12_000  # far inside one datagram, with room for the transport


# -- on the mesh ------------------------------------------------------------------------------

def restored_backup_beat(arc, name):
    """What a second copy of ``name``, restored from a backup, would send next:
    the same seq and prev as the real node's next heartbeat, other contents."""
    node = arc.part(name)
    return make_heartbeat(arc.ids[name], roles=sorted(node.roles), seq=node._seq + 1, prev=node._prev,
                          neighbors=[], clock=arc.net.clock)


def test_a_node_whose_key_runs_twice_is_isolated_without_lockdown():
    arc = sim.Arc(entries=("S2",))
    d = arc.did
    twin = restored_backup_beat(arc, "S1")
    arc.S1.send(d["S2"], "flood", obj=twin)  # the copy's heartbeat reaches S2 first
    arc.net.settle(2)                         # then the real S1 beats: same prev, different contents

    for part in arc.everyone():
        assert part.ledger.isolated() == {d["S1"]} and part.ledger.excludes_node(d["S1"])
        assert not part.ledger.lockdown and part.mode() != MODE_LOCKDOWN
    inc = arc.C.ledger.signed_incidents()
    assert [i["kind"] for i in inc] == [NODE_HEARTBEAT_FORK] and len(arc.C.ledger.held_ids()) == 1
    assert arc.S3.machine.history[-1].cause == "isolation" and arc.S3.machine.state == MODE_DEGRADED
    assert arc.S3.health_snapshot()[d["S1"]].isolated

    arc.net.settle(6)  # drawing 2 around S1: the bypass and the central server
    out = arc.ask([sim.ALPHA])
    assert out.status == OUTCOME_ACCEPTED and d["S1"] not in {x for leg in arc.S2.noted("decide")[-1]["legs"]
                                                             for x in leg}

    ids = arc.C.ledger.held_ids()
    arc.C.publish(make_reset(arc.ids["O"], [node_target(d["S1"])], ids, clock=arc.net.clock))
    arc.net.settle(10)
    assert {p.mode() for p in arc.everyone()} == {MODE_NORMAL}


def test_fork_evidence_is_one_incident_whoever_reports_it():
    arc = sim.Arc()
    d = arc.did
    a = restored_backup_beat(arc, "S4")
    b = make_heartbeat(arc.ids["S4"], roles=["forward"], seq=a["seq"], prev=a["prev"], neighbors=[d["R"]],
                       clock=arc.net.clock)
    ev = fork_evidence(a, b)
    one = verify_incident(make_incident(arc.ids["S3"], NODE_HEARTBEAT_FORK, ev, clock=arc.net.clock), arc.trust)
    two = verify_incident(make_incident(arc.ids["R"], NODE_HEARTBEAT_FORK, ev, clock=arc.net.clock), arc.trust)
    assert one.incident_id == two.incident_id and one.targets == {node_target(d["S4"])}
    backwards = {"a": ev["b"], "b": ev["a"]}
    assert verify_incident(make_incident(arc.ids["S3"], NODE_HEARTBEAT_FORK, backwards, clock=arc.net.clock),
                           arc.trust) is None
    successor = make_heartbeat(arc.ids["S4"], roles=["forward"], seq=a["seq"] + 1, prev=digest(a), neighbors=[],
                               clock=arc.net.clock)
    assert verify_incident(make_incident(arc.ids["S3"], NODE_HEARTBEAT_FORK, fork_evidence(a, successor),
                                         clock=arc.net.clock), arc.trust) is None  # a chain, not a fork


def test_a_link_whose_end_stops_hearing_the_other_leaves_every_route():
    arc = sim.Arc(entries=("S2",))
    d = arc.did

    def own_heartbeat_from_s1(src, f):
        obj = f.get("obj") if f["k"] == "flood" else None
        return src == d["S1"] and isinstance(obj, dict) and obj.get("node") == d["S1"]

    arc.net.drop[d["S2"]] = own_heartbeat_from_s1  # S2 stops hearing S1 directly
    arc.net.settle(6)
    now = arc.net.clock()
    for part in (arc.S2, arc.S3, arc.C):
        assert part.lsdb.alive(d["S1"], now)                      # S1's heartbeats still arrive (via C)
        assert d["S2"] not in part.lsdb.adjacency(now).get(d["S1"], set())
    assert not arc.S2.lsdb.witnessed(d["S2"], d["S1"])            # S2's own heartbeat says so
    decided = len(arc.S2.noted("decide"))
    assert arc.ask([sim.ALPHA]).status != OUTCOME_ACCEPTED       # L is reached only through S1
    assert len(arc.S2.noted("decide")) == decided                # and no leg was sent into the dead link

    del arc.net.drop[d["S2"]]
    arc.net.settle(3)
    assert arc.ask([sim.ALPHA]).status == OUTCOME_ACCEPTED


def test_a_node_that_missed_the_incidents_catches_up_from_its_neighbours_roots():
    arc = sim.Arc(entries=("S2",))
    d = arc.did
    asked = []

    def spy(src, f):  # drops nothing; notes what S4 asks for by id
        if src == d["S4"] and f["k"] == "inc?":
            asked.append(f["ids"])
        return False

    for did in d.values():
        arc.net.drop[did] = spy
    arc.net.down.add(d["S4"])
    for n in range(3):  # more than one directory answer carries
        ev = {"reporter": d["S2"], "proposal": {"n": n}, "violation": "quorum-downgrade"}
        arc.S2.publish(make_incident(arc.ids["S2"], DECIDER_SELF_REPORTED, ev, clock=arc.net.clock))
    arc.net.run()
    held = set(arc.S3.ledger.held_ids())
    assert len(held) == 3 and arc.S3.mode() == MODE_LOCKDOWN

    arc.net.down.discard(d["S4"])  # back, with an empty ledger and a different root
    arc.net.until(lambda: set(arc.S4.ledger.held_ids()) == held, limit=20)
    assert arc.S4.mode() == MODE_LOCKDOWN and arc.S4.ledger.root() == arc.S3.ledger.root()
    assert asked and set(asked[0]) <= held  # what the directory did not carry was fetched by id
