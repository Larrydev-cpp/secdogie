"""Tests for the relay role (2C): any allowlisted node can serve as a relay, is
found through membership, and a client fails over between relays.

The data path is real UDP on 127.0.0.1; a fake clock drives leases, so renewal,
expiry and failover are deterministic."""
from __future__ import annotations

import base64
import itertools
import json
import queue
import time

import pytest

pytest.importorskip("nacl")

from nacl.public import PrivateKey  # noqa: E402
from secdogie_identity import Allowlist, Identity, sign_payload  # noqa: E402
from secdogie_identity.binding import create_binding  # noqa: E402
from secdogie_transport import (  # noqa: E402
    DirectUDPTransport,
    DirectUpgrader,
    Endpoint,
    MembershipView,
    PeerIdentity,
    RelayClient,
    RelayService,
    Session,
    UDPChannel,
    gossip_round,
)
from secdogie_transport.membership import (  # noqa: E402
    ROLE_RELAY,
    ROLE_RENDEZVOUS,
    announce,
    sign_record,
    verify_record,
)
from secdogie_transport.relay import (  # noqa: E402
    ACK_TIMEOUT,
    RELAY_ACK,
    RELAY_DELIVER,
    RELAY_REGISTER,
    RELAY_SEND,
    RENEW_EVERY,
)
from secdogie_transport.sealed import public_key_b64  # noqa: E402
from secdogie_transport.upgrade import decode_upgrade  # noqa: E402

SECRET = b"a journal entry nobody on the path should read"


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


_announcements = itertools.count(1)


class Node:
    """A mesh node: direct UDP transport, membership view, relay client, and --
    once `serve()` is called -- the relay role."""

    def __init__(self, allow, clock, *, encrypted=False, max_relays=2):
        self.allow = allow
        self.clock = clock
        self.identity = Identity.generate()
        self.did = self.identity.did
        allow.add(self.did)
        tkey = PrivateKey.generate() if encrypted else None
        self.binding = create_binding(self.identity, public_key_b64(tkey), key_version=1) if encrypted else None
        self.channel = UDPChannel()
        self.transport = DirectUDPTransport(self.identity, self.channel, allowlist=allow, transport_key=tkey)
        self.view = MembershipView(allowlist=allow)
        self.client = RelayClient(self.transport, self.view, allowlist=allow, clock=clock, max_relays=max_relays)
        self.service: RelayService | None = None
        self.direct_inbox: queue.Queue = queue.Queue()
        self.relay_inbox: queue.Queue = queue.Queue()
        me = self.self_session()
        self.transport.register(me, lambda frm, msg: self.direct_inbox.put((frm, msg)))
        self.client.register(me, lambda frm, msg: self.relay_inbox.put((frm, msg)))

    @property
    def endpoint(self):
        return Endpoint("local", *self.channel.address)

    def self_session(self):
        return Session("s-" + self.did[-6:], PeerIdentity(self.did, "unused"), active=self.endpoint)

    def serve(self, **kwargs):
        kwargs.setdefault("allowlist", self.allow)
        self.service = RelayService(self.transport, clock=self.clock, **kwargs)
        return self.service

    def announce(self, roles=None):
        if roles is None:
            roles = [ROLE_RELAY] if self.service is not None else []
        announce(self.identity, [self.endpoint], last_seen=self.clock() + next(_announcements) * 1e-3,
                 view=self.view, roles=roles, relays=self.client.relays())

    def close(self):
        self.channel.close()


@pytest.fixture
def mesh():
    clock, allow, nodes = Clock(), Allowlist(), []

    def make(**kwargs):
        node = Node(allow, clock, **kwargs)
        nodes.append(node)
        return node

    try:
        yield make, clock, allow
    finally:
        for node in nodes:
            node.close()


def converge(*nodes):
    """Every node announces itself, then one pairwise gossip pass spreads it all."""
    for node in nodes:
        node.announce()
    for a, b in itertools.combinations(nodes, 2):
        gossip_round(a.view, b.view)


