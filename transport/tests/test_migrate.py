"""P2P.2: relay -> direct Session migration, and signed-timestamp checks on every
direct UDP frame during the hole-punch.

Pure tests (freshness, Session path/epoch, frame codecs, the upgrader against a
fake direct transport) plus headless 127.0.0.1 loopback tests: a make-before-break
migration where relay traffic sent before the switch still arrives, a hole-punch
that fails closed when a peer's clock is out of skew, and replayed / stale
PROBE traffic that must never upgrade a path or move an endpoint."""
from __future__ import annotations

import base64
import json
import queue
import time

import pytest

pytest.importorskip("nacl")

from nacl.public import PrivateKey  # noqa: E402
from secdogie_identity import Allowlist, Identity, sign_payload  # noqa: E402
from secdogie_transport import (  # noqa: E402
    PATH_DIRECT,
    PATH_RELAY,
    DirectUDPTransport,
    DirectUpgrader,
    Endpoint,
    HubTransport,
    PeerIdentity,
    Session,
    UDPChannel,
    is_fresh,
)
from secdogie_transport.sealed import make_box, open_sealed, seal  # noqa: E402
from secdogie_transport.udp import _decode_frame, _encode_frame  # noqa: E402
from secdogie_transport.upgrade import (  # noqa: E402
    PROBING,
    RELAYED,
    decode_upgrade,
    encode_probe,
    encode_probe_ack,
)

RELAY_EP = Endpoint("candidate", "relay.invalid", 1)


