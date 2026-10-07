"""The WebRTC link, in one process: two aiortc peers meet through a fake
signaling gateway, prove their DIDs over the DTLS fingerprints (W1), and only
then carry DID-signed frames for the unchanged transport. A gateway in the
middle cannot pass W1; a non-data offer is never answered; refusals are signed.

Runs only with the [webrtc] extra, and only on a host with a non-loopback IPv4
address (aiortc's ICE does not use 127.0.0.1)."""
from __future__ import annotations

import ast
import queue
import socket
import time
from pathlib import Path

import pytest

pytest.importorskip("nacl")

from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_transport import (  # noqa: E402
    WEBRTC_HOST,
    ChannelMux,
    CompositeChannel,
    DirectUDPTransport,
    Endpoint,
    PeerIdentity,
    Session,
    UDPChannel,
)

WEBRTC_PY = Path(__file__).resolve().parents[1] / "secdogie_transport" / "webrtc.py"


def test_the_link_module_never_touches_media():
    """Static on purpose: aiortc itself loads PyAV when imported, so a runtime
    check of sys.modules would always fail -- what matters is that this code
    never imports a media module or adds a track."""
    tree = ast.parse(WEBRTC_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            if isinstance(node, ast.Attribute) and node.attr in ("addTrack", "addTransceiver", "getUserMedia"):
                pytest.fail(f"media call {node.attr} in webrtc.py")
            continue
        for n in names:
            assert not (n == "av" or n.startswith("av.") or n.startswith("aiortc.contrib.media")), n


def test_signal_urls():
    from secdogie_transport.webrtc import check_signal_url

    assert check_signal_url("wss://sig.example/ws")
    assert check_signal_url("ws://127.0.0.1:8787/ws") and check_signal_url("ws://localhost/ws")
    for bad in ("ws://sig.example/ws", "https://sig.example/ws", "", "wss:///ws"):
        with pytest.raises(ValueError):
            check_signal_url(bad)


# ---- live links --------------------------------------------------------------------------------

pytest.importorskip("aiortc")
pytest.importorskip("websockets")

from secdogie_transport.webrtc import BindingPolicy, WebRTCChannel, WebRTCConfig  # noqa: E402
from secdogie_transport.webrtc_testing import FakeSignalingServer  # noqa: E402


def _lan_ipv4() -> str | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))  # no packet is sent; this only picks a route
        ip = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    return None if ip.startswith("127.") else ip


pytestmark = pytest.mark.skipif(_lan_ipv4() is None, reason="aiortc's ICE needs a non-loopback IPv4 address")

ROOM = "test-room-0001"


def _until(pred, timeout=20.0, what="condition"):
    deadline = time.monotonic() + timeout
    while not pred():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.05)


class End:
    """One side: a WebRTC link under a DirectUDPTransport with a mux, like the
    node (and a Python App) run it."""

    def __init__(self, identity, url, policy, *, trust, room=ROOM):
        self.identity = identity
        self.link = WebRTCChannel(WebRTCConfig(url, room, ice_servers=()), policy)
        self.channel = CompositeChannel(UDPChannel("127.0.0.1", 0), {WEBRTC_HOST: self.link})
        self.transport = DirectUDPTransport(identity, self.channel, allowlist=trust,
                                            bound_link_hosts=frozenset({WEBRTC_HOST}))
        self.mux = ChannelMux(self.transport, Session("s", PeerIdentity(identity.did, ""),
                                                      active=Endpoint("local", *self.channel.address)))
        self.inbox: queue.Queue = queue.Queue()
        self.mux.channel("test/v1", lambda frm, data: self.inbox.put((frm, data)))
        self.policy = policy

    def bound(self):
        return [d for k, d in self.link.events if k == "bound"]

    def close(self):
        self.channel.close()


@pytest.fixture
def server():
    s = FakeSignalingServer()
    s.start()
    yield s
    s.stop()


@pytest.fixture
def ids():
    return Identity.generate(), Identity.generate()  # node, app


def _node(ids, url, *, apps=None, admit=None, room=ROOM):
    node, app = ids
    apps = apps if apps is not None else Allowlist({app.did})
    return End(node, url, BindingPolicy(node, apps, speak_first=True, admit=admit), trust=apps, room=room)