def wait(pred, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline and not pred():
        time.sleep(0.01)
    return pred()


def lease_up(node, count=1):
    node.client.refresh()
    assert wait(lambda: len(node.client.relays()) >= count)


def get(q, timeout=2.0):
    try:
        return q.get(timeout=timeout)
    except queue.Empty:
        return None


def send_raw(channel, to_node, obj):
    channel.send(*to_node.channel.address, json.dumps(obj).encode("utf-8"))


def relay_send(sender, relay, dst, message=b"x"):
    inner = base64.b64encode(sender.transport.build_frame(dst.did, message)).decode("ascii")
    obj = {"t": RELAY_SEND, "from": sender.did, "to": relay.did, "dst": dst.did, "inner": inner}
    send_raw(sender.channel, relay, sign_payload(sender.identity, obj))


class Revocable:
    """The shape revocation plugs into: an allowlist minus revoked DIDs."""

    def __init__(self, base):
        self.base = base
        self.revoked: set[str] = set()

    def contains(self, did):
        return self.base.contains(did) and did not in self.revoked


# --- membership: roles + relay addresses (pure) ------------------------------


def test_roles_and_relays_are_self_signed_and_validated():
    allow = Allowlist()
    me, relay, other = Identity.generate(), Identity.generate(), Identity.generate()
    for ident in (me, relay, other):
        allow.add(ident.did)
    ep = [Endpoint("local", "10.0.0.1", 1)]

    rec = verify_record(sign_record(me, ep, last_seen=1.0, roles=[ROLE_RELAY], relays=[relay.did]), allowlist=allow)
    assert rec.roles == (ROLE_RELAY,) and rec.relays == (relay.did,)

    tampered = sign_record(me, ep, last_seen=1.0, roles=[ROLE_RELAY])
    tampered["relays"] = [other.did]  # a gossiping peer redirecting me through its relay
    assert verify_record(tampered, allowlist=allow) is None

    legacy = sign_record(me, ep, last_seen=1.0)  # no roles/relays: encodes exactly as before
    assert "roles" not in legacy and "relays" not in legacy
    assert verify_record(legacy, allowlist=allow).roles == ()

    # unknown roles are dropped on verify, even when validly signed
    odd = sign_payload(me, {"type": "secdogie/membership/record/v1", "did": me.did, "endpoints": [],
                            "last_seen": 1.0, "roles": [ROLE_RELAY, "root", 5], "relays": [me.did, 7]})
    rec = verify_record(odd, allowlist=allow)
    assert rec.roles == (ROLE_RELAY,) and rec.relays == ()

    with pytest.raises(ValueError):
        sign_record(me, ep, last_seen=1.0, roles=["root"])
    with pytest.raises(ValueError):
        sign_record(me, ep, last_seen=1.0, relays=[me.did])
    with pytest.raises(ValueError):
        sign_record(me, ep, last_seen=1.0, relays=[Identity.generate().did for _ in range(5)])


def test_providers_lists_role_holders_freshest_first():
    allow = Allowlist()
    old, new, plain = Identity.generate(), Identity.generate(), Identity.generate()
    for ident in (old, new, plain):
        allow.add(ident.did)
    view = MembershipView(allowlist=allow)
    view.merge_record(sign_record(old, [], last_seen=1.0, roles=[ROLE_RELAY]))
    view.merge_record(sign_record(new, [], last_seen=5.0, roles=[ROLE_RELAY, ROLE_RENDEZVOUS]))
    view.merge_record(sign_record(plain, [], last_seen=9.0))
    assert view.providers(ROLE_RELAY) == [new.did, old.did]
    assert view.providers(ROLE_RENDEZVOUS) == [new.did]


# --- relaying (loopback) ------------------------------------------------------


def test_any_allowlisted_node_relays_between_two_peers(mesh):
    make, clock, allow = mesh
    a, b, r = make(), make(), make()
    r.serve()
    converge(a, b, r)
    lease_up(a)
    lease_up(b)
    assert a.client.relays() == b.client.relays() == [r.did]
    assert r.service.clients() == sorted([a.did, b.did])
    converge(a, b, r)
    assert a.view.get(b.did).relays == (r.did,)

    assert a.client.route(a.did, b.did, b"hello via relay")
    assert get(b.relay_inbox) == (a.did, b"hello via relay")
    assert b.client.route(b.did, a.did, b"and back")
    assert get(a.relay_inbox) == (b.did, b"and back")
    assert wait(lambda: r.service.stats["forwarded"] == 2)

    # relayed traffic is kept apart from the direct path, and the relay's address
    # is never adopted as the sender's endpoint
    assert a.direct_inbox.empty() and b.direct_inbox.empty()
    assert a.did not in b.transport._endpoints and b.did not in a.transport._endpoints


def test_relay_carries_sealed_frames_it_cannot_read(mesh):
    make, clock, allow = mesh
    a, b, r = make(encrypted=True), make(encrypted=True), make()
    r.serve()
    assert a.transport.add_peer_binding(b.binding) and b.transport.add_peer_binding(a.binding)
    converge(a, b, r)
    lease_up(b)
    converge(a, b, r)

    seen = []
    r.transport.on_frame(RELAY_SEND, lambda obj, addr: (seen.append(obj), r.service._on_send(obj, addr)))
    assert a.client.route(a.did, b.did, SECRET)
    assert get(b.relay_inbox) == (a.did, SECRET)
    assert SECRET not in base64.b64decode(seen[0]["inner"])

    # the captured send, replayed from elsewhere: the relay forwards it again,
    # but B's replay window drops the inner frame
    replayer = UDPChannel()
    try:
        send_raw(replayer, r, seen[0])
        assert wait(lambda: r.service.stats["forwarded"] == 2)
        assert get(b.relay_inbox, timeout=0.3) is None
    finally:
        replayer.close()


def test_failover_to_another_relay_when_one_withdraws(mesh):
    make, clock, allow = mesh
    a, b, r1, r2 = make(max_relays=1), make(), make(), make()
    relays = {r1.did: r1, r2.did: r2}
    r1.serve()
    r2.serve()
    converge(a, b, r1, r2)
    lease_up(a)  # A holds one lease...
    lease_up(b, 2)  # ...B holds two
    converge(a, b, r1, r2)
    primary = relays[a.client.relays()[0]]
    backup = r2 if primary is r1 else r1
    assert a.view.get(b.did).relays == (primary.did, backup.did)

    assert a.client.route(a.did, b.did, b"one")
    assert get(b.relay_inbox) == (a.did, b"one")
    assert wait(lambda: primary.service.stats["forwarded"] == 1)

    primary.service.stop()  # the NAS hosting it goes to sleep
    clock.advance(RENEW_EVERY)
    a.client.refresh()
    b.client.refresh()  # heartbeats: only the backup answers
    assert wait(lambda: backup.service.stats["registered"] == 2)
    clock.advance(ACK_TIMEOUT + 1)
    assert a.client.refresh() == []  # primary given up on; a lease with the backup is on its way
    assert b.client.refresh() == [backup.did]

    # Before gossip catches up, B's record still names the dead relay first. A
    # saw it fail, so it goes through the backup -- without a lease there yet.
    assert a.view.get(b.did).relays[0] == primary.did
    assert a.client.route(a.did, b.did, b"two")
    assert get(b.relay_inbox) == (a.did, b"two")
    assert wait(lambda: backup.service.stats["forwarded"] == 1)

    converge(a, b, r1, r2)
    assert a.view.get(b.did).relays == (backup.did,)


def test_a_node_that_advertises_the_role_but_does_not_serve_is_skipped(mesh):
    make, clock, allow = mesh
    b, liar = make(), make()
    liar.announce(roles=[ROLE_RELAY])  # claims the role; no RelayService behind it
    b.announce()
    gossip_round(b.view, liar.view)
    assert b.client.refresh() == []
    clock.advance(ACK_TIMEOUT + 1)
    assert b.client.refresh() == []
    assert liar.did not in b.client._leases  # given up on, and skipped for a while


# --- zero trust ---------------------------------------------------------------


def test_roles_fail_closed_without_an_allowlist(mesh):
    make, clock, allow = mesh
    node = make()
    with pytest.raises(ValueError):
        RelayService(node.transport, allowlist=None)
    with pytest.raises(ValueError):
        RelayClient(node.transport, node.view, allowlist=None)
    for direct_type in ("secdogie/direct/v1", "secdogie/direct/v2"):
        with pytest.raises(ValueError):
            node.transport.on_frame(direct_type, lambda obj, addr: None)


def test_relay_serves_only_allowlisted_registered_peers_checked_on_every_forward(mesh):
    make, clock, allow = mesh
    a, b, r = make(), make(), make()
    policy = Revocable(allow)
    r.serve(allowlist=policy)
    converge(a, b, r)

    outsider = Identity.generate()
    probe = UDPChannel()
    try:
        send_raw(probe, r, sign_payload(outsider, {"t": RELAY_REGISTER, "from": outsider.did,
                                                   "to": r.did, "ts": clock()}))
        assert wait(lambda: r.service.stats["dropped_unauthenticated"] == 1)
    finally:
        probe.close()
    assert r.service.clients() == []

    relay_send(a, r, b)  # B holds no lease: nowhere to deliver
    assert wait(lambda: r.service.stats["dropped_no_route"] == 1)

    lease_up(b)
    relay_send(a, r, b, b"ok")
    assert get(b.relay_inbox) == (a.did, b"ok")

    policy.revoked = {b.did}  # destination revoked after it registered
    relay_send(a, r, b, b"to revoked")
    assert wait(lambda: r.service.stats["dropped_unauthorized"] == 1)
    policy.revoked = {a.did}  # sender revoked
    relay_send(a, r, b, b"from revoked")
    assert wait(lambda: r.service.stats["dropped_unauthenticated"] == 2)
    assert get(b.relay_inbox, timeout=0.3) is None


def test_node_accepts_deliveries_only_from_its_relays_and_unaltered(mesh):
    make, clock, allow = mesh
    a, b, r, rogue = make(), make(), make(), make()
    r.serve()
    converge(a, b, r, rogue)
    lease_up(b)

    def deliver(via, src, frame):
        obj = {"t": RELAY_DELIVER, "from": via.did, "to": b.did, "src": src,
               "inner": base64.b64encode(frame).decode("ascii")}
        send_raw(via.channel, b, sign_payload(via.identity, obj))

    genuine = a.transport.build_frame(b.did, b"from a")
    deliver(rogue, a.did, genuine)  # allowlisted, but not a relay B asked for
    deliver(r, rogue.did, genuine)  # B's relay, but src relabelled
    assert get(b.relay_inbox, timeout=0.3) is None
    deliver(r, a.did, genuine)
    assert get(b.relay_inbox) == (a.did, b"from a")


def test_register_must_be_fresh_so_a_replay_cannot_redirect_a_client(mesh):
    make, clock, allow = mesh
    b, r = make(), make()
    r.serve()
    register = sign_payload(b.identity, {"t": RELAY_REGISTER, "from": b.did, "to": r.did, "ts": clock()})
    send_raw(b.channel, r, register)
    assert wait(lambda: r.service.clients() == [b.did])
    home = r.service._clients[b.did].addr

    attacker = UDPChannel()
    try:
        send_raw(attacker, r, register)  # captured and replayed from another address
        send_raw(attacker, r, sign_payload(b.identity, {"t": RELAY_REGISTER, "from": b.did,
                                                        "to": r.did, "ts": clock() - 3600}))
        nan = json.dumps(sign_payload(b.identity, {"t": RELAY_REGISTER, "from": b.did,
                                                   "to": r.did, "ts": float("nan")}))
        attacker.send(*r.channel.address, nan.encode("utf-8"))  # NaN would defeat both checks
        assert wait(lambda: r.service.stats["dropped_replayed"] == 1 and r.service.stats["dropped_stale"] == 2)
    finally:
        attacker.close()
    assert r.service._clients[b.did].addr == home


def test_client_ignores_acks_that_do_not_echo_its_latest_request(mesh):
    make, clock, allow = mesh
    b, r = make(), make()
    r.serve()
    converge(b, r)
    lease_up(b)
    first_ts = b.client._leases[r.did].ts
    r.service.stop()
    clock.advance(RENEW_EVERY)
    b.client.refresh()  # heartbeat goes unanswered...
    replayed = sign_payload(r.identity, {"t": RELAY_ACK, "from": r.did, "to": b.did, "lease": 60.0,
                                         "echo": first_ts})
    send_raw(r.channel, b, replayed)  # ...and an old ack must not keep the lease alive
    time.sleep(0.2)
    clock.advance(ACK_TIMEOUT + 1)
    assert b.client.refresh() == []


def test_relay_rate_limits_each_sender(mesh):
    make, clock, allow = mesh
    a, b, r = make(), make(), make()
    r.serve(rate=1.0, burst=3.0)
    converge(a, b, r)
    lease_up(b)
    stats = r.service.stats

    for i in range(10):
        relay_send(a, r, b, b"%d" % i)
    assert wait(lambda: stats["forwarded"] + stats["dropped_rate_limited"] == 10)
    assert stats["forwarded"] == 3
    clock.advance(2.0)  # refills at 1 frame/s
    for _ in range(3):
        relay_send(a, r, b)
    assert wait(lambda: stats["forwarded"] + stats["dropped_rate_limited"] == 13)
    assert stats["forwarded"] == 5


# --- DirectUpgrader on the mesh relay ---------------------------------------


def wire_upgrader(node, peer):
    peer_session = Session("p-" + peer.did[-6:], PeerIdentity(peer.did, "unused"),
                           active=Endpoint("candidate", "relay.invalid", 1))
    upgrader = DirectUpgrader(node.transport, peer_session, relay=node.client)
    got = {"direct": [], "relay": []}

    def on_direct(frm, msg):
        reply = upgrader.handle(frm, msg)
        if reply is not None:
            node.transport.route(node.did, frm, reply)
        elif decode_upgrade(msg) is None:
            got["direct"].append(msg)

    def on_relay(frm, msg):
        if not upgrader.handle_relay(frm, msg, my_endpoints=[node.endpoint]):
            got["relay"].append(msg)

    me = node.self_session()
    node.transport.register(me, on_direct)
    node.client.register(me, on_relay)
    return upgrader, got


def test_upgrader_coordinates_over_the_mesh_relay_then_goes_direct(mesh):
    make, clock, allow = mesh
    a, b, r = make(), make(), make()
    r.serve()
    converge(a, b, r)
    lease_up(a)
    lease_up(b)
    converge(a, b, r)
    ua, got_a = wire_upgrader(a, b)
    ub, got_b = wire_upgrader(b, a)

    assert ua.send(b.did, b"before upgrade") == "relay"
    assert wait(lambda: got_b["relay"] == [b"before upgrade"])

    assert ua.connect(b.did, [a.endpoint])  # CONNECT travels through R; both sides dial
    assert wait(lambda: ua.state_for(b.did).is_direct and ub.state_for(a.did).is_direct)
    assert ua.send(b.did, b"after upgrade") == "direct"
    assert wait(lambda: got_b["direct"] == [b"after upgrade"])
    assert ub.send(a.did, b"reply") == "direct"
    assert wait(lambda: got_a["direct"] == [b"reply"])
