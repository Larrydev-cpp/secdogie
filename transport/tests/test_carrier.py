"""The carrier seam (N0): carriers under DirectUDPTransport.

UDP keeps behaving as before (the rest of the suite is unchanged); these tests
cover what the seam adds -- several carriers under one transport, endpoints that
remember their carrier, one delivery thread whatever thread or event loop a
carrier receives on, and every frame check holding on every carrier."""
from __future__ import annotations

import asyncio
import json
import queue
import threading
import warnings

import pytest

pytest.importorskip("nacl")

from secdogie_identity import Allowlist, Identity, sign_payload  # noqa: E402
from secdogie_transport import (  # noqa: E402
    AsyncCarrierHost,
    Carrier,
    CarrierSet,
    DirectTransport,
    DirectUDPTransport,
    Endpoint,
    MemoryCarrier,
    MemoryNetwork,
    PeerIdentity,
    Session,
    UDPChannel,
)


def _get(q, timeout=2.0):
    try:
        return q.get(timeout=timeout)
    except queue.Empty:
        return None


class Node:
    """A node with a UDP primary carrier and a memory carrier beside it."""

    def __init__(self, allowlist, net: MemoryNetwork, *, mem_kind: str = "memory"):
        self.identity = Identity.generate()
        self.did = self.identity.did
        self.udp = UDPChannel()
        self.mem = MemoryCarrier(net, kind=mem_kind)
        self.carriers = CarrierSet(self.udp, self.mem)
        self.inbox: queue.Queue = queue.Queue()
        self.threads: list[str] = []
        self.transport = DirectUDPTransport(self.identity, self.carriers, allowlist=allowlist)

    def register_self(self):
        host, port = self.udp.address
        self.transport.register(
            Session("s-" + self.did[-6:], PeerIdentity(self.did, "unused"), active=Endpoint("local", host, port)),
            self._deliver,
        )

    def _deliver(self, frm, msg):
        self.threads.append(threading.current_thread().name)
        self.inbox.put((frm, msg))

    def close(self):
        self.carriers.close()


@pytest.fixture
def pair():
    net = MemoryNetwork()
    allow = Allowlist()
    a, b = Node(allow, net), Node(allow, net)
    allow.add(a.did)
    allow.add(b.did)
    a.register_self()
    b.register_self()
    try:
        yield a, b, net
    finally:
        a.close()
        b.close()


# --- the Carrier contract -------------------------------------------------------


def test_udp_channel_is_the_udp_carrier():
    ch = UDPChannel()
    try:
        assert isinstance(ch, Carrier)
        assert ch.kind == "udp" and ch.primary_kind == "udp" and ch.kinds == frozenset({"udp"})
        assert not ch.reliable
        with pytest.raises(ValueError):
            ch.send("127.0.0.1", 9, b"x", carrier="quic")
    finally:
        ch.close()


def test_direct_transport_is_the_same_class():
    assert DirectTransport is DirectUDPTransport


def test_carrier_set_rules():
    net = MemoryNetwork()
    udp = UDPChannel()
    m1, m2 = MemoryCarrier(net), MemoryCarrier(net)
    try:
        with pytest.raises(ValueError):
            CarrierSet(m1, m2)  # two "memory" carriers
        s = CarrierSet(udp, m1)
        assert s.address == udp.address
        assert s.primary_kind == "udp" and s.kinds == frozenset({"udp", "memory"})
        assert s.addresses() == {"udp": udp.address, "memory": m1.address}
        assert s.max_frame == udp.max_frame and not s.reliable
        with pytest.raises(TypeError):
            CarrierSet(s)
        with pytest.raises(ValueError):
            s.send("x", 1, b"y", carrier="webrtc")
    finally:
        udp.close()
        m1.close()
        m2.close()


# --- frames over several carriers -----------------------------------------------


def test_frame_over_the_memory_carrier_is_delivered_and_remembered(pair):
    a, b, _ = pair
    a.transport.set_peer_endpoint(b.did, *b.mem.address, carrier="memory")
    assert a.transport.peer_carrier(b.did) == "memory"
    assert a.transport.route(a.did, b.did, b"over memory")
    assert _get(b.inbox) == (a.did, b"over memory")
    # B learned A's memory address from the authenticated frame, tagged with its carrier.
    assert b.transport.peer_carrier(a.did) == "memory"
    assert b.transport.peer_endpoints(a.did) == {"memory": a.mem.address}