def _app(ids, url, *, room=ROOM, policy=None):
    node, app = ids
    trust = Allowlist({node.did})
    return End(app, url, policy or BindingPolicy(app, trust, speak_first=False, node_did=node.did), trust=trust,
               room=room)


def test_two_ends_bind_and_carry_frames_both_ways(server, ids):
    node, app = _node(ids, server.url), _app(ids, server.url)
    try:
        node.link.open()
        _until(lambda: server.peers(ROOM) == 1, what="the node in the room")
        app.link.open()
        _until(lambda: node.bound() and app.bound(), what="W1 both ways")
        assert node.bound()[0][1] == ids[1].did and app.bound()[0][1] == ids[0].did
        # the app speaks first on the transport; the node learns its endpoint from the frame
        node.transport.set_peer_endpoint(ids[1].did, WEBRTC_HOST, -1)  # placeholder until a frame arrives
        app.transport.set_peer_endpoint(ids[0].did, WEBRTC_HOST, app.bound()[0][0])
        assert app.mux.send(ids[0].did, "test/v1", b"hello node")
        assert node.inbox.get(timeout=10) == (ids[1].did, b"hello node")
        assert node.transport.endpoint_host(ids[1].did) == WEBRTC_HOST
        assert node.mux.send(ids[1].did, "test/v1", "你好".encode())
        assert app.inbox.get(timeout=10) == (ids[0].did, "你好".encode())
        # the offer came from the page side (it joined second) and was data-only
        assert [m["type"] for m in server.relayed][:2] == ["offer", "answer"]
    finally:
        app.close()
        node.close()


def test_an_app_that_is_not_enrolled_gets_a_signed_refusal(server, ids):
    node = _node(ids, server.url, apps=Allowlist({Identity.generate().did}))
    app = _app(ids, server.url)
    try:
        node.link.open()
        _until(lambda: server.peers(ROOM) == 1)
        app.link.open()
        _until(lambda: app.policy.refusal == "not-enrolled", what="the refusal")
        assert not node.bound()
    finally:
        app.close()
        node.close()


def test_busy_is_a_signed_refusal_too(server, ids):
    node = _node(ids, server.url, admit=lambda did: "busy")
    app = _app(ids, server.url)
    try:
        node.link.open()
        _until(lambda: server.peers(ROOM) == 1)
        app.link.open()
        _until(lambda: app.policy.refusal == "busy", what="busy")
        assert not node.bound()
    finally:
        app.close()
        node.close()


def test_a_page_reveals_nothing_to_a_node_it_does_not_expect(server, ids):
    impostor = (Identity.generate(), ids[1])
    node = _node(impostor, server.url)
    app = _app(ids, server.url)  # expects ids[0]
    try:
        node.link.open()
        _until(lambda: server.peers(ROOM) == 1)
        app.link.open()
        _until(lambda: app.policy.last_failure == "signer not trusted", what="the app's refusal")
        _until(lambda: any(k == "link-closed" for k, _ in node.link.events), what="the link closing")
        assert not app.bound() and not node.bound()
    finally:
        app.close()
        node.close()


def test_an_offer_with_media_is_never_answered(server, ids):
    def add_video(msg):
        if msg["type"] == "offer":
            msg = {**msg, "payload": {**msg["payload"], "sdp": msg["payload"]["sdp"] + "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"}}
        return msg

    server.rewrite = add_video
    node, app = _node(ids, server.url), _app(ids, server.url)
    try:
        node.link.open()
        _until(lambda: server.peers(ROOM) == 1)
        app.link.open()
        _until(lambda: node.link.stats["refused_sdp"] >= 1, what="the refusal")
        time.sleep(1.0)
        assert not any(m["type"] == "answer" for m in server.relayed)
        assert not node.bound()
    finally:
        app.close()
        node.close()


