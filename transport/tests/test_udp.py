"""Real-loopback UDP tests for DirectUDPTransport: two nodes on 127.0.0.1 with
their own DIDs exchange DID-signed datagrams; spoofed/unauthorized frames are
dropped; and a peer's address change (roaming) keeps delivery working."""
from __future__ import annotations

import queue
import threading
import time

import pytest

pytest.importorskip("nacl")

from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_transport import Endpoint, PeerIdentity, Session  # noqa: E402
from secdogie_transport.udp import (  # noqa: E402
    DirectUDPTransport,  # noqa: E402
    UDPChannel,
    _encode_frame,
)


def _get(q, timeout=2.0):
    try:
        return q.get(timeout=timeout)
    except queue.Empty:
        return None


class Node:
    def __init__(self, allowlist):
        self.identity = Identity.generate()
        self.did = self.identity.did
        self.channel = UDPChannel()
        self.inbox: queue.Queue = queue.Queue()
        self.transport = DirectUDPTransport(self.identity, self.channel, allowlist=allowlist)

    def register_self(self):
        peer = PeerIdentity(self.did, "unused")
        host, port = self.channel.address
        self.transport.register(
            Session("s-" + self.did[-6:], peer, active=Endpoint("local", host, port)),
            lambda frm, msg: self.inbox.put((frm, msg)),
        )

    def close(self):
        self.channel.close()


@pytest.fixture
def two_nodes():
    a = None
    b = None
    try:
        allow = Allowlist()
        a = Node(allow)
        b = Node(allow)
        allow.add(a.did)
        allow.add(b.did)
        a.register_self()
        b.register_self()
        yield a, b
    finally:
        if a:
            a.close()
        if b:
            b.close()


def test_direct_signed_datagram_is_delivered(two_nodes):
    a, b = two_nodes
    a.transport.set_peer_endpoint(b.did, *b.channel.address)
    assert a.transport.route(a.did, b.did, b"hello over UDP")
    frm, msg = _get(b.inbox)
    assert frm == a.did and msg == b"hello over UDP"


def test_reply_uses_learned_address_roaming(two_nodes):
    a, b = two_nodes
    a.transport.set_peer_endpoint(b.did, *b.channel.address)
    a.transport.route(a.did, b.did, b"ping")
    assert _get(b.inbox) is not None
    # b never pre-configured a's address; it learned it from the inbound frame
    assert b.transport.route(b.did, a.did, b"pong")
    frm, msg = _get(a.inbox)
    assert frm == b.did and msg == b"pong"


def test_unsigned_or_garbage_is_dropped(two_nodes):
    a, b = two_nodes
    b.channel.send(*a.channel.address, b"not json at all")  # a's recv loop drops it
    b.channel.send(*a.channel.address, b'{"t":"secdogie/direct/v1","from":"x","to":"y"}')
    assert _get(a.inbox, timeout=0.4) is None


def test_unauthorized_did_is_dropped():
    allow = Allowlist()
    a = Node(allow)
    allow.add(a.did)  # a authorizes only itself
    a.register_self()
    stranger = Identity.generate()  # not on a's allowlist
    ch = UDPChannel()
    try:
        frame = _encode_frame(stranger, a.did, b"intrusion")
        ch.send(*a.channel.address, frame)
        assert _get(a.inbox, timeout=0.4) is None
    finally:
        a.close()
        ch.close()


def test_address_change_is_adopted_by_did(two_nodes):
    a, b = two_nodes
    # a moves to a fresh channel (new source port) but same DID
    a.channel.close()
    a.channel = UDPChannel()
    a.transport.channel = a.channel
    a.channel.start(a.transport._on_datagram)
    a.transport.set_peer_endpoint(b.did, *b.channel.address)

    a.transport.route(a.did, b.did, b"from new port")
    frm, msg = _get(b.inbox)
    assert frm == a.did and msg == b"from new port"
    # b now routes back to a's NEW address (adopted by DID), not the old one
    assert b.transport.route(b.did, a.did, b"reply to new port")
    got = _get(a.inbox)
    assert got is not None and got[1] == b"reply to new port"


def test_route_without_endpoint_returns_false(two_nodes):
    a, b = two_nodes
    # a has never seen c and has no endpoint for it
    assert a.transport.route(a.did, "did:key:zUnknownPeer", b"x") is False


def test_flush_delay():
    # sanity: give the recv threads a moment on slow CI (no assertion beyond no-crash)
    time.sleep(0.01)


def test_close_waits_for_a_running_callback():
    # A node closes its channel and then its journal; if close() returned while
    # a callback was still reading the journal, the journal was freed under it
    # (a segfault in CI). close() must not return until the callback is done.
    entered, finished = threading.Event(), threading.Event()

    def slow(_data, _addr):
        entered.set()
        time.sleep(0.3)
        finished.set()

    ch, sender = UDPChannel(), UDPChannel()
    try:
        ch.start(slow)
        sender.send(*ch.address, b"x")
        assert entered.wait(2.0)
        ch.close()
        assert finished.is_set()
    finally:
        ch.close()
        sender.close()


def test_close_from_inside_a_callback_does_not_deadlock():
    done = threading.Event()
    ch, sender = UDPChannel(), UDPChannel()

    def closes_itself(_data, _addr):
        ch.close()
        done.set()

    try:
        ch.start(closes_itself)
        sender.send(*ch.address, b"x")
        assert done.wait(2.0)
        ch._thread.join(2.0)
        assert not ch._thread.is_alive()
    finally:
        sender.close()


def test_close_without_start():
    UDPChannel().close()