def test_reply_goes_back_on_the_carrier_the_newest_frame_came_on(pair):
    a, b, net = pair
    a.transport.set_peer_endpoint(b.did, *b.mem.address, carrier="memory")
    assert a.transport.route(a.did, b.did, b"ping")
    assert _get(b.inbox) == (a.did, b"ping")
    # Cut A's memory path: B's reply, sent on memory, is lost...
    net.block(a.mem.address)
    assert b.transport.route(b.did, a.did, b"pong-1")
    assert _get(a.inbox, timeout=0.3) is None
    # ...and arrives once the path is back. UDP was never involved.
    net.unblock(a.mem.address)
    assert b.transport.route(b.did, a.did, b"pong-2")
    assert _get(a.inbox) == (b.did, b"pong-2")


def test_newest_frame_moves_the_active_carrier_and_each_carrier_keeps_its_address(pair):
    a, b, _ = pair
    a.transport.set_peer_endpoint(b.did, *b.udp.address)
    assert a.transport.route(a.did, b.did, b"udp first")
    assert _get(b.inbox) == (a.did, b"udp first")
    assert b.transport.peer_carrier(a.did) == "udp"
    a.transport.set_peer_endpoint(b.did, *b.mem.address, carrier="memory")
    assert a.transport.route(a.did, b.did, b"then memory")
    assert _get(b.inbox) == (a.did, b"then memory")
    assert b.transport.peer_carrier(a.did) == "memory"
    assert b.transport.peer_endpoints(a.did) == {"udp": a.udp.address, "memory": a.mem.address}


def test_a_replay_on_another_carrier_is_dropped_and_moves_nothing(pair):
    a, b, net = pair
    frame = a.transport.build_frame(b.did, b"once")
    a.udp.send(*b.udp.address, frame)
    assert _get(b.inbox) == (a.did, b"once")
    attacker = MemoryCarrier(net)
    try:
        attacker.send(*b.mem.address, frame)  # the same signed frame, from elsewhere
        assert _get(b.inbox, timeout=0.3) is None
        assert b.transport.peer_carrier(a.did) == "udp"
        assert "memory" not in b.transport.peer_endpoints(a.did)
    finally:
        attacker.close()


def test_unauthorized_signer_on_the_memory_carrier_is_dropped(pair):
    _, b, net = pair
    stranger = Identity.generate()
    obj = sign_payload(stranger, {"t": "secdogie/direct/v1", "from": stranger.did, "to": b.did,
                                  "ctr": 1, "data": "aGk="})
    rogue = MemoryCarrier(net)
    try:
        rogue.send(*b.mem.address, json.dumps(obj).encode())
        assert _get(b.inbox, timeout=0.3) is None
        assert b.transport.peer_carrier(stranger.did) is None
    finally:
        rogue.close()


def test_route_to_a_carrier_this_node_lacks_is_refused(pair):
    a, b, _ = pair
    a.transport.set_peer_endpoint(b.did, "192.0.2.1", 443, carrier="webrtc")
    assert not a.transport.route(a.did, b.did, b"nowhere")


# --- one delivery thread --------------------------------------------------------


def test_every_carrier_delivers_on_the_one_delivery_thread_in_order(pair):
    a, b, _ = pair
    a.transport.set_peer_endpoint(b.did, *b.mem.address, carrier="memory")
    for i in range(50):
        assert a.transport.route(a.did, b.did, str(i).encode())
    a.transport.set_peer_endpoint(b.did, *b.udp.address)
    assert a.transport.route(a.did, b.did, b"udp")
    got = [_get(b.inbox) for _ in range(51)]
    assert [m for _, m in got[:50]] == [str(i).encode() for i in range(50)]
    assert got[50] == (a.did, b"udp")
    assert set(b.threads) == {"carrier-deliver"}
    assert b.carriers.delivery_thread.name == "carrier-deliver"


def test_shared_socket_roles_answer_on_the_primary_carrier_only(pair):
    a, b, net = pair
    seen = queue.Queue()
    b.transport.on_frame("secdogie/test-role/v1", lambda obj, addr: seen.put(addr))
    raw = json.dumps({"t": "secdogie/test-role/v1"}).encode()
    probe = MemoryCarrier(net)
    try:
        probe.send(*b.mem.address, raw)
        assert _get(seen, timeout=0.3) is None  # not on a secondary carrier
        a.udp.send(*b.udp.address, raw)
        assert _get(seen) == a.udp.address  # a plain (host, port), as roles expect
    finally:
        probe.close()


