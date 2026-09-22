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