def test_a_page_that_comes_back_gets_a_new_link_and_the_node_follows(server, ids):
    node, app = _node(ids, server.url), _app(ids, server.url)
    try:
        node.link.open()
        _until(lambda: server.peers(ROOM) == 1)
        app.link.open()
        _until(lambda: node.bound() and app.bound())
        first = node.bound()[0][0]
        app.close()  # the tab is closed
        app = _app(ids, server.url)  # ...and opened again
        app.link.open()
        _until(lambda: len(node.bound()) == 2, what="the second binding")
        second = node.bound()[1][0]
        assert second != first
        app.transport.set_peer_endpoint(ids[0].did, WEBRTC_HOST, app.bound()[0][0])
        assert app.mux.send(ids[0].did, "test/v1", b"back")
        assert node.inbox.get(timeout=10) == (ids[1].did, b"back")
        assert node.mux.send(ids[1].did, "test/v1", b"welcome back")  # to the new link, not the old one
        assert app.inbox.get(timeout=10) == (ids[0].did, b"welcome back")
    finally:
        app.close()
        node.close()


def test_the_node_rejoins_after_the_gateway_drops_it(server, ids):
    node = _node(ids, server.url)
    try:
        node.link.open()
        _until(lambda: node.link.stats["signal_connects"] == 1)
        server.kick(ROOM)
        _until(lambda: node.link.stats["signal_connects"] == 2, timeout=10, what="the reconnect")
        assert server.peers(ROOM) == 1
    finally:
        node.close()


def test_a_third_peer_gets_room_full(server, ids):
    node, app = _node(ids, server.url), _app(ids, server.url)
    third = _app(ids, server.url)
    try:
        node.link.open()
        _until(lambda: server.peers(ROOM) == 1)
        app.link.open()
        _until(lambda: server.peers(ROOM) == 2)
        third.link.open()
        _until(lambda: server.refused_full >= 1, what="room-full")
        assert any(k == "gateway-error" and d == "room-full" for k, d in third.link.events)
    finally:
        for e in (third, app, node):
            e.close()


# ---- a gateway in the middle ---------------------------------------------------------------


class Relay:
    """A hostile gateway's own peer: it terminates a link and forwards every
    statement it gets to its twin, verbatim."""

    def __init__(self):
        self.twin = None
        self.links = []
        self.backlog = []  # statements for this side's link, held until it opens
        self.forwarded = 0

    def on_open(self, link):
        self.links.append(link)
        for msg in self.backlog:
            link.send_text(msg)
        self.backlog.clear()

    def on_text(self, link, msg):
        self.forwarded += 1
        if self.twin.links:
            self.twin.links[-1].send_text(msg)
        else:
            self.twin.backlog.append(msg)


class Blurt(BindingPolicy):
    """An App that states first, without checking the node -- so the node's own
    check is what a relayed statement has to get past."""

    def on_open(self, link):
        link.send_text(self._statement(link))


def test_a_man_in_the_middle_fails_w1_on_both_sides(ids):
    node_side, app_side = FakeSignalingServer(), FakeSignalingServer()
    node_side.start()
    app_side.start()
    r_node, r_app = Relay(), Relay()
    r_node.twin, r_app.twin = r_app, r_node
    mitm_node = WebRTCChannel(WebRTCConfig(node_side.url, ROOM, ice_servers=()), r_node)
    mitm_app = WebRTCChannel(WebRTCConfig(app_side.url, ROOM, ice_servers=()), r_app)
    node = _node(ids, node_side.url)
    blurt = Blurt(ids[1], Allowlist({ids[0].did}), speak_first=False, node_did=ids[0].did)
    app = _app(ids, app_side.url, policy=blurt)
    try:
        for ch in (mitm_node, mitm_app):
            ch.start(lambda data, link_id: None)
            ch.open()
        _until(lambda: node_side.peers(ROOM) == 1 and app_side.peers(ROOM) == 1)
        node.link.open()
        app.link.open()
        _until(lambda: r_node.forwarded >= 1 and r_app.forwarded >= 1, timeout=30, what="statements relayed")
        _until(lambda: blurt.last_failure is not None and node.policy.last_failure is not None, what="both refusals")
        assert "not the one it signed for" in blurt.last_failure
        assert "not the one it signed for" in node.policy.last_failure or "not ours" in node.policy.last_failure
        assert not node.bound() and not app.bound()
        assert node.inbox.empty() and app.inbox.empty()
    finally:
        for e in (app, node):
            e.close()
        for ch in (mitm_node, mitm_app):
            ch.close()
        node_side.stop()
        app_side.stop()
