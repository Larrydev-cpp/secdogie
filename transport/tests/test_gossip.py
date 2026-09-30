"""Membership gossip on the wire (T4), over real UDP on 127.0.0.1: nodes that
start knowing a single bootstrap record learn the whole mesh; only mesh peers
gossip; records are batched to fit a datagram and a forged one never lands."""
from __future__ import annotations

import json
import random
import time

import pytest

pytest.importorskip("nacl")

from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_transport import (  # noqa: E402
    ChannelMux,
    DirectUDPTransport,
    Endpoint,
    MembershipGossip,
    MembershipView,
    PeerIdentity,
    Session,
    UDPChannel,
)
from secdogie_transport.gossip import MEMBERSHIP_CHANNEL  # noqa: E402
from secdogie_transport.membership import sign_record  # noqa: E402


def _wait(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


class Node:
    def __init__(self, identity, mesh, *, hears=None, seed=0, max_bytes=24_000):
        self.identity = identity
        self.channel = UDPChannel("127.0.0.1", 0)
        self.transport = DirectUDPTransport(identity, self.channel, allowlist=hears or mesh)
        self.mux = ChannelMux(self.transport, Session("n", PeerIdentity(identity.did, ""),
                                                      active=Endpoint("local", *self.channel.address)))
        self.view = MembershipView(allowlist=mesh)
        self.learned = []
        self.gossip = MembershipGossip(self.mux, self.view, peers=mesh, self_record=self.record,
                                       on_learn=self.learn, rng=random.Random(seed), max_bytes=max_bytes)

    def record(self):
        return sign_record(self.identity, [Endpoint("local", *self.channel.address)], last_seen=time.time())

    def learn(self, rec):
        self.learned.append(rec.did)
        if rec.did != self.identity.did and self.transport.peer_endpoint(rec.did) is None:
            best = rec.endpoints.best()
            self.transport.set_peer_endpoint(rec.did, best.host, best.port)

    def knows(self, *nodes):
        return all(self.view.get(n.identity.did) is not None for n in nodes)

    def close(self):
        self.gossip.close()
        self.channel.close()


@pytest.fixture
def nodes():
    made = []
    yield made
    for n in made:
        n.close()


def test_a_mesh_bootstrapped_from_one_record_converges(nodes):
    ids = [Identity.generate() for _ in range(4)]
    mesh = Allowlist({i.did for i in ids})
    ns = [Node(i, mesh, seed=k) for k, i in enumerate(ids)]
    nodes.extend(ns)
    hub = ns[0]
    for n in ns[1:]:  # everyone knows only the first node
        n.view.merge_record(hub.record())
        n.learn(n.view.get(hub.identity.did))
    for _ in range(40):
        for n in ns:
            n.gossip.tick()
        if all(n.knows(*ns) for n in ns):
            break
        time.sleep(0.05)
    assert all(n.knows(*ns) for n in ns)
    # a peer learned only through gossip is reachable directly
    a, b = ns[1], ns[2]
    got = []
    b.mux.channel("hello/v1", lambda frm, p: got.append((frm, p)))
    assert a.mux.send(b.identity.did, "hello/v1", b"hi")
    assert _wait(lambda: got == [(a.identity.did, b"hi")])


def test_only_mesh_peers_are_answered(nodes):
    a_id, app_id = Identity.generate(), Identity.generate()
    mesh = Allowlist({a_id.did})
    a = Node(a_id, mesh, hears=Allowlist({a_id.did, app_id.did}))  # the transport also hears an App
    app_ch = UDPChannel("127.0.0.1", 0)
    app_t = DirectUDPTransport(app_id, app_ch, allowlist=Allowlist({a_id.did}))
    app = ChannelMux(app_t, Session("app", PeerIdentity(app_id.did, ""), active=Endpoint("local", *app_ch.address)))
    replies = []
    app.channel(MEMBERSHIP_CHANNEL, lambda frm, p: replies.append(p))
    nodes.append(a)
    try:
        a.gossip.tick()  # a now holds its own record
        app_t.set_peer_endpoint(a_id.did, *a.channel.address)
        assert app.send(a_id.did, MEMBERSHIP_CHANNEL, json.dumps({"kind": "digest", "digest": {}}).encode())
        time.sleep(0.3)
        assert replies == []  # the App is heard by the transport but gets no directory
    finally:
        app_ch.close()


def test_a_bad_digest_or_record_is_ignored(nodes):
    a_id, b_id, c_id = Identity.generate(), Identity.generate(), Identity.generate()
    mesh = Allowlist({a_id.did, b_id.did, c_id.did})
    a, b = Node(a_id, mesh), Node(b_id, mesh)
    nodes.extend([a, b])
    b.transport.set_peer_endpoint(a_id.did, *a.channel.address)
    a.transport.set_peer_endpoint(b_id.did, *b.channel.address)
    a.gossip.tick()
    junk = {"kind": "digest", "digest": {"x": "nan?", a_id.did: [1], b_id.did: float("inf")}}
    assert b.mux.send(a_id.did, MEMBERSHIP_CHANNEL, json.dumps(junk).encode())
    genuine = sign_record(c_id, [Endpoint("local", "127.0.0.1", 9)], last_seen=time.time())
    forged = dict(genuine, endpoints=[{"kind": "local", "host": "203.0.113.9", "port": 9}])
    msg = {"kind": "records", "records": [forged, "junk", 7]}
    assert b.mux.send(a_id.did, MEMBERSHIP_CHANNEL, json.dumps(msg).encode())
    time.sleep(0.3)
    assert a.view.get(c_id.did) is None  # the altered record did not verify
    assert b.view.get(a_id.did) is not None  # the junk digest still got a's records back


def test_records_are_sent_in_datagram_sized_batches(nodes):
    ids = [Identity.generate() for _ in range(12)]
    mesh = Allowlist({i.did for i in ids})
    a, b = Node(ids[0], mesh, max_bytes=900), Node(ids[1], mesh)
    nodes.extend([a, b])
    for other in ids[2:]:  # a holds ten more members' records
        a.view.merge_record(sign_record(other, [Endpoint("local", "127.0.0.1", 9)], last_seen=time.time()))
    sent = []
    real = a.mux.send
    a.mux.send = lambda to, ch, p: sent.append(len(p)) or real(to, ch, p)
    a.transport.set_peer_endpoint(ids[1].did, *b.channel.address)
    b.transport.set_peer_endpoint(ids[0].did, *a.channel.address)
    b.gossip.tick()  # b offers its (empty-ish) digest to... nobody yet: it knows no one
    b.view.merge_record(a.record())
    assert b.gossip.tick() == ids[0].did
    assert _wait(lambda: len(b.view.known()) == 12)
    assert len(sent) > 3 and max(sent) < 2000  # several small messages, not one big one


def test_an_exchange_is_bounded_and_never_targets_itself(nodes):
    a_id, b_id = Identity.generate(), Identity.generate()
    mesh = Allowlist({a_id.did, b_id.did})
    a, b = Node(a_id, mesh), Node(b_id, mesh)
    nodes.extend([a, b])
    a.gossip.tick()
    assert a.gossip.targets() == []  # it knows only itself
    b.view.merge_record(a.record())
    b.learn(b.view.get(a_id.did))
    counts = {"a": 0, "b": 0}
    for name, node in (("a", a), ("b", b)):
        real = node.mux.send

        def counting(to, ch, p, name=name, real=real):
            counts[name] += 1
            return real(to, ch, p)

        node.mux.send = counting
    assert b.gossip.tick() == a_id.did
    assert _wait(lambda: a.knows(b) and b.knows(a))
    time.sleep(0.2)
    settled = dict(counts)
    time.sleep(0.5)
    # at most: b's digest, a's records + counter-digest, b's records. Then quiet.
    assert counts == settled and sum(settled.values()) <= 4