def test_a_full_inbound_queue_drops_and_counts():
    net = MemoryNetwork()
    sender, receiver = MemoryCarrier(net), MemoryCarrier(net)
    s = CarrierSet(receiver, queue_size=2)
    release = threading.Event()
    s.start(lambda data, addr: release.wait(2.0))
    try:
        for _ in range(10):
            sender.send(*receiver.address, b"x")
        deadline = threading.Event()
        for _ in range(40):
            if s.dropped:
                break
            deadline.wait(0.05)
        assert s.dropped > 0
    finally:
        release.set()
        s.close()
        sender.close()


def test_memory_network_block_and_close():
    net = MemoryNetwork()
    a, b = MemoryCarrier(net), MemoryCarrier(net)
    got = queue.Queue()
    b.start(lambda data, addr: got.put((data, addr)))
    try:
        net.block(b.address)
        a.send(*b.address, b"lost")
        assert _get(got, timeout=0.2) is None
        net.unblock(b.address)
        a.send(*b.address, b"kept")
        assert _get(got) == (b"kept", a.address)
        b.close()
        a.send(*b.address, b"after close")  # detached: silently nowhere, like UDP
        assert _get(got, timeout=0.2) is None
        with pytest.raises(ValueError):
            a.send(*b.address, b"x" * (a.max_frame + 1))
    finally:
        a.close()
        b.close()


# --- the asyncio boundary -------------------------------------------------------


def test_async_host_runs_coroutines_and_calls():
    with AsyncCarrierHost() as host:
        async def add(x, y):
            await asyncio.sleep(0)
            return x + y

        assert host.submit(add(2, 3)).result(timeout=2) == 5
        assert host.call(lambda: host.in_loop_thread()).result(timeout=2) is True
        assert not host.in_loop_thread()

        async def boom():
            raise RuntimeError("inside the loop")

        with pytest.raises(RuntimeError, match="inside the loop"):
            host.submit(boom()).result(timeout=2)
        # The loop survives a failed coroutine.
        assert host.submit(add(1, 1)).result(timeout=2) == 2


def test_async_host_stop_cancels_pending_and_refuses_new_work():
    host = AsyncCarrierHost().start()
    started, cancelled = threading.Event(), threading.Event()

    async def forever():
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    fut = host.submit(forever())
    assert started.wait(2.0)
    host.stop()
    host.stop()  # idempotent
    assert cancelled.wait(2.0)
    assert fut.cancelled()
    assert not host.thread.is_alive()

    async def late():
        return 1

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # an un-awaited coroutine would warn
        with pytest.raises(RuntimeError):
            host.submit(late())
    with pytest.raises(RuntimeError):
        host.start()


class LoopCarrier(Carrier):
    """A toy asyncio carrier: frames are handed over on the event loop thread,
    as aiortc / aioquic callbacks will be."""

    kind = "aio"
    reliable = True
    _registry: dict = {}
    _next = [1]

    def __init__(self, host: AsyncCarrierHost):
        self.host = host
        self.address = ("aio", LoopCarrier._next[0])
        LoopCarrier._next[0] += 1
        LoopCarrier._registry[self.address] = self
        self._cb = None

    def start(self, on_datagram):
        self._cb = on_datagram

    def send(self, host, port, data, carrier=None):
        self._check_kind(carrier)
        target = LoopCarrier._registry[(host, port)]
        self.host.loop.call_soon_threadsafe(target._cb, bytes(data), self.address)

    def close(self):
        LoopCarrier._registry.pop(self.address, None)


def test_frames_from_an_event_loop_carrier_are_delivered_off_the_loop_thread():
    allow = Allowlist()
    with AsyncCarrierHost() as host:
        nodes = []
        for _ in range(2):
            ident = Identity.generate()
            allow.add(ident.did)
            carrier = LoopCarrier(host)
            inbox = queue.Queue()
            tr = DirectUDPTransport(ident, CarrierSet(carrier), allowlist=allow)
            tr.register(Session("s", PeerIdentity(ident.did, ""), active=None),
                        lambda frm, msg, q=inbox: q.put((frm, msg, threading.current_thread())))
            nodes.append((ident, carrier, tr, inbox))
        (ia, _, ta, _), (ib, cb, tb, qb) = nodes
        ta.set_peer_endpoint(ib.did, *cb.address)
        assert ta.route(ia.did, ib.did, b"from the loop")
        frm, msg, thread = _get(qb)
        assert (frm, msg) == (ia.did, b"from the loop")
        assert thread is not host.thread and thread.name == "carrier-deliver"
        assert tb.peer_carrier(ia.did) == "aio"
        for _, _, tr, _ in nodes:
            tr.channel.close()
