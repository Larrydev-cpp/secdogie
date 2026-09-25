"""Journal replication over a real MeshNode mesh (2B acceptance).

Three nodes on 127.0.0.1 with signed journals: A and B reach each other directly,
C is only reachable over the relay. Every node's events converge to every other
node; an empty node that joins later catches up from its peers (no archive, no
server); and a journal too big for one datagram converges in bounded batches.
"""
from __future__ import annotations

import time

import pytest

pytest.importorskip("nacl")
pytest.importorskip("secdogie_transport")

from secdogie_citadel.journal import Journal  # noqa: E402
from secdogie_citadel.replication import ReplicationPeer, _batches, attach  # noqa: E402
from secdogie_citadel.state import StateStore, record_state  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_transport import Endpoint, HubTransport, MeshNode, UDPChannel  # noqa: E402

TIMERS = dict(keepalive_interval=1.0, dead_after=3.0, probe_timeout=0.6,
              upgrade_retry=1.0, gossip_interval=1.0, sync_interval=1.0)


class Clock:
    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t


class Blockable(UDPChannel):
    def __init__(self):
        super().__init__()
        self.block_in = False

    def start(self, on_datagram):
        super().start(lambda d, a: None if self.block_in else on_datagram(d, a))


def _materialize(journal) -> dict:
    store = StateStore()
    store.merge_events(journal.events())
    return store.materialize()


@pytest.fixture
def mesh():
    allow = Allowlist()
    relay = HubTransport(allowlist=allow)
    clock = Clock()
    made = []

    def make(identity=None):
        ident = identity or Identity.generate()
        allow.add(ident.did)
        node = MeshNode(ident, Blockable(), allowlist=allow, relay=relay, clock=clock, **TIMERS)
        node.journal = Journal(identity=ident, allowlist=allow, clock=clock)
        attach(node, node.journal)
        made.append(node)
        return node

    make.clock = clock
    yield make
    for n in made:
        n.close()


def pump(nodes, pred, clock, *, step=0.2, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for n in nodes:
            n.tick()
        if pred():
            return True
        clock.t += step
        time.sleep(0.01)
    return pred()


def _ep(node):
    return Endpoint("local", *node.direct.channel.address)


def _heads_equal(nodes):
    heads = [n.journal.heads() for n in nodes]
    return all(h == heads[0] for h in heads) and len(heads[0]) == len(nodes)


def test_journals_converge_over_direct_and_relay_paths(mesh):
    a, b, c = mesh(), mesh(), mesh()
    c.direct.channel.block_in = True          # C: relay only
    a.add_peer(b.did, [_ep(b)])
    a.add_peer(c.did, [_ep(c)])
    record_state(a.journal, "goal", "g1", "set", {"by": "a"})
    record_state(b.journal, "task", "t1", "set", {"by": "b"})
    record_state(c.journal, "note", "n1", "set", {"by": "c"})

    assert pump([a, b, c], lambda: _heads_equal([a, b, c]), mesh.clock)
    assert _materialize(a.journal) == _materialize(b.journal) == _materialize(c.journal)
    assert a.path(b.did) == "direct" and a.path(c.did) == "relay"
    for n in (a, b, c):
        assert n.journal.verify() == (True, None)


def test_an_empty_node_catches_up_from_peers(mesh):
    a, b = mesh(), mesh()
    a.add_peer(b.did, [_ep(b)])
    for i in range(5):
        record_state(a.journal, "goal", f"g{i}", "set", {"i": i})
    record_state(b.journal, "task", "t", "set", {})
    assert pump([a, b], lambda: _heads_equal([a, b]), mesh.clock)

    # a brand-new node (its disk lost, or a fresh device) only knows one peer
    c = mesh()
    c.add_peer(a.did, [_ep(a)])
    assert pump([a, b, c], lambda: c.journal.heads() == a.journal.heads(), mesh.clock)
    assert _materialize(c.journal) == _materialize(a.journal)


def test_a_large_journal_converges_in_bounded_batches(mesh):
    a, b = mesh(), mesh()
    a.add_peer(b.did, [_ep(b)])
    for i in range(300):
        record_state(a.journal, "item", f"i{i}", "set", {"text": "x" * 200, "i": i})
    assert pump([a, b], lambda: b.journal.heads() == a.journal.heads(), mesh.clock, timeout=20.0)
    assert b.journal.verify() == (True, None)


def test_batches_are_bounded_and_keep_order():
    events = [{"author": "a", "seq": i, "pad": "p" * 100} for i in range(1, 51)]
    batches = _batches(events, 1_000)
    assert len(batches) > 1 and [e for b in batches for e in b] == events
    import json
    assert all(len(json.dumps(b, separators=(",", ":"))) <= 1_100 for b in batches)
    assert _batches([], 1_000) == [[]] and _batches(events, None) == [events]

    sent = []
    j = Journal(identity=Identity.generate())
    for _ in range(20):
        j.append("k", {"pad": "p" * 200})
    peer = ReplicationPeer(j, lambda to, p: sent.append(p), max_batch_bytes=2_000)
    peer.on_message("did:key:zX", {"kind": "journal_have", "heads": {}, "reply": True})
    assert len(sent) > 1 and sum(len(p["events"]) for p in sent) == 20
