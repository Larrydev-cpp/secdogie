"""The interception barrier and gossip flood (R1.2b), on real loopback UDP.

A TrustPolicy is duck-typed exactly like an Allowlist, so passing one in place of
the allowlist makes every already-existing .contains gate reject a revoked DID --
on a direct frame, through a relay, and on a journal merge -- with no change to
those code paths. These tests prove that end to end, and that a Master-signed
revocation floods across the mesh while a forged one changes nothing."""
from __future__ import annotations

import itertools
import queue
import time

import pytest

pytest.importorskip("nacl")

from secdogie_identity import (
    Allowlist,
    Identity,
    TrustPolicy,
    cosign,
    create_revocation,
)
from secdogie_identity.revocation import MasterSet
from secdogie_transport import (
    DirectUDPTransport,
    Endpoint,
    MembershipView,
    RelayClient,
    RelayService,
    RevocationGossip,
    Session,
    UDPChannel,
    gossip_round,
)
from secdogie_transport.membership import ROLE_RELAY, announce
from secdogie_transport.peer import PeerIdentity

_seq = itertools.count(1)


def _get(q, timeout=2.0):
    try:
        return q.get(timeout=timeout)
    except queue.Empty:
        return None


def _wait(pred, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline and not pred():
        time.sleep(0.01)
    return pred()


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now

    def advance(self, s):
        self.now += s


class Node:
    """A node whose transport, relay and gossip all share one TrustPolicy."""

    def __init__(self, allow, masters, clock):
        self.identity = Identity.generate()
        self.did = self.identity.did
        allow.add(self.did)
        self.policy = TrustPolicy(allow, masters=masters)
        self.channel = UDPChannel()
        self.transport = DirectUDPTransport(self.identity, self.channel, allowlist=self.policy)
        self.view = MembershipView(allowlist=self.policy)
        self.client = RelayClient(self.transport, self.view, allowlist=self.policy, clock=clock)
        self.gossip = RevocationGossip(self.transport, self.policy, self.view)
        self.service: RelayService | None = None
        self.direct_inbox: queue.Queue = queue.Queue()
        self.relay_inbox: queue.Queue = queue.Queue()
        me = Session("s-" + self.did[-6:], PeerIdentity(self.did, "unused"),
                     active=Endpoint("local", *self.channel.address))
        self.transport.register(me, lambda f, m: self.direct_inbox.put((f, m)))
        self.client.register(me, lambda f, m: self.relay_inbox.put((f, m)))

    def serve(self):
        self.service = RelayService(self.transport, allowlist=self.policy, clock=self.transport_clock)
        return self.service

    def announce_self(self, clock, roles=()):
        # A strictly increasing last_seen so a later announce (e.g. one that now
        # carries relays) wins last-writer-wins over an earlier one.
        return announce(self.identity, [Endpoint("local", *self.channel.address)],
                        last_seen=clock() + next(_seq) * 1e-3, view=self.view, roles=roles,
                        relays=self.client.relays())

    def close(self):
        self.channel.close()


@pytest.fixture
def mesh():
    clock = Clock()
    allow = Allowlist()
    masters_id = Identity.generate()
    masters = MasterSet([masters_id.did])
    nodes: list[Node] = []

    def make():
        n = Node(allow, masters, clock)
        n.transport_clock = clock
        nodes.append(n)
        return n

    try:
        yield make, clock, allow, masters_id
    finally:
        for n in nodes:
            n.close()


def _revocation(master, dids):
    return cosign(master, create_revocation(dids))


def test_a_revoked_sender_is_dropped_on_the_direct_path(mesh):
    make, clock, allow, master = mesh
    a, b = make(), make()
    a.transport.set_peer_endpoint(b.did, *b.channel.address)
    assert a.transport.route(a.did, b.did, b"before")
    assert _get(b.direct_inbox) == (a.did, b"before")

    # B learns A is revoked. A's next frame reaches B's socket but fails the
    # .contains gate inside _open, so it is silently dropped -- no delivery.
    assert b.policy.apply(_revocation(master, [a.did])) == {a.did}
    a.transport.route(a.did, b.did, b"after")
    assert _get(b.direct_inbox, timeout=0.3) is None


def test_a_revoked_destination_is_refused_by_the_relay(mesh):
    make, clock, allow, master = mesh
    a, b, r = make(), make(), make()
    r.serve()
    for n in (a, b, r):
        n.announce_self(clock, roles=[ROLE_RELAY] if n is r else ())
    for x, y in ((a, b), (a, r), (b, r), (b, a), (r, a), (r, b)):
        gossip_round(x.view, y.view)
    b.client.refresh()
    assert _wait(lambda: b.client.relays() == [r.did])
    b.announce_self(clock)
    gossip_round(a.view, b.view)

    assert a.client.route(a.did, b.did, b"via relay")
    assert _get(b.relay_inbox) == (a.did, b"via relay")

    # The relay learns B is revoked; it re-checks the allowlist on every forward,
    # so A's next send is dropped with dropped_unauthorized, never delivered.
    assert r.policy.apply(_revocation(master, [b.did])) == {b.did}
    before = r.service.stats["dropped_unauthorized"]
    a.client.route(a.did, b.did, b"after revoke")
    assert _wait(lambda: r.service.stats["dropped_unauthorized"] == before + 1)
    assert _get(b.relay_inbox, timeout=0.3) is None


def test_a_legit_revocation_floods_the_mesh(mesh):
    make, clock, allow, master = mesh
    a, b, c = make(), make(), make()
    # a knows b, b knows c: a's broadcast reaches b, which re-floods to c.
    a.view.merge_record(b.announce_self(clock))
    b.view.merge_record(c.announce_self(clock))
    victim = make()

    rec = _revocation(master, [victim.did])
    assert a.gossip.announce(rec) == {victim.did}
    assert _wait(lambda: b.policy.is_revoked(victim.did))
    assert _wait(lambda: c.policy.is_revoked(victim.did))  # reached via b's re-flood


def test_a_forged_revocation_is_dropped_and_not_flooded(mesh):
    make, clock, allow, master = mesh
    a, b = make(), make()
    a.view.merge_record(b.announce_self(clock))
    victim = make()

    impostor = Identity.generate()  # not a master
    forged = _revocation(impostor, [victim.did])
    a.gossip.broadcast(forged)  # push it at the mesh directly
    time.sleep(0.2)
    assert not a.policy.is_revoked(victim.did)
    assert not b.policy.is_revoked(victim.did)
    assert victim.did in a.policy.dids()


def test_a_node_does_not_reflood_a_forged_or_duplicate_record(mesh):
    make, clock, allow, master = mesh
    a, b = make(), make()
    a.view.merge_record(b.announce_self(clock))  # a can flood to b
    victim = make()

    sent: list = []
    real_send = a.transport.channel.send
    a.transport.channel.send = lambda h, p, d, _s=real_send, _o=sent: (_o.append(d), _s(h, p, d))[1]

    # A forged record: apply() rejects it, so a must not amplify it onward.
    forged = _revocation(Identity.generate(), [victim.did])
    a.gossip._on_frame({"record": forged}, ("127.0.0.1", 1))
    assert sent == []

    # A genuine record floods once; the same record arriving again is a no-op.
    rec = _revocation(master, [victim.did])
    a.gossip._on_frame({"record": rec}, ("127.0.0.1", 1))
    assert len(sent) == 1
    a.gossip._on_frame({"record": rec}, ("127.0.0.1", 1))  # duplicate
    assert len(sent) == 1

    # announce() likewise floods only on a real change.
    assert a.gossip.announce(rec) == frozenset()  # already applied
    assert len(sent) == 1
