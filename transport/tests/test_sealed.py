"""Encrypted direct frames (v2): loopback tests on 127.0.0.1.

Two nodes, each with a DID and an X25519 transport key bound to it, exchange
sealed frames. Covers: round trip, nothing readable on the wire, tampering,
reflection, replay (including a replay from another address, which must not move
the peer's endpoint), no key -> nothing sent, bad bindings refused, v1 dropped,
and the P2P.2 upgrade still working on top."""
from __future__ import annotations

import base64
import json
import queue
import time

import pytest

pytest.importorskip("nacl")

from nacl.public import PrivateKey  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_identity.binding import create_binding  # noqa: E402
from secdogie_transport import (  # noqa: E402
    DirectUpgrader,
    Endpoint,
    PeerIdentity,
    ReplayWindow,
    Session,
    load_transport_key,
)
from secdogie_transport.sealed import public_key_b64  # noqa: E402
from secdogie_transport.udp import DirectUDPTransport, UDPChannel, _encode_frame  # noqa: E402

SECRET = b"the journal entry nobody on the path should read"


# --- ReplayWindow (pure) ----------------------------------------------------


def test_replay_window():
    w = ReplayWindow(size=8)
    assert w.accept(100)
    assert not w.accept(100)            # duplicate
    assert w.accept(103)                # forward
    assert w.accept(101)                # late but inside the window, once
    assert not w.accept(101)
    assert not w.accept(95)             # 103 - 95 = 8 -> outside a window of 8
    assert w.accept(96)                 # 7 behind -> inside
    assert w.accept(1_000)              # big jump resets the bitmap
    assert not w.accept(1_000)
    assert not w.accept(103)            # now far too old


# --- key file ---------------------------------------------------------------


