"""MeshNode (2B): the transport pieces assembled into running peers.

Pure tests for the mux (fragmentation, bounded reassembly, MTU), the transport
hooks (`on_other`, closed socket), round-trip-only liveness and rendezvous replay
protection; then headless 127.0.0.1 multi-node tests with a shared fake clock:
direct upgrade, rendezvous as a peer role (no server), a node that can only be
reached over the relay, a direct path that dies and falls back, fragmentation
across both paths, and the sealed (v2) data plane."""
from __future__ import annotations

import time

import pytest

pytest.importorskip("nacl")

from nacl.public import PrivateKey  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_identity.binding import create_binding  # noqa: E402
from secdogie_transport import (  # noqa: E402
    DirectUDPTransport,
    DirectUpgrader,
    Endpoint,
    HubTransport,
    MeshNode,
    PeerIdentity,
    Session,
    UDPChannel,
    mux,
)
from secdogie_transport.rendezvous import RendezvousClient, RendezvousServer  # noqa: E402
from secdogie_transport.sealed import make_box, public_key_b64, seal  # noqa: E402
from secdogie_transport.udp import _encode_frame  # noqa: E402
from secdogie_transport.upgrade import RELAYED, decode_upgrade, encode_probe_ack  # noqa: E402

# --- mux (pure) -------------------------------------------------------------


def _roundtrip(body, did="did:key:zA"):
    r = mux.Reassembler()
    out = None
    dgrams = mux.encode("ch", body)
    for d in dgrams:
        got = r.feed(did, d)
        out = got if got is not None else out
    return dgrams, out


def test_small_message_is_one_datagram_and_large_one_fragments():
    dgrams, out = _roundtrip({"k": "v"})
    assert len(dgrams) == 1 and out == ("ch", {"k": "v"})
    body = {"blob": "x" * 20_000, "quotes": '"\\' * 2_000, "cjk": "中" * 500}
    dgrams, out = _roundtrip(body)
    assert len(dgrams) > 1 and out == ("ch", body)


def test_every_frame_fits_under_the_path_mtu_signed_and_sealed():
    a, b = Identity.generate(), Identity.generate()
    box = make_box(PrivateKey.generate(), PrivateKey.generate().public_key)
    body = {"worst": '"\\' * 5_000, "text": "abc\n\t" * 3_000}
    largest_single = max((mux.encode("member", {"x": "y" * n}) for n in range(700)
                          if len(mux.encode("member", {"x": "y" * n})) == 1), key=lambda d: len(d[0]))
    for d in mux.encode("repl", body) + largest_single:
        assert len(_encode_frame(a, b.did, d, ctr=2**63, ts=2**41)) <= 1400
        assert len(seal(a, box, b.did, 2**63, d, ts=2**41)) <= 1400


def test_fragments_do_not_mix_across_peers_and_bad_pieces_are_dropped():
    dgrams = mux.encode("ch", {"x": "y" * 5_000})
    r = mux.Reassembler()
    for d in dgrams[:-1]:
        assert r.feed("did:key:zA", d) is None
    assert r.feed("did:key:zB", dgrams[-1]) is None      # B cannot complete A's message
    assert r.feed("did:key:zA", dgrams[-1]) == ("ch", {"x": "y" * 5_000})
    assert r.feed("did:key:zA", b'{"t":"secdogie/mux/frag/v1","id":"i","i":5,"n":2,"d":""}') is None
    assert r.feed("did:key:zA", b'{"t":"secdogie/mux/frag/v1","id":"i","i":0,"n":99999,"d":""}') is None
    assert r.feed("did:key:zA", b"not mux") is None and r.pending("did:key:zA") == 0


def test_reassembly_is_bounded_and_expires():
    clock = [100.0]
    r = mux.Reassembler(max_pending=2, timeout=10.0, clock=lambda: clock[0])
    firsts = [mux.encode("ch", {"n": i, "pad": "p" * 3_000})[0] for i in range(3)]
    for d in firsts:
        r.feed("did:key:zA", d)
    assert r.pending("did:key:zA") == 2                   # oldest evicted
    clock[0] = 111.0
    assert r.expire() == 2 and r.pending() == 0


# --- transport hooks + liveness + rendezvous (pure / loopback) ---------------


def test_non_direct_datagrams_go_to_on_other_and_closed_socket_fails_soft():
    seen = []
    me, peer = Identity.generate(), Identity.generate()
    allow = Allowlist({me.did, peer.did})
    ch, other = UDPChannel(), UDPChannel()
    t = DirectUDPTransport(me, ch, allowlist=allow, on_other=lambda raw, addr: seen.append(raw))
    try:
        other.send(*ch.address, b'{"type":"secdogie/rendezvous/register/v1"}')
        other.send(*ch.address, _encode_frame(peer, me.did, b"direct"))  # a direct frame: not on_other
        assert _wait(lambda: len(seen) >= 1)
        time.sleep(0.1)
        assert seen == [b'{"type":"secdogie/rendezvous/register/v1"}']
        t.set_peer_endpoint(peer.did, *other.address)
        ch.close()
        assert t.route(me.did, peer.did, b"x") is False    # closed socket -> False, no exception
    finally:
        ch.close()
        other.close()


