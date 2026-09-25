"""Tests for direct-connection upgrade + relay fallback (P2P.2).

Pure state-machine and codec tests, plus two real-loopback tests: a probe/ack
round-trip that migrates a peer session relay -> direct, and a dead endpoint that
leaves the session on the relay."""
from __future__ import annotations

import time

import pytest

pytest.importorskip("nacl")

from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_transport import (  # noqa: E402
    DirectUDPTransport,
    DirectUpgrader,
    Endpoint,
    PeerIdentity,
    Session,
    UDPChannel,
    UpgradeState,
)
from secdogie_transport.upgrade import (  # noqa: E402
    PROBING,
    RELAYED,
    decode_upgrade,
    encode_probe,
    encode_probe_ack,
)

# --- pure state machine -----------------------------------------------------


def test_upgrade_state_transitions():
    st = UpgradeState("did:key:zPeer")
    assert st.state == RELAYED and not st.is_direct
    st.begin_probe()
    assert st.state == PROBING
    ep = Endpoint("observed", "203.0.113.4", 5000)
    st.on_ack(ep)
    assert st.is_direct and st.direct_endpoint == ep
    # once direct, a stray timeout must not knock it back down
    st.on_timeout()
    assert st.is_direct


def test_upgrade_state_timeout_falls_back_to_relay():
    st = UpgradeState("did:key:zP")
    st.begin_probe()
    st.on_timeout()
    assert st.state == RELAYED and st.direct_endpoint is None


# --- codec ------------------------------------------------------------------


def test_probe_codec_roundtrip_and_passthrough():
    assert decode_upgrade(encode_probe("n1"))["t"].endswith("probe/v1")
    assert decode_upgrade(encode_probe_ack("n1"))["t"].endswith("probe-ack/v1")
    # ordinary application data is not an upgrade message
    assert decode_upgrade(b"hello over UDP") is None
    assert decode_upgrade(b'{"t":"something-else"}') is None
    assert decode_upgrade(b"\xff\xfe not json") is None


# --- real loopback ----------------------------------------------------------


class Node:
    """A node with a DirectUDPTransport and one peer session + upgrader. Its
    inbound handler dispatches upgrade messages and routes any reply back."""

    def __init__(self, allow, peer_did_placeholder="pending"):
        self.identity = Identity.generate()
        self.did = self.identity.did
        self.channel = UDPChannel()
        self.transport = DirectUDPTransport(self.identity, self.channel, allowlist=allow)
        self.peer_session: Session | None = None
        self.upgrader: DirectUpgrader | None = None

    def bind_peer(self, peer_did: str):
        # a peer session that starts life on a (fake) relay endpoint
        self.peer_session = Session(
            "s-" + self.did[-4:] + peer_did[-4:],
            PeerIdentity(peer_did, "unused"),
            active=Endpoint("candidate", "relay.invalid", 1),
        )
        self.upgrader = DirectUpgrader(self.transport, self.peer_session)

        def inbound(frm, msg):
            reply = self.upgrader.handle(frm, msg)
            if reply is not None:
                self.transport.route(self.did, frm, reply)

        self_session = Session("self-" + self.did[-4:], PeerIdentity(self.did, "unused"),
                               active=Endpoint("local", *self.channel.address))
        self.transport.register(self_session, inbound)

    def close(self):
        self.channel.close()


def test_direct_upgrade_migrates_session_to_the_direct_endpoint():
    allow = Allowlist()
    a, b = Node(allow), Node(allow)
    allow.add(a.did)
    allow.add(b.did)
    a.bind_peer(b.did)
    b.bind_peer(a.did)
    try:
        assert a.peer_session.active.host == "relay.invalid"  # starts relayed
        # A probes B's real (loopback) endpoint, as rendezvous would have provided.
        a.upgrader.probe(b.did, Endpoint("candidate", *b.channel.address))
        assert a.upgrader.state_for(b.did).state == PROBING
        # wait for probe -> ack -> migrate
        deadline = time.time() + 2.0
        while time.time() < deadline and not a.upgrader.state_for(b.did).is_direct:
            time.sleep(0.01)
        assert a.upgrader.state_for(b.did).is_direct
        # the peer session now points at B's direct endpoint, identity unchanged
        assert (a.peer_session.active.host, a.peer_session.active.port) == b.channel.address
        assert a.peer_session.active.kind == "observed"
        assert a.peer_session.peer.did == b.did  # same peer/session, just a new path
    finally:
        a.close()
        b.close()


def test_dead_endpoint_stays_on_relay():
    allow = Allowlist()
    a = Node(allow)
    allow.add(a.did)
    stranger = Identity.generate()
    allow.add(stranger.did)
    a.bind_peer(stranger.did)
    # a dead port: bind then close so nothing answers
    dead = UDPChannel()
    dead_addr = dead.address
    dead.close()
    try:
        a.upgrader.probe(stranger.did, Endpoint("candidate", *dead_addr))
        time.sleep(0.3)  # no one answers
        assert not a.upgrader.state_for(stranger.did).is_direct
        a.upgrader.on_timeout(stranger.did)  # probe window elapsed
        assert a.upgrader.state_for(stranger.did).state == RELAYED
        assert a.peer_session.active.host == "relay.invalid"  # never migrated
    finally:
        a.close()


# --- liveness / downgrade (pure) --------------------------------------------


