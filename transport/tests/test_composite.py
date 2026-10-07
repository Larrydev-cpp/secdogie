"""CompositeChannel: the UDP socket plus pseudo-address links under one
unchanged DirectUDPTransport -- and ``bound_link_hosts``, which lets a node that
seals its UDP frames use plain signed (v1) frames on a link that is already
encrypted and bound to the peer's DID, and nowhere else."""
from __future__ import annotations

import queue
import threading
import time

import pytest

pytest.importorskip("nacl")

from nacl.public import PrivateKey  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_transport import (  # noqa: E402
    WEBRTC_HOST,
    CompositeChannel,
    DirectUDPTransport,
    Endpoint,
    FailoverTransport,
    PeerIdentity,
    Session,
)
from secdogie_transport.udp import encode_frame  # noqa: E402


class FakeUDP:
    def __init__(self):
        self.address = ("127.0.0.1", 7950)
        self.sent: list[tuple] = []
        self.deliver = None
        self.closed = False

    def start(self, on_datagram):
        self.deliver = on_datagram

    def send(self, host, port, data):
        self.sent.append((host, port, data))

    def close(self):
        self.closed = True


class FakeLink:
    def __init__(self):
        self.sent: list[tuple] = []
        self.deliver = None
        self.closed = False

    def start(self, deliver):
        self.deliver = deliver

    def send(self, link_id, data):
        self.sent.append((link_id, data))

    def close(self):
        self.closed = True


def test_routes_by_pseudo_host_and_never_leaks_one_to_udp():
    udp, link = FakeUDP(), FakeLink()
    ch = CompositeChannel(udp, {WEBRTC_HOST: link})
    got = []
    ch.start(lambda data, addr: got.append((data, addr)))
    assert ch.address == udp.address
    ch.send(WEBRTC_HOST, 3, b"to-link")
    ch.send("@other", 1, b"nowhere")
    ch.send("10.0.0.2", 7950, b"to-udp")
    assert link.sent == [(3, b"to-link")]
    assert udp.sent == [("10.0.0.2", 7950, b"to-udp")]
    link.deliver(b"in", 4)
    udp.deliver(b"in2", ("10.0.0.2", 1))
    assert got == [(b"in", (WEBRTC_HOST, 4)), (b"in2", ("10.0.0.2", 1))]
    ch.close()
    assert link.closed and udp.closed


def test_link_names_must_be_pseudo_hosts():
    with pytest.raises(ValueError):
        CompositeChannel(FakeUDP(), {"webrtc": FakeLink()})


def test_two_sources_are_handed_up_one_at_a_time():
    udp, link = FakeUDP(), FakeLink()
    ch = CompositeChannel(udp, {WEBRTC_HOST: link})
    inside, overlap = [0], []

    def slow(data, addr):
        inside[0] += 1
        overlap.append(inside[0])
        time.sleep(0.01)
        inside[0] -= 1

    ch.start(slow)
    threads = [threading.Thread(target=lambda: [link.deliver(b"x", 1) for _ in range(10)]),
               threading.Thread(target=lambda: [udp.deliver(b"y", ("h", 1)) for _ in range(10)])]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(overlap) == 20 and max(overlap) == 1


# ---- a sealing node over a bound link -------------------------------------------------------


class Side:
    def __init__(self, allow, *, sealed: bool, bound=frozenset({WEBRTC_HOST})):
        self.identity = Identity.generate()
        self.did = self.identity.did
        self.udp, self.link = FakeUDP(), FakeLink()
        self.channel = CompositeChannel(self.udp, {WEBRTC_HOST: self.link})
        key = PrivateKey.generate() if sealed else None
        self.transport = DirectUDPTransport(self.identity, self.channel, allowlist=allow, transport_key=key,
                                            bound_link_hosts=bound)
        self.inbox: queue.Queue = queue.Queue()
        self.transport.register(Session("s", PeerIdentity(self.did, ""), active=Endpoint("local", "127.0.0.1", 1)),
                                lambda frm, msg: self.inbox.put((frm, msg)))


@pytest.fixture
def pair():
    allow = Allowlist()
    node, app = Side(allow, sealed=True), Side(allow, sealed=False)
    allow.add(node.did)
    allow.add(app.did)
    return node, app


def _v1(app, node, msg, ctr):
    return encode_frame(app.identity, node.did, msg, ctr)


def test_a_sealing_node_takes_v1_on_a_bound_link_only(pair):
    node, app = pair
    node.link.deliver(_v1(app, node, b"over webrtc", 10), 1)
    assert node.inbox.get_nowait() == (app.did, b"over webrtc")
    assert node.transport.endpoint_host(app.did) == WEBRTC_HOST
    node.udp.deliver(_v1(app, node, b"over udp", 11), ("10.0.0.9", 5000))
    assert node.inbox.empty()  # plaintext over UDP stays refused when sealing
    assert node.transport.endpoint_host(app.did) == WEBRTC_HOST


def test_a_sealing_node_answers_a_link_peer_with_v1_and_a_udp_peer_with_nothing(pair):
    node, app = pair
    node.link.deliver(_v1(app, node, b"hi", 10), 7)
    assert node.transport.route(node.did, app.did, b"reply")
    (link_id, frame), = node.link.sent
    assert link_id == 7
    app.link.deliver(frame, 7)  # the app side is unsealed: it opens v1 as always
    assert app.inbox.get_nowait() == (node.did, b"reply")
    node.transport.set_peer_endpoint(app.did, "10.0.0.9", 5000)
    assert not node.transport.route(node.did, app.did, b"no key for udp")
    assert node.udp.sent == []


def test_relayed_frames_never_take_the_link_exception(pair):
    node, app = pair
    assert node.transport.open_relayed(_v1(app, node, b"via relay", 12), sender=app.did) is None


def test_a_replayed_link_frame_is_dropped(pair):
    node, app = pair
    frame = _v1(app, node, b"once", 20)
    node.link.deliver(frame, 1)
    node.link.deliver(frame, 2)
    assert node.inbox.get_nowait() == (app.did, b"once")
    assert node.inbox.empty()


def test_without_bound_hosts_a_sealing_node_refuses_the_link_too():
    allow = Allowlist()
    node, app = Side(allow, sealed=True, bound=frozenset()), Side(allow, sealed=False)
    allow.add(node.did)
    allow.add(app.did)
    node.link.deliver(_v1(app, node, b"x", 1), 1)
    assert node.inbox.empty()


# ---- failover: a link peer is never also sent through a relay ----------------------------


class StubRelay:
    def __init__(self):
        self.routed = []

    def register(self, session, deliver):
        pass

    def relays(self):
        return ["did:relay"]

    def route_via(self, relay, to_did, message):
        self.routed.append(to_did)
        return True


def test_failover_skips_the_relay_for_a_peer_on_a_link(pair):
    node, app = pair
    relay = StubRelay()
    fo = FailoverTransport(node.transport, relay, ["did:relay"], fresh_for=0.0)
    node.link.deliver(_v1(app, node, b"hi", 30), 1)
    assert fo.route(node.did, app.did, b"reply")
    assert relay.routed == []
    other = Identity.generate().did
    node.transport.set_peer_endpoint(other, "10.0.0.3", 1)
    fo.route(node.did, other, b"stale peer")
    assert relay.routed == [other]