class _FakeDirect:
    def __init__(self):
        self.identity = Identity.generate()
        self.sent = []

    def route_to(self, to, ep, msg):
        self.sent.append(msg)
        return True

    def set_peer_endpoint(self, *a):
        pass

    def route(self, *a):
        return True


def test_liveness_is_refreshed_only_by_round_trips_not_by_inbound_traffic():
    clock = [1_000.0]
    up = DirectUpgrader(_FakeDirect(), Session("s", PeerIdentity("did:key:zP", "")),
                        clock=lambda: clock[0])
    up.probe("did:key:zP", Endpoint("candidate", "10.0.0.2", 5000))
    up.handle("did:key:zP", encode_probe_ack(decode_upgrade(up.direct.sent[-1])["nonce"]))
    assert up.state_for("did:key:zP").is_direct
    clock[0] = 1_025.0
    up.handle("did:key:zP", b"application data")          # one-way traffic: no refresh
    assert up.sweep(now=1_031.0, dead_after=30.0) == ["did:key:zP"]
    assert up.state_for("did:key:zP").state == RELAYED


def test_rendezvous_register_replay_and_stale_frames_are_rejected():
    server_id, a_id = Identity.generate(), Identity.generate()
    allow = Allowlist({a_id.did})
    now = [1_000.0]
    server = RendezvousServer(server_id, allowlist=allow, clock=lambda: now[0])
    client = RendezvousClient(a_id, server_id.did, clock=lambda: now[0])
    frame = client.register_frame([Endpoint("local", "10.0.0.5", 4000)])
    ack = server.on_register(frame, ("203.0.113.5", 40000))
    assert ack is not None and client.handle_register_ack(ack).host == "203.0.113.5"
    # the same REGISTER replayed from another address cannot re-point A
    assert server.on_register(frame, ("198.51.100.66", 6666)) is None
    assert server.known(a_id.did).best().host == "203.0.113.5"
    # a REGISTER older than the skew is refused; a fresh one is accepted
    now[0] = 1_100.0
    assert server.on_register(frame, ("203.0.113.5", 40000)) is None
    assert server.on_register(client.register_frame([]), ("203.0.113.5", 40001)) is not None
    # a stale reply is refused by the client
    now[0] = 1_200.0
    assert client.handle_register_ack(ack) is None


# --- multi-node loopback ----------------------------------------------------


class Clock:
    """One fake clock shared by every node, so signed timestamps stay mutually
    fresh while tests fast-forward the protocol timers."""

    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t


class Firewalled(UDPChannel):
    """A real UDP socket whose inbound / outbound can be cut, standing in for a
    NAT or firewall that blocks the direct path."""

    def __init__(self):
        super().__init__()
        self.block_in = False
        self.block_out = False

    def start(self, on_datagram):
        super().start(lambda d, a: None if self.block_in else on_datagram(d, a))

    def send(self, host, port, data):
        if not self.block_out:
            super().send(host, port, data)


TIMERS = dict(keepalive_interval=1.0, dead_after=3.0, probe_timeout=0.6,
              upgrade_retry=1.0, gossip_interval=1.0, sync_interval=1.0)


class KV:
    """A tiny anti-entropy protocol for tests: each node's dict converges."""

    def __init__(self, node):
        self.data: dict = {}
        self.node = node
        node.add_protocol("kv", self.on_message, on_sync=self.sync)

    def on_message(self, frm, body):
        if isinstance(body, dict):
            self.data.update(body)

    def sync(self, did):
        if self.data:
            self.node.send(did, "kv", dict(self.data))


@pytest.fixture
def mesh():
    made = []
    allow = Allowlist()
    relay = HubTransport(allowlist=allow)
    clock = Clock()

    def make(*, relay_on=True, **kw):
        ident = Identity.generate()
        allow.add(ident.did)
        ch = Firewalled()
        node = MeshNode(ident, ch, allowlist=allow, relay=relay if relay_on else None,
                        clock=clock, **{**TIMERS, **kw})
        node.kv = KV(node)
        made.append(node)
        return node

    make.clock = clock
    make.allow = allow
    yield make
    for n in made:
        n.close()