def test_direct_path_expires_back_to_relay_when_silent():
    from secdogie_transport.upgrade import DIRECT
    st = UpgradeState("did:key:zP")
    st.on_ack(Endpoint("observed", "203.0.113.9", 6000), now=100.0)
    assert st.is_direct and st.last_seen == 100.0
    # still fresh -> no downgrade
    assert st.expire(now=120.0, dead_after=30.0) is False and st.is_direct
    # an inbound packet refreshes it
    st.touch(now=125.0)
    assert st.expire(now=150.0, dead_after=30.0) is False
    # then it goes silent -> downgrade to relay, endpoint cleared
    assert st.expire(now=200.0, dead_after=30.0) is True
    assert st.state == RELAYED and st.direct_endpoint is None
    # a stray expire after downgrade is a no-op
    assert st.expire(now=999.0, dead_after=30.0) is False
    assert DIRECT == "direct"


def test_connect_codec_roundtrip():
    from secdogie_transport.upgrade import decode_upgrade, encode_connect
    raw = encode_connect([Endpoint("candidate", "10.0.0.2", 5001)], reply=True)
    msg = decode_upgrade(raw)
    assert msg["t"].endswith("connect/v1") and msg["reply"] is True
    assert msg["endpoints"][0] == {"kind": "candidate", "host": "10.0.0.2", "port": 5001}


# --- relay-mediated connect + route selection + fallback (loopback) ---------


class RelayNode:
    """A node reachable over an in-memory relay (HubTransport) and, once upgraded,
    directly over UDP. Inbound direct datagrams drive the upgrader; inbound relay
    datagrams drive the CONNECT coordination and carry application data."""

    def __init__(self, relay, allow):
        self.identity = Identity.generate()
        self.did = self.identity.did
        self.channel = UDPChannel()
        self.direct = DirectUDPTransport(self.identity, self.channel, allowlist=allow)
        self.relay = relay
        self.app_direct: list[bytes] = []
        self.app_relay: list[bytes] = []
        self.upgrader: DirectUpgrader | None = None

    @property
    def endpoint(self):
        return Endpoint("candidate", *self.channel.address)

    def bind_peer(self, peer_did: str):
        self.peer_session = Session(
            "s-" + self.did[-4:] + peer_did[-4:],
            PeerIdentity(peer_did, "unused"),
            active=Endpoint("candidate", "relay.invalid", 1),
        )
        self.upgrader = DirectUpgrader(self.direct, self.peer_session, relay=self.relay)

        def on_direct(frm, msg):
            reply = self.upgrader.handle(frm, msg)
            if reply is not None:
                self.direct.route(self.did, frm, reply)
            elif reply is None and _is_app(msg):
                self.app_direct.append(msg)

        def on_relay(frm, msg):
            if not self.upgrader.handle_relay(frm, msg, my_endpoints=[self.endpoint]):
                self.app_relay.append(msg)

        self.direct.register(
            Session("self-d-" + self.did[-4:], PeerIdentity(self.did, "unused"),
                    active=Endpoint("local", *self.channel.address)),
            on_direct,
        )
        self.relay.register(
            Session("self-r-" + self.did[-4:], PeerIdentity(self.did, "unused"),
                    active=Endpoint("candidate", "relay", 1)),
            on_relay,
        )

    def close(self):
        self.channel.close()


def _is_app(msg: bytes) -> bool:
    from secdogie_transport.upgrade import decode_upgrade
    return decode_upgrade(msg) is None


def _wait(pred, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline and not pred():
        time.sleep(0.01)
    return pred()


def test_relay_connect_upgrades_both_sides_then_routes_direct_and_falls_back():
    from secdogie_transport import HubTransport
    allow = Allowlist()
    relay = HubTransport(allowlist=allow)
    a, b = RelayNode(relay, allow), RelayNode(relay, allow)
    for did in (a.did, b.did):
        allow.add(did)
    a.bind_peer(b.did)
    b.bind_peer(a.did)
    try:
        # both peers behind "NAT": neither has the other's direct endpoint yet.
        # A initiates a relay-mediated CONNECT; B answers and both dial.
        assert a.upgrader.connect(b.did, [a.endpoint]) is True
        assert _wait(lambda: a.upgrader.state_for(b.did).is_direct
                             and b.upgrader.state_for(a.did).is_direct)

        # an application message now takes the direct path on both sides
        assert a.upgrader.send(b.did, b"hello-b") == "direct"
        assert _wait(lambda: b.app_direct == [b"hello-b"])
        assert b.upgrader.send(a.did, b"hello-a") == "direct"
        assert _wait(lambda: a.app_direct == [b"hello-a"])

        # the direct path goes silent -> sweep downgrades it back to the relay,
        # and the next application message falls back to the relay (fallback works
        # AFTER an upgrade, not only before it)
        downgraded = a.upgrader.sweep(now=1e12, dead_after=30.0)
        assert b.did in downgraded
        assert a.upgrader.state_for(b.did).state == RELAYED
        assert a.upgrader.send(b.did, b"over-relay") == "relay"
        assert _wait(lambda: b.app_relay == [b"over-relay"])
    finally:
        a.close()
        b.close()


def test_send_uses_relay_before_any_upgrade():
    from secdogie_transport import HubTransport
    allow = Allowlist()
    relay = HubTransport(allowlist=allow)
    a, b = RelayNode(relay, allow), RelayNode(relay, allow)
    for did in (a.did, b.did):
        allow.add(did)
    a.bind_peer(b.did)
    b.bind_peer(a.did)
    try:
        # no upgrade attempted: RELAYED from the start -> message goes via relay
        assert a.upgrader.state_for(b.did).state == RELAYED
        assert a.upgrader.send(b.did, b"first") == "relay"
        assert _wait(lambda: b.app_relay == [b"first"])
    finally:
        a.close()
        b.close()
