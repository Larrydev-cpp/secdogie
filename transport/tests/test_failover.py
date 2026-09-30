"""Direct first, relay as the fallback (FailoverTransport), over real UDP on
127.0.0.1: when the direct path is blocked, messages flow through a relay both
clients lease with; once a peer is heard directly, the relay goes unused; when
the direct path goes quiet, the relay carries traffic again."""
from __future__ import annotations

import queue

import pytest

pytest.importorskip("nacl")

from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_transport import (  # noqa: E402
    DirectUDPTransport,
    Endpoint,
    FailoverTransport,
    MembershipView,
    PeerIdentity,
    RelayClient,
    RelayService,
    Session,
    UDPChannel,
)
from secdogie_transport.membership import ROLE_RELAY, sign_record  # noqa: E402


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _wait(q, timeout=3.0):
    return q.get(timeout=timeout)


class Relay:
    def __init__(self, allow):
        self.identity = Identity.generate()
        self.channel = UDPChannel("127.0.0.1", 0)
        self.transport = DirectUDPTransport(self.identity, self.channel, allowlist=allow)
        self.service = RelayService(self.transport, allowlist=allow)
        self.record = sign_record(self.identity, [Endpoint("local", *self.channel.address)], last_seen=1.0,
                                  roles=[ROLE_RELAY])


class Client:
    def __init__(self, identity, peer_allow, relay: Relay, clock):
        self.identity = identity
        self.channel = UDPChannel("127.0.0.1", 0)
        self.direct = DirectUDPTransport(identity, self.channel, allowlist=peer_allow)
        view = MembershipView(allowlist=Allowlist({relay.identity.did}))
        assert view.merge_record(relay.record)
        self.relay_client = RelayClient(self.direct, view, allowlist=Allowlist({relay.identity.did}))
        self.link = FailoverTransport(self.direct, self.relay_client, [relay.identity.did], clock=clock)
        self.inbox: queue.Queue = queue.Queue()
        me = Session("s", PeerIdentity(identity.did, ""), active=Endpoint("local", *self.channel.address))
        self.link.register(me, lambda frm, msg: self.inbox.put((frm, msg)))

    def lease(self):
        self.relay_client.refresh()


@pytest.fixture
def world():
    a_id, b_id = Identity.generate(), Identity.generate()
    relay = Relay(Allowlist({a_id.did, b_id.did}))
    clock = Clock()
    a = Client(a_id, Allowlist({b_id.did}), relay, clock)
    b = Client(b_id, Allowlist({a_id.did}), relay, clock)
    for c in (a, b):
        c.lease()
    import time

    deadline = time.time() + 3
    while time.time() < deadline and not (a.relay_client.relays() and b.relay_client.relays()):
        time.sleep(0.02)
    assert a.relay_client.relays() and b.relay_client.relays(), "leases not granted"
    try:
        yield a, b, relay, clock
    finally:
        for ch in (a.channel, b.channel, relay.channel):
            ch.close()


def test_a_blocked_direct_path_falls_back_to_the_relay(world):
    a, b, relay, _ = world
    blocked = UDPChannel("127.0.0.1", 0)  # a port that never answers for b
    a.direct.set_peer_endpoint(b.identity.did, *blocked.address)
    try:
        assert a.link.route(a.identity.did, b.identity.did, b"over the relay")
        assert _wait(b.inbox) == (a.identity.did, b"over the relay")
        # b never heard a directly, and has no endpoint for it: the reply goes through the relay too
        assert b.link.route(b.identity.did, a.identity.did, b"and back")
        assert _wait(a.inbox) == (b.identity.did, b"and back")
        assert relay.service.stats["forwarded"] >= 2
    finally:
        blocked.close()


def test_once_heard_directly_the_relay_is_not_used_until_the_path_goes_quiet(world):
    a, b, relay, clock = world
    a.direct.set_peer_endpoint(b.identity.did, *b.channel.address)
    b.direct.set_peer_endpoint(a.identity.did, *a.channel.address)
    a.link.route(a.identity.did, b.identity.did, b"hello")  # both paths: nothing heard yet
    got = {_wait(b.inbox), _wait(b.inbox)}  # arrives twice: direct and relayed (the session dedups)
    assert got == {(a.identity.did, b"hello")}
    assert b.link.direct_is_fresh(a.identity.did)
    forwarded = relay.service.stats["forwarded"]
    b.link.route(b.identity.did, a.identity.did, b"direct only")
    assert _wait(a.inbox) == (b.identity.did, b"direct only")
    with pytest.raises(queue.Empty):
        a.inbox.get(timeout=0.3)  # no second, relayed copy
    assert relay.service.stats["forwarded"] == forwarded
    clock.now += 60  # the direct path went quiet
    assert not b.link.direct_is_fresh(a.identity.did)
    b.link.route(b.identity.did, a.identity.did, b"both again")
    assert {_wait(a.inbox), _wait(a.inbox)} == {(b.identity.did, b"both again")}


def test_a_failover_transport_needs_a_relay(world):
    a, *_ = world
    with pytest.raises(ValueError):
        FailoverTransport(a.direct, a.relay_client, [])


def test_route_via_refuses_a_relay_off_the_list(world):
    a, b, *_ = world
    stranger = Identity.generate().did
    assert a.relay_client.route_via(stranger, b.identity.did, b"x") is False
    assert a.relay_client.route_via(a.identity.did, b.identity.did, b"x") is False  # not itself


def test_from_records_takes_only_valid_relay_records(world):
    a, b, relay, _ = world
    link = FailoverTransport.from_records(a.direct, [relay.record])
    assert link.relay_dids == [relay.identity.did]
    not_a_relay = sign_record(b.identity, [Endpoint("local", "127.0.0.1", 9)], last_seen=1.0)
    with pytest.raises(ValueError, match="relay role"):
        FailoverTransport.from_records(a.direct, [not_a_relay])
    forged = dict(relay.record, last_seen=2.0)  # altered after signing
    with pytest.raises(ValueError):
        FailoverTransport.from_records(a.direct, [forged])