def _wait(pred, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline and not pred():
        time.sleep(0.01)
    return pred()


def pump(nodes, pred, clock, *, step=0.2, timeout=8.0):
    """Drive every node's tick while fast-forwarding the shared clock."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for n in nodes:
            n.tick()
        if pred():
            return True
        clock.t += step
        time.sleep(0.01)
    return pred()


def _ep(node):
    return Endpoint("local", *node.direct.channel.address)


def test_two_nodes_upgrade_to_direct_and_exchange_messages(mesh):
    a, b = mesh(relay_on=False), mesh(relay_on=False)
    a.add_peer(b.did, [_ep(b)])
    a.kv.data["from-a"] = 1
    assert pump([a, b], lambda: b.kv.data.get("from-a") == 1, mesh.clock)
    assert a.path(b.did) == "direct"
    # B learned A through the direct traffic + gossip and upgraded too
    assert pump([a, b], lambda: b.path(a.did) == "direct", mesh.clock)
    assert a.session_for(b.did).is_direct and a.session_for(b.did).epoch >= 1


def test_rendezvous_is_a_peer_role_and_introduces_two_nodes(mesh):
    # no server anywhere: A is an ordinary node that also serves rendezvous
    a = mesh(relay_on=False, serve_rendezvous=True)
    b, c = mesh(relay_on=False), mesh(relay_on=False)
    for n in (b, c):
        n.use_rendezvous(a.did, *a.direct.channel.address)
    def registered():
        return (a.rendezvous.known(b.did) and a.rendezvous.known(c.did)
                and any(e.kind == "observed" for e in b.endpoints.all()))  # B learned its reflexive endpoint
    assert pump([a, b, c], registered, mesh.clock)
    b.lookup(c.did)
    b.kv.data["hello"] = "c"
    assert pump([a, b, c], lambda: c.kv.data.get("hello") == "c", mesh.clock)
    assert b.path(c.did) == "direct"


def test_three_nodes_converge_when_one_is_reachable_only_over_the_relay(mesh):
    a, b, c = mesh(), mesh(), mesh()
    c.direct.channel.block_in = True          # C is behind a NAT nobody can punch
    a.add_peer(b.did, [_ep(b)])
    a.add_peer(c.did, [_ep(c)])
    for i, n in enumerate((a, b, c)):
        n.kv.data[f"k{i}"] = n.did
    want = {f"k{i}": n.did for i, n in enumerate((a, b, c))}
    assert pump([a, b, c], lambda: all(n.kv.data == want for n in (a, b, c)), mesh.clock)
    assert a.path(b.did) == "direct"
    assert a.path(c.did) == "relay" and c.path(a.did) == "relay"
    # membership converged too: everyone knows everyone
    assert pump([a, b, c], lambda: all(len(n.membership.known()) == 3 for n in (a, b, c)), mesh.clock)


def test_dead_direct_path_falls_back_to_the_relay_and_keeps_converging(mesh):
    a, b = mesh(), mesh()
    a.add_peer(b.did, [_ep(b)])
    assert pump([a, b], lambda: a.path(b.did) == "direct" and b.path(a.did) == "direct", mesh.clock)
    # the direct path dies in one direction only: B still hears A, A hears nothing
    b.direct.channel.block_out = True
    assert pump([a, b], lambda: a.path(b.did) == "relay", mesh.clock)
    assert a.session_for(b.did).path == "relay"
    b.kv.data["after"] = "fallback"
    assert pump([a, b], lambda: a.kv.data.get("after") == "fallback", mesh.clock)


def test_large_messages_fragment_over_direct_and_relay(mesh):
    a, b, c = mesh(), mesh(), mesh()
    c.direct.channel.block_in = True
    a.add_peer(b.did, [_ep(b)])
    a.add_peer(c.did)
    assert pump([a, b, c], lambda: a.path(b.did) == "direct", mesh.clock)
    big = {"blob": "z" * 30_000}
    assert a.send(b.did, "kv", big) == "direct"
    assert a.send(c.did, "kv", big) == "relay"
    assert pump([a, b, c], lambda: b.kv.data.get("blob") == big["blob"]
                and c.kv.data.get("blob") == big["blob"], mesh.clock)


def test_unauthorized_peers_are_refused(mesh):
    a = mesh()
    stranger = Identity.generate()
    assert a.add_peer(stranger.did) is False
    assert a.send(stranger.did, "kv", {"x": 1}) == "unauthorized"
    with pytest.raises(ValueError):
        a.add_protocol("member", lambda *x: None)


def test_sealed_mesh_exchanges_encrypted_frames():
    allow = Allowlist()
    clock = Clock()
    nodes = []
    try:
        for _ in range(2):
            ident, tkey = Identity.generate(), PrivateKey.generate()
            allow.add(ident.did)
            n = MeshNode(ident, Firewalled(), allowlist=allow, transport_key=tkey, clock=clock, **TIMERS)
            n.binding = create_binding(ident, public_key_b64(tkey), key_version=1)
            n.kv = KV(n)
            nodes.append(n)
        a, b = nodes
        assert a.add_peer(b.did, [_ep(b)], binding=b.binding)
        assert b.add_peer(a.did, binding=a.binding)
        a.kv.data["secret"] = "sealed"
        assert pump(nodes, lambda: b.kv.data.get("secret") == "sealed", clock)
        assert a.path(b.did) == "direct" and a.direct.encrypted
    finally:
        for n in nodes:
            n.close()


def test_background_threads_run_the_mesh(mesh):
    a, b = mesh(relay_on=False), mesh(relay_on=False)
    a.add_peer(b.did, [_ep(b)])
    a.kv.data["bg"] = True
    a.start(tick_every=0.05)
    b.start(tick_every=0.05)
    try:
        # real time now; the shared clock only moves when we move it
        def advance():
            mesh.clock.t += 0.2
            return b.kv.data.get("bg") is True
        assert _wait(advance, timeout=8.0)
    finally:
        a.stop()
        b.stop()