def test_load_transport_key_reads_tunnel_format(tmp_path):
    sk = PrivateKey.generate()
    path = tmp_path / "node.tkey"
    path.write_text(
        "# written by secdogie-tunnel genkey\n"
        f"private_key = {base64.b64encode(bytes(sk)).decode()}\n"
        "address = 10.0.0.1\n",
        encoding="utf-8",
    )
    assert public_key_b64(load_transport_key(path)) == public_key_b64(sk)

    missing = tmp_path / "none.conf"
    missing.write_text("address = 10.0.0.1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_transport_key(missing)
    short = tmp_path / "short.conf"
    short.write_text(f"private_key = {base64.b64encode(b'x' * 16).decode()}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_transport_key(short)


# --- loopback nodes ---------------------------------------------------------


class RecordingChannel(UDPChannel):
    """A real UDP channel that also keeps every datagram it sends."""

    def __init__(self):
        super().__init__()
        self.sent: list[bytes] = []

    def send(self, host, port, data):
        self.sent.append(data)
        super().send(host, port, data)


class EncNode:
    def __init__(self, allow, *, encrypted=True):
        self.identity = Identity.generate()
        self.did = self.identity.did
        self.tkey = PrivateKey.generate()
        self.binding = create_binding(self.identity, public_key_b64(self.tkey), key_version=1)
        self.channel = RecordingChannel()
        self.inbox: queue.Queue = queue.Queue()
        self.transport = DirectUDPTransport(
            self.identity, self.channel, allowlist=allow,
            transport_key=self.tkey if encrypted else None,
        )
        host, port = self.channel.address
        self.transport.register(
            Session("s-" + self.did[-6:], PeerIdentity(self.did, "unused"),
                    active=Endpoint("local", host, port)),
            lambda frm, msg: self.inbox.put((frm, msg)),
        )

    def know(self, other: EncNode, *, binding=True):
        if binding:
            assert self.transport.add_peer_binding(other.binding)
        self.transport.set_peer_endpoint(other.did, *other.channel.address)

    def close(self):
        self.channel.close()


def _get(q, timeout=1.0):
    try:
        return q.get(timeout=timeout)
    except queue.Empty:
        return None


def _raw_send(from_channel, to_node, data):
    from_channel.send(*to_node.channel.address, data)


@pytest.fixture
def pair():
    allow = Allowlist()
    a, b = EncNode(allow), EncNode(allow)
    allow.add(a.did)
    allow.add(b.did)
    a.know(b)
    b.know(a)
    yield a, b
    a.close()
    b.close()


def test_round_trip_and_nothing_readable_on_the_wire(pair):
    a, b = pair
    assert a.transport.route(a.did, b.did, SECRET)
    assert _get(b.inbox) == (a.did, SECRET)
    assert b.transport.route(b.did, a.did, b"reply")
    assert _get(a.inbox) == (b.did, b"reply")

    wire = b"".join(a.channel.sent)
    assert SECRET not in wire
    assert base64.b64encode(SECRET) not in wire
    frame = json.loads(a.channel.sent[0])
    assert frame["t"] == "secdogie/direct/v2" and "data" not in frame


def test_tampered_frame_is_dropped(pair):
    a, b = pair
    a.transport.route(a.did, b.did, SECRET)
    assert _get(b.inbox) is not None
    frame = json.loads(a.channel.sent[-1])
    ct = bytearray(base64.b64decode(frame["ct"]))
    ct[-1] ^= 0x01
    frame["ct"] = base64.b64encode(bytes(ct)).decode()
    _raw_send(a.channel, b, json.dumps(frame).encode())
    assert _get(b.inbox, timeout=0.3) is None


def test_frame_bounced_back_to_its_sender_is_dropped(pair):
    a, b = pair
    a.transport.route(a.did, b.did, SECRET)
    assert _get(b.inbox) is not None
    _raw_send(b.channel, a, a.channel.sent[-1])  # A's own frame, delivered to A
    assert _get(a.inbox, timeout=0.3) is None


def test_replay_is_dropped_and_cannot_move_the_endpoint(pair):
    a, b = pair
    a.transport.route(a.did, b.did, SECRET)
    assert _get(b.inbox) == (a.did, SECRET)
    captured = a.channel.sent[-1]

    # same frame again from A's address: not delivered twice
    _raw_send(a.channel, b, captured)
    assert _get(b.inbox, timeout=0.3) is None

    # same frame from somebody else's address: not delivered, endpoint unchanged
    other = UDPChannel()
    try:
        _raw_send(other, b, captured)
        assert _get(b.inbox, timeout=0.3) is None
        assert b.transport._endpoints[a.did] == a.channel.address
    finally:
        other.close()

    # fresh traffic still flows
    a.transport.route(a.did, b.did, b"next")
    assert _get(b.inbox) == (a.did, b"next")


def test_v1_replay_does_move_the_endpoint_which_v2_fixes():
    # The contrast case: without encryption a captured frame replayed from another
    # address is re-delivered and redirects the peer. That is the gap v2 closes.
    allow = Allowlist()
    a, b = EncNode(allow, encrypted=False), EncNode(allow, encrypted=False)
    allow.add(a.did)
    allow.add(b.did)
    a.know(b, binding=False)
    b.know(a, binding=False)
    other = UDPChannel()
    try:
        a.transport.route(a.did, b.did, b"hi")
        assert _get(b.inbox) == (a.did, b"hi")
        _raw_send(other, b, a.channel.sent[-1])
        assert _get(b.inbox) == (a.did, b"hi")
        assert b.transport._endpoints[a.did] == other.address
    finally:
        other.close()
        a.close()
        b.close()


def test_no_verified_key_means_nothing_is_sent():
    allow = Allowlist()
    a, b = EncNode(allow), EncNode(allow)
    allow.add(a.did)
    allow.add(b.did)
    a.know(b, binding=False)  # endpoint known, key not
    try:
        assert a.transport.route(a.did, b.did, SECRET) is False
        assert a.channel.sent == []
    finally:
        a.close()
        b.close()


def test_bad_bindings_are_refused(pair):
    a, b = pair
    # tampered: swap in another key without re-signing
    forged = dict(b.binding)
    forged["transport_public_key"] = public_key_b64(PrivateKey.generate())
    assert not a.transport.add_peer_binding(forged)
    # expired
    stale = create_binding(b.identity, public_key_b64(b.tkey), key_version=1,
                           valid_from=0.0, expires_at=1.0)
    assert not a.transport.add_peer_binding(stale)
    # a DID that is not on the allowlist
    outsider = Identity.generate()
    assert not a.transport.add_peer_binding(
        create_binding(outsider, public_key_b64(PrivateKey.generate()), key_version=1))
    # an older key_version than one already accepted
    newer_key = PrivateKey.generate()
    assert a.transport.add_peer_binding(
        create_binding(b.identity, public_key_b64(newer_key), key_version=2))
    assert not a.transport.add_peer_binding(b.binding)  # v1 < v2


def test_encrypted_node_drops_plaintext_v1(pair):
    a, b = pair
    _raw_send(a.channel, b, _encode_frame(a.identity, b.did, b"plaintext"))
    assert _get(b.inbox, timeout=0.3) is None


def test_direct_upgrade_still_works_encrypted(pair):
    a, b = pair
    session = Session("s-up", PeerIdentity(b.did, "unused"),
                      active=Endpoint("candidate", "relay.invalid", 1))
    up_a = DirectUpgrader(a.transport, session)
    up_b = DirectUpgrader(b.transport, Session("s-up-b", PeerIdentity(a.did, "unused")))

    def on_a(frm, msg):
        up_a.handle(frm, msg)

    def on_b(frm, msg):
        reply = up_b.handle(frm, msg)
        if reply is not None:
            b.transport.route(b.did, frm, reply)

    a.transport._inbound = on_a
    b.transport._inbound = on_b
    assert up_a.probe(b.did, Endpoint("candidate", *b.channel.address))
    deadline = time.time() + 2.0
    while time.time() < deadline and not up_a.state_for(b.did).is_direct:
        time.sleep(0.01)
    assert up_a.state_for(b.did).is_direct
    assert all(json.loads(f)["t"] == "secdogie/direct/v2" for f in a.channel.sent)
