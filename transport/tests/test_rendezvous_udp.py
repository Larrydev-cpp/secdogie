"""Rendezvous over real UDP on 127.0.0.1 (T3): a node serves the rendezvous role
on its own transport (`RendezvousService`), other nodes register and look each
other up through it (`RendezvousLink`), and a peer found that way is reachable
directly -- no address configured by hand."""
from __future__ import annotations

import queue
import time

import pytest

pytest.importorskip("nacl")

from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_transport import (  # noqa: E402
    DirectUDPTransport,
    Endpoint,
    PeerIdentity,
    RendezvousLink,
    RendezvousService,
    Session,
    UDPChannel,
)
from secdogie_transport.membership import ROLE_RELAY, ROLE_RENDEZVOUS, sign_record  # noqa: E402


def _wait(pred, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


class Node:
    def __init__(self, identity, peers):
        self.identity = identity
        self.channel = UDPChannel("127.0.0.1", 0)
        self.transport = DirectUDPTransport(identity, self.channel, allowlist=Allowlist(peers))
        self.inbox: queue.Queue = queue.Queue()
        me = Session("s", PeerIdentity(identity.did, ""), active=Endpoint("local", *self.channel.address))
        self.transport.register(me, lambda frm, msg: self.inbox.put((frm, msg)))


class Rendezvous:
    def __init__(self, allow):
        self.identity = Identity.generate()
        self.channel = UDPChannel("127.0.0.1", 0)
        self.transport = DirectUDPTransport(self.identity, self.channel, allowlist=allow)
        self.service = RendezvousService(self.transport, allowlist=allow)
        self.record = sign_record(self.identity, [Endpoint("local", *self.channel.address)], last_seen=1.0,
                                  roles=[ROLE_RENDEZVOUS])


@pytest.fixture
def mesh():
    a_id, b_id, stranger_id = Identity.generate(), Identity.generate(), Identity.generate()
    rv = Rendezvous(Allowlist({a_id.did, b_id.did}))
    a = Node(a_id, {b_id.did})
    b = Node(b_id, {a_id.did})
    stranger = Node(stranger_id, {a_id.did})
    links = []

    def link(node, records=None):
        lk = RendezvousLink.from_records(node.transport, records or [rv.record])
        links.append(lk)
        return lk

    try:
        yield rv, a, b, stranger, link
    finally:
        for lk in links:
            lk.close()
        for ch in (a.channel, b.channel, stranger.channel, rv.channel):
            ch.close()


def test_a_peer_found_by_did_is_reachable_directly(mesh):
    rv, a, b, _, link = mesh
    la, lb = link(a), link(b)
    la.register([])  # a announces nothing itself: the rendezvous stamps where it sees a from
    assert _wait(lambda: rv.identity.did in la.reflexive)
    assert la.reflexive[rv.identity.did].port == a.channel.address[1]
    found = lb.lookup(a.identity.did)
    assert found is not None and found.best().port == a.channel.address[1]
    b.transport.set_peer_endpoint(a.identity.did, found.best().host, found.best().port)
    assert b.transport.route(b.identity.did, a.identity.did, b"found you")
    assert a.inbox.get(timeout=3) == (b.identity.did, b"found you")


def test_an_unregistered_peer_is_not_found(mesh):
    _, a, b, _, link = mesh
    link(a)
    assert link(b).lookup(a.identity.did, timeout=1.0) is None


def test_a_did_off_the_rendezvous_allowlist_is_neither_indexed_nor_answered(mesh):
    rv, a, b, stranger, link = mesh
    ls = link(stranger)
    ls.register([])
    time.sleep(0.3)
    assert ls.reflexive == {}  # no ack
    assert rv.service.server.known(stranger.identity.did) is None
    assert ls.lookup(a.identity.did, timeout=0.5) is None  # its lookups get no answer either
    assert rv.service.stats["dropped_unauthenticated"] >= 2


def test_lookup_falls_through_to_a_rendezvous_that_knows_the_peer(mesh):
    rv, a, b, _, link = mesh
    other = Rendezvous(Allowlist({a.identity.did, b.identity.did}))
    try:
        link(a, [rv.record]).register([])  # a registers only with the first
        assert _wait(lambda: rv.service.server.known(a.identity.did) is not None)
        found = link(b, [other.record, rv.record]).lookup(a.identity.did, timeout=1.0)
        assert found is not None and found.best().port == a.channel.address[1]
    finally:
        other.channel.close()


def test_a_stopped_rendezvous_answers_nothing(mesh):
    rv, a, b, _, link = mesh
    link(a).register([])
    assert _wait(lambda: rv.service.server.known(a.identity.did) is not None)
    rv.service.stop()
    assert link(b).lookup(a.identity.did, timeout=0.5) is None


def test_start_keeps_registering(mesh):
    rv, a, _, _, link = mesh
    stop = link(a).start(every=0.05)
    try:
        assert _wait(lambda: rv.service.stats["registered"] >= 3)
    finally:
        stop.set()


def test_from_records_takes_only_valid_rendezvous_records(mesh):
    rv, a, _, _, _ = mesh
    relay_only = sign_record(rv.identity, [Endpoint("local", "127.0.0.1", 9)], last_seen=1.0, roles=[ROLE_RELAY])
    with pytest.raises(ValueError, match="rendezvous role"):
        RendezvousLink.from_records(a.transport, [relay_only])
    no_endpoint = sign_record(rv.identity, [], last_seen=1.0, roles=[ROLE_RENDEZVOUS])
    with pytest.raises(ValueError, match="endpoint"):
        RendezvousLink.from_records(a.transport, [no_endpoint])
    forged = dict(rv.record, last_seen=2.0)  # altered after signing
    with pytest.raises(ValueError):
        RendezvousLink.from_records(a.transport, [forged])
    with pytest.raises(ValueError):
        RendezvousLink(a.transport, {})


def test_a_service_needs_an_allowlist(mesh):
    _, a, *_ = mesh
    with pytest.raises(ValueError):
        RendezvousService(a.transport, allowlist=None)