def _wait(pred, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline and not pred():
        time.sleep(0.01)
    return pred()


# --- freshness (pure) -------------------------------------------------------


def test_is_fresh_accepts_only_int_ms_within_skew():
    now = 1_000.0  # seconds
    assert is_fresh(1_000_000, now=now, max_skew=30)
    assert is_fresh(1_000_000 - 30_000, now=now, max_skew=30)       # exactly at the edge
    assert not is_fresh(1_000_000 - 30_001, now=now, max_skew=30)   # stale
    assert not is_fresh(1_000_000 + 30_001, now=now, max_skew=30)   # future-dated
    for bad in (None, True, "1000000", 1_000_000.0, [1]):
        assert not is_fresh(bad, now=now, max_skew=30)              # fail closed


# --- Session path migration (pure) ------------------------------------------


def _peer_session() -> Session:
    return Session("s-1", PeerIdentity("did:key:zPeer", "unused"), active=RELAY_EP)


def test_session_starts_relayed_and_migrates_to_direct_keeping_identity():
    s = _peer_session()
    assert s.path == PATH_RELAY and s.epoch == 0 and not s.is_direct
    direct = Endpoint("observed", "203.0.113.7", 41000)
    s.migrate(direct, path=PATH_DIRECT)
    assert s.is_direct and s.active == direct and s.epoch == 1
    assert s.relay_endpoint == RELAY_EP                 # remembered for fallback
    assert (s.session_id, s.did) == ("s-1", "did:key:zPeer")

    # a NAT rebind while direct: new endpoint, same path, no epoch bump
    rebound = Endpoint("observed", "203.0.113.7", 41001)
    s.migrate(rebound)
    assert s.is_direct and s.active == rebound and s.epoch == 1

    # fallback restores the relay endpoint; a second fallback is a no-op
    assert s.fall_back() is True
    assert s.path == PATH_RELAY and s.active == RELAY_EP and s.epoch == 2
    assert s.fall_back() is False and s.epoch == 2
    assert (s.session_id, s.did) == ("s-1", "did:key:zPeer")


def test_session_rejects_unknown_path():
    with pytest.raises(ValueError):
        Session("s", PeerIdentity("did:key:z", "k"), path="teleport")
    with pytest.raises(ValueError):
        _peer_session().migrate(RELAY_EP, path="teleport")


# --- frame codecs: signed ts (pure) -----------------------------------------


def test_v1_frame_timestamp_is_signed_and_checked():
    a, b = Identity.generate(), Identity.generate()
    allow = Allowlist()
    allow.add(a.did)
    now = 1_700_000_000.0
    frame = _encode_frame(a, b.did, b"probe", ctr=7, ts=int(now * 1000))
    assert _decode_frame(frame, allow, b.did, now=now) == (a.did, 7, b"probe")
    assert _decode_frame(frame, allow, b.did, now=now + 31) is None     # captured, replayed late
    assert _decode_frame(frame, allow, b.did, now=now - 31) is None     # sender clock ahead

    # rewriting ts to look fresh breaks the signature
    obj = json.loads(frame)
    obj["ts"] = int((now + 60) * 1000)
    assert _decode_frame(json.dumps(obj).encode(), allow, b.did, now=now + 60) is None

    # a validly signed frame that simply has no ts fails closed
    unstamped = sign_payload(a, {"t": "secdogie/direct/v1", "from": a.did, "to": b.did,
                                 "ctr": 1, "data": ""})
    assert _decode_frame(json.dumps(unstamped).encode(), allow, b.did, now=now) is None


def test_v2_sealed_frame_timestamp_is_checked_before_decryption():
    a, b = Identity.generate(), Identity.generate()
    ka, kb = PrivateKey.generate(), PrivateKey.generate()
    allow = Allowlist()
    allow.add(a.did)
    now = 1_700_000_000.0
    frame = seal(a, make_box(ka, kb.public_key), b.did, 9, b"secret", ts=int(now * 1000))
    box_for = {a.did: make_box(kb, ka.public_key)}.get
    opened = open_sealed(frame, allowlist=allow, self_did=b.did, box_for=box_for, now=now)
    assert opened == (a.did, 9, b"secret")
    assert open_sealed(frame, allowlist=allow, self_did=b.did, box_for=box_for, now=now + 31) is None
    assert open_sealed(frame, allowlist=allow, self_did=b.did, box_for=box_for,
                       now=now + 31, max_skew=60) == opened


# --- upgrader against a fake direct transport (pure) ------------------------


class FakeDirect:
    """Records what the upgrader asks of the direct transport; sends nothing."""

    def __init__(self):
        self.identity = Identity.generate()
        self.dialed: list[tuple[str, Endpoint, bytes]] = []
        self.routes: dict[str, tuple[str, int]] = {}

    def route_to(self, to_did, endpoint, message):
        self.dialed.append((to_did, endpoint, message))
        return True

    def set_peer_endpoint(self, did, host, port):
        self.routes[did] = (host, port)

    def route(self, from_did, to_did, message):
        return to_did in self.routes


class Clock:
    def __init__(self, t=1_000.0):
        self.t = t

    def __call__(self):
        return self.t


def _upgrader(peer_did="did:key:zPeer", clock=None):
    session = Session("s-1", PeerIdentity(peer_did, "unused"), active=RELAY_EP)
    fake = FakeDirect()
    return DirectUpgrader(fake, session, probe_timeout=5.0, clock=clock or Clock()), fake, session


def _nonce_of(message: bytes) -> str:
    return decode_upgrade(message)["nonce"]


def test_probe_dials_without_touching_the_current_route():
    up, fake, session = _upgrader()
    ep = Endpoint("candidate", "198.51.100.2", 5000)
    assert up.probe("did:key:zPeer", ep)
    assert fake.dialed[0][:2] == ("did:key:zPeer", ep)
    assert fake.routes == {}                        # unproven: route unchanged
    assert session.path == PATH_RELAY and session.active == RELAY_EP
    assert up.state_for("did:key:zPeer").state == PROBING


def test_probe_nonces_are_random_and_single_use():
    up, fake, session = _upgrader()
    ep = Endpoint("candidate", "198.51.100.2", 5000)
    up.probe("did:key:zPeer", ep)
    up.probe("did:key:zPeer", ep)
    n1, n2 = (_nonce_of(m) for _, _, m in fake.dialed)
    assert n1 != n2 and len(n1) == 32 and "did:" not in n1
    up.handle("did:key:zPeer", encode_probe_ack(n1))
    assert session.is_direct and session.epoch == 1
    up.sweep(now=10_000.0, dead_after=1e9)          # drop the other probe
    up.handle("did:key:zPeer", encode_probe_ack(n1))  # replayed nonce: already consumed
    assert session.epoch == 1


def test_ack_from_a_different_peer_or_unknown_nonce_does_not_upgrade():
    up, fake, session = _upgrader()
    up.probe("did:key:zPeer", Endpoint("candidate", "198.51.100.2", 5000))
    nonce = _nonce_of(fake.dialed[0][2])
    # another allowlisted peer echoing the nonce (e.g. it saw the probe) is refused
    up.handle("did:key:zMallory", encode_probe_ack(nonce))
    assert not up.state_for("did:key:zMallory").is_direct
    assert not up.state_for("did:key:zPeer").is_direct
    up.handle("did:key:zPeer", encode_probe_ack("guessed-nonce"))
    assert not up.state_for("did:key:zPeer").is_direct and fake.routes == {}
    # the genuine ACK still works afterwards
    up.handle("did:key:zPeer", encode_probe_ack(nonce))
    assert session.is_direct and fake.routes["did:key:zPeer"] == ("198.51.100.2", 5000)


def test_while_direct_an_ack_on_another_endpoint_does_not_flap():
    up, fake, session = _upgrader()
    first = Endpoint("candidate", "198.51.100.2", 5000)
    second = Endpoint("candidate", "10.0.0.2", 5000)
    up.probe("did:key:zPeer", first)
    up.probe("did:key:zPeer", second)
    n_first, n_second = (_nonce_of(m) for _, _, m in fake.dialed)
    up.handle("did:key:zPeer", encode_probe_ack(n_first))
    up.handle("did:key:zPeer", encode_probe_ack(n_second))
    assert fake.routes["did:key:zPeer"] == first.key()
    assert session.active.key() == first.key() and session.epoch == 1


def test_sweep_times_out_probes_then_downgrades_a_silent_direct_path():
    clock = Clock(1_000.0)
    up, fake, session = _upgrader(clock=clock)
    up.probe("did:key:zPeer", Endpoint("candidate", "198.51.100.2", 5000))
    assert up.sweep(now=1_004.0) == [] and up.state_for("did:key:zPeer").state == PROBING
    assert up.sweep(now=1_006.0) == []                  # probe expired: back to RELAYED
    assert up.state_for("did:key:zPeer").state == RELAYED and session.path == PATH_RELAY

    up.probe("did:key:zPeer", Endpoint("candidate", "198.51.100.2", 5000))
    up.handle("did:key:zPeer", encode_probe_ack(_nonce_of(fake.dialed[-1][2])))
    assert session.is_direct and session.epoch == 1
    assert up.sweep(now=1_020.0, dead_after=30.0) == []  # still fresh
    assert up.sweep(now=1_031.0, dead_after=30.0) == ["did:key:zPeer"]
    assert session.path == PATH_RELAY and session.active == RELAY_EP and session.epoch == 2


def test_timeout_falls_back_but_never_downgrades_a_direct_peer():
    up, fake, session = _upgrader()
    up.probe("did:key:zPeer", Endpoint("candidate", "198.51.100.2", 5000))
    up.handle("did:key:zPeer", encode_probe_ack(_nonce_of(fake.dialed[-1][2])))
    up.keepalive("did:key:zPeer")                   # a re-probe in flight...
    up.on_timeout("did:key:zPeer")                  # ...times out
    assert session.is_direct and up.state_for("did:key:zPeer").is_direct


# --- headless loopback ------------------------------------------------------


class RecordingChannel(UDPChannel):
    def __init__(self):
        super().__init__()
        self.sent: list[tuple[tuple[str, int], bytes]] = []

    def send(self, host, port, data):
        self.sent.append(((host, port), data))
        super().send(host, port, data)


class Node:
    """A node on an in-memory relay and a real loopback UDP socket, whose clock
    can be skewed. Relay traffic drives CONNECT; direct traffic drives the
    upgrader; application messages are recorded with the path they arrived on."""

    def __init__(self, relay, allow, *, skew=0.0):
        self.identity = Identity.generate()
        self.did = self.identity.did
        self.channel = RecordingChannel()
        self.direct = DirectUDPTransport(self.identity, self.channel, allowlist=allow,
                                         clock=lambda: time.time() + skew)
        self.relay = relay
        self.inbox: queue.Queue = queue.Queue()
        self.probes_seen = 0

    @property
    def endpoint(self):
        return Endpoint("candidate", *self.channel.address)

    def bind_peer(self, peer_did):
        self.session = Session("s-" + self.did[-4:] + peer_did[-4:],
                               PeerIdentity(peer_did, "unused"), active=RELAY_EP)
        self.upgrader = DirectUpgrader(self.direct, self.session, relay=self.relay)

        def on_direct(frm, msg):
            if (decode_upgrade(msg) or {}).get("t", "").endswith("/probe/v1"):
                self.probes_seen += 1
            reply = self.upgrader.handle(frm, msg)
            if reply is not None:
                self.direct.route(self.did, frm, reply)
            elif decode_upgrade(msg) is None:
                self.inbox.put(("direct", msg))

        def on_relay(frm, msg):
            if not self.upgrader.handle_relay(frm, msg, my_endpoints=[self.endpoint]):
                self.inbox.put(("relay", msg))

        me = PeerIdentity(self.did, "unused")
        self.direct.register(Session("d-" + self.did[-4:], me, active=self.endpoint), on_direct)
        self.relay.register(Session("r-" + self.did[-4:], me, active=RELAY_EP), on_relay)

    def drain(self, n, timeout=2.0):
        out = []
        for _ in range(n):
            try:
                out.append(self.inbox.get(timeout=timeout))
            except queue.Empty:
                break
        return out

    def close(self):
        self.channel.close()


@pytest.fixture
def mesh():
    nodes = []
    allow = Allowlist()
    relay = HubTransport(allowlist=allow)

    def make(*, skew=0.0):
        n = Node(relay, allow, skew=skew)
        allow.add(n.did)
        nodes.append(n)
        return n

    yield make
    for n in nodes:
        n.close()


def _pair(make, *, skew_b=0.0):
    a, b = make(), make(skew=skew_b)
    a.bind_peer(b.did)
    b.bind_peer(a.did)
    return a, b


def test_make_before_break_migration_relay_to_direct_and_back(mesh):
    a, b = _pair(mesh)
    sid = a.session.session_id

    # before the upgrade: relay only
    assert a.upgrader.send(b.did, b"m1") == "relay"
    assert a.upgrader.connect(b.did, [a.endpoint])
    assert _wait(lambda: a.session.is_direct and b.session.is_direct)

    # identity unchanged, one path change, the relay endpoint remembered
    assert a.session.session_id == sid and a.session.did == b.did
    assert a.session.epoch == 1 and a.session.relay_endpoint == RELAY_EP
    assert a.session.active.key() == b.channel.address

    # after the switch: direct; nothing sent before it was lost
    assert a.upgrader.send(b.did, b"m2") == "direct"
    assert b.drain(2) == [("relay", b"m1"), ("direct", b"m2")]

    # the relay was never torn down: a peer still on it can reach us
    assert b.relay.route(b.did, a.did, b"late-relay")
    assert a.drain(1) == [("relay", b"late-relay")]

    # silence -> fallback to the relay endpoint, same session
    assert a.upgrader.sweep(now=time.time() + 1e6) == [b.did]
    assert a.session.path == PATH_RELAY and a.session.active == RELAY_EP
    assert a.session.epoch == 2 and a.session.session_id == sid
    assert a.upgrader.send(b.did, b"m3") == "relay"
    assert b.drain(1) == [("relay", b"m3")]


def test_every_hole_punch_datagram_is_signed_and_timestamped(mesh):
    a, b = _pair(mesh)
    a.upgrader.connect(b.did, [a.endpoint])
    assert _wait(lambda: a.session.is_direct and b.session.is_direct)
    frames = [json.loads(raw) for _, raw in a.channel.sent + b.channel.sent]
    kinds = {decode_upgrade(base64.b64decode(f["data"]))["t"].rsplit("/", 2)[1]
             for f in frames}
    assert {"probe", "probe-ack"} <= kinds
    for f in frames:
        assert f["signer"] == f["from"] and f["sig"]
        assert isinstance(f["ts"], int) and abs(f["ts"] - time.time() * 1000) < 5_000
        assert isinstance(f["ctr"], int)


def test_hole_punch_fails_closed_when_a_peer_clock_is_out_of_skew(mesh):
    # B's clock is two minutes ahead: its PROBEs look future-dated to A, and A's
    # look stale to B, so no direct path is ever proven -- both stay on the relay.
    a, b = _pair(mesh, skew_b=120.0)
    a.upgrader.connect(b.did, [a.endpoint])
    assert _wait(lambda: len(a.channel.sent) >= 1 and len(b.channel.sent) >= 1)
    time.sleep(0.3)
    assert a.probes_seen == 0 and b.probes_seen == 0
    assert not a.session.is_direct and not b.session.is_direct
    a.upgrader.sweep(now=time.time() + 60)
    assert a.upgrader.state_for(b.did).state == RELAYED and a.session.path == PATH_RELAY
    assert a.upgrader.send(b.did, b"still-reachable") == "relay"
    assert b.drain(1) == [("relay", b"still-reachable")]


def test_replayed_probe_is_dropped_and_does_not_move_the_endpoint(mesh):
    a, b = _pair(mesh)
    a.upgrader.probe(b.did, b.endpoint)
    assert _wait(lambda: a.session.is_direct)
    probe_frame = next(raw for dst, raw in a.channel.sent if dst == b.channel.address)
    seen = b.probes_seen

    attacker = UDPChannel()
    try:
        attacker.send(*b.channel.address, probe_frame)  # same signed PROBE, other address
        time.sleep(0.3)
        assert b.probes_seen == seen                      # dropped by the replay window
        assert b.direct._endpoints[a.did] == a.channel.address
    finally:
        attacker.close()


def test_stale_signed_probe_is_dropped_even_with_a_new_counter(mesh):
    a, b = _pair(mesh)
    old = int((time.time() - 120) * 1000)
    fresh = int(time.time() * 1000)
    stale = _encode_frame(a.identity, b.did, encode_probe("n-stale"), ctr=10**20, ts=old)
    a.channel.send(*b.channel.address, stale)
    time.sleep(0.3)
    assert b.probes_seen == 0
    ok = _encode_frame(a.identity, b.did, encode_probe("n-fresh"), ctr=10**20 + 1, ts=fresh)
    a.channel.send(*b.channel.address, ok)
    assert _wait(lambda: b.probes_seen == 1)
