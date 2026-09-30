"""ChannelMux: several applications share one transport's single inbound path,
each receiving only its channel, with the sender's verified DID. Unknown or
malformed messages are dropped; a failing handler does not disturb the others.
Exercised over real UDP on 127.0.0.1."""
from __future__ import annotations

import queue

import pytest

pytest.importorskip("nacl")

from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_transport import Endpoint, PeerIdentity, Session  # noqa: E402
from secdogie_transport.mux import ChannelMux, decode, encode  # noqa: E402
from secdogie_transport.udp import DirectUDPTransport, UDPChannel  # noqa: E402


def _get(q, timeout=2.0):
    try:
        return q.get(timeout=timeout)
    except queue.Empty:
        return None


class Node:
    def __init__(self, allow):
        self.identity = Identity.generate()
        self.did = self.identity.did
        self.channel = UDPChannel()
        self.transport = DirectUDPTransport(self.identity, self.channel, allowlist=allow)
        host, port = self.channel.address
        self.mux = ChannelMux(self.transport, Session("s", PeerIdentity(self.did, "unused"),
                                                      active=Endpoint("local", host, port)))


@pytest.fixture
def pair():
    allow = Allowlist()
    a, b = Node(allow), Node(allow)
    allow.add(a.did)
    allow.add(b.did)
    a.transport.set_peer_endpoint(b.did, *b.channel.address)
    b.transport.set_peer_endpoint(a.did, *a.channel.address)
    yield a, b
    a.channel.close()
    b.channel.close()


def test_each_channel_gets_only_its_own_messages(pair):
    a, b = pair
    dialogue, other = queue.Queue(), queue.Queue()
    b.mux.channel("dialogue/v1", lambda frm, data: dialogue.put((frm, data)))
    b.mux.channel("telemetry", lambda frm, data: other.put((frm, data)))
    assert a.mux.send(b.did, "dialogue/v1", b"hello")
    assert a.mux.send(b.did, "telemetry", b"\x00\x01")
    assert _get(dialogue) == (a.did, b"hello")
    assert _get(other) == (a.did, b"\x00\x01")
    assert dialogue.empty() and other.empty()


def test_unknown_channels_and_closed_channels_are_dropped(pair):
    a, b = pair
    got = queue.Queue()
    b.mux.channel("dialogue/v1", lambda frm, data: got.put(data))
    a.mux.send(b.did, "nobody-listens", b"x")
    b.mux.channel("dialogue/v1", None)
    a.mux.send(b.did, "dialogue/v1", b"after close")
    assert _get(got, timeout=0.5) is None


def test_a_failing_handler_does_not_disturb_other_channels(pair):
    a, b = pair
    got = queue.Queue()

    def boom(frm, data):
        raise RuntimeError("handler bug")

    b.mux.channel("broken", boom)
    b.mux.channel("fine", lambda frm, data: got.put(data))
    a.mux.send(b.did, "broken", b"1")
    a.mux.send(b.did, "fine", b"2")
    assert _get(got) == b"2"


def test_a_failing_handler_never_reaches_back_into_the_sender():
    # On an in-process hub delivery is synchronous: without the mux's guard a
    # receiver's bug would surface as an exception in the sender's send().
    from secdogie_transport.transport import HubTransport

    a_id, b_id = Identity.generate(), Identity.generate()
    hub = HubTransport(allowlist=Allowlist({a_id.did, b_id.did}))
    a = ChannelMux(hub, Session("a", PeerIdentity(a_id.did, "unused")))
    b = ChannelMux(hub, Session("b", PeerIdentity(b_id.did, "unused")))
    def boom(frm, data):
        raise RuntimeError("receiver bug")

    b.channel("broken", boom)
    assert a.send(b_id.did, "broken", b"x") is True


def test_an_unauthorized_sender_reaches_no_channel(pair):
    a, b = pair
    got = queue.Queue()
    b.mux.channel("dialogue/v1", lambda frm, data: got.put(data))
    stranger = Node(Allowlist({b.did}))  # knows b, but b does not allow it
    stranger.transport.set_peer_endpoint(b.did, *b.channel.address)
    stranger.mux.send(b.did, "dialogue/v1", b"let me in")
    assert _get(got, timeout=0.5) is None
    stranger.channel.close()


def test_encoding_round_trips_and_rejects_garbage():
    assert decode(encode("dialogue/v1", b"\x00payload")) == ("dialogue/v1", b"\x00payload")
    assert decode(encode("a", b"")) == ("a", b"")
    for bad in (b"", b"\x05ab", b"\x02\xff\xfe", b"\x03a b", b"\x00x"):
        assert decode(bad) is None
    for name in ("", "has space", "x" * 65, "é"):
        with pytest.raises(ValueError):
            encode(name, b"")
