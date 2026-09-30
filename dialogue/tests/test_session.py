"""The dialogue session over a bad link: reliable packets arrive exactly once
despite loss, duplication and reordering; big envelopes are fragmented; a
message that cannot get through is reported, never assumed delivered;
snapshots are not retransmitted; liveness is tracked; hostile frames are
harmless. Deterministic: a seeded lossy link and a fake clock. One real-UDP
round trip at the end."""
from __future__ import annotations

import queue
import random

import pytest

pytest.importorskip("nacl")

from secdogie_dialogue.protocol import (  # noqa: E402
    DialoguePacket,
    DialogueType,
    NodeDelta,
    NodeOp,
    Sender,
    SessionEvent,
    SessionPacket,
    StateSnapshotPacket,
)
from secdogie_dialogue.session import DialogueSession, SessionRouter, fragments  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402

T0 = 1_700_000_000 * 10**9


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def wall(self):
        return T0 + int(self.t * 1e9)


class Link:
    """Frames in flight between two sessions, delivered by pump() with seeded
    loss / duplication / reordering."""

    def __init__(self, seed=7, drop=0.0, dup=0.0, reorder=False):
        self.rng = random.Random(seed)
        self.drop, self.dup, self.reorder = drop, dup, reorder
        self.inflight: list = []
        self.sent = 0

    def sender(self, dst):
        def send(frame: bytes) -> bool:
            self.sent += 1
            self.inflight.append((dst, frame))
            return True
        return send

    def pump(self):
        batch, self.inflight = self.inflight, []
        if self.reorder:
            self.rng.shuffle(batch)
        for dst, frame in batch:
            if self.rng.random() < self.drop:
                continue
            dst.receive(frame)
            if self.rng.random() < self.dup:
                dst.receive(frame)


APP, NODE = Identity.generate(), Identity.generate()


def _pair(link, clock, **kw):
    holder = {}
    app = DialogueSession(APP, NODE.did, lambda f: link.sender(holder["node"])(f), trust=Allowlist({NODE.did}),
                          clock=clock, wall_ns=clock.wall, **kw)
    node = DialogueSession(NODE, APP.did, link.sender(app), trust=Allowlist({APP.did}),
                           clock=clock, wall_ns=clock.wall, **kw)
    holder["node"] = node
    got_app, got_node = [], []
    app.on_envelope = got_app.append
    node.on_envelope = got_node.append
    return app, node, got_app, got_node


def _run(link, clock, sessions, *, rounds=200, step=0.25):
    for _ in range(rounds):
        link.pump()
        clock.t += step
        for s in sessions:
            s.tick()
        if not link.inflight and all(s.pending == 0 for s in sessions):
            link.pump()
            return


def _probe(i):
    return DialoguePacket(f"p{i}", DialogueType.SOCRATIC_QUESTION, f"question {i}?")


# ---- reliability -----------------------------------------------------------------------


def test_reliable_packets_arrive_exactly_once_over_a_bad_link():
    clock, link = Clock(), Link(drop=0.3, dup=0.2, reorder=True)
    app, node, _, got_node = _pair(link, clock, retry_max=12)
    for i in range(30):
        app.send(_probe(i))
    _run(link, clock, [app, node])
    ids = [e.packet.probe_id for e in got_node if e.kind.value == "dialogue"]
    assert sorted(ids) == sorted(f"p{i}" for i in range(30))  # each exactly once
    assert app.pending == 0


def test_acknowledged_messages_are_not_resent():
    clock, link = Clock(), Link()
    app, node, _, got_node = _pair(link, clock, heartbeat_interval=1e9)
    app.send(_probe(1))
    link.pump()  # the fragment arrives; the ack goes back
    link.pump()
    assert app.pending == 0 and len(got_node) == 1
    before = link.sent
    clock.t += 30
    app.tick()
    node.tick()
    assert link.sent == before  # nothing retransmitted


def test_a_duplicate_is_acknowledged_again_but_delivered_once():
    clock, link = Clock(), Link()
    app, node, _, got_node = _pair(link, clock, heartbeat_interval=1e9)
    app.send(_probe(1))
    (dst, frame), = link.inflight
    node.receive(frame)
    node.receive(frame)
    acks = [f for d, f in link.inflight if f[:1] == b"A"]
    assert len(got_node) == 1 and len(acks) == 2


def test_an_undeliverable_message_is_reported_after_the_last_attempt_never_before():
    clock, link = Clock(), Link(drop=1.0)
    app, node, _, _ = _pair(link, clock, retry_max=4, heartbeat_interval=1e9)
    failed = []
    app.on_undeliverable = lambda msg_id, packet: failed.append((msg_id, packet))
    msg_id = app.send(_probe(1))
    attempts = 1
    for _ in range(100):
        clock.t += 0.25
        before = link.sent
        app.tick()
        attempts += link.sent - before
        if failed:
            break
        assert not failed
    assert failed == [(msg_id, _probe(1))]
    assert attempts == 4 and app.pending == 0


def test_snapshots_are_fire_and_forget():
    clock, link = Clock(), Link(drop=1.0)
    app, node, _, _ = _pair(link, clock, heartbeat_interval=1e9)
    failed = []
    node.on_undeliverable = lambda *a: failed.append(a)
    node.send(StateSnapshotPacket(1, 1, 2, (NodeDelta(NodeOp.REMOVE, 3),), base_generation=1))
    sent = link.sent
    for _ in range(40):
        clock.t += 0.5
        node.tick()
    assert link.sent == sent and node.pending == 0 and failed == []


def test_a_large_envelope_is_fragmented_and_reassembled():
    clock, link = Clock(), Link(reorder=True)
    app, node, got_app, _ = _pair(link, clock, heartbeat_interval=1e9)
    nodes = tuple(NodeDelta(NodeOp.ADD, i, role="AXStaticText", name=f"label {i} " + "x" * 40,
                            parent_index=-1 if i == 0 else 0) for i in range(600))
    snap = StateSnapshotPacket(42, 7, 1, nodes, full=True)
    node.send(snap, reliable=True)
    assert len(link.inflight) > 2  # really fragmented
    _run(link, clock, [app, node])
    (env,) = got_app
    assert env.packet == snap


# ---- liveness ------------------------------------------------------------------------------


def test_a_silent_peer_is_reported_down_once_and_up_when_heard_again():
    clock, link = Clock(), Link()
    app, node, _, _ = _pair(link, clock, heartbeat_interval=1.0, dead_after=3)
    events = []
    app.on_peer_down = lambda: events.append("down")
    app.on_peer_up = lambda: events.append("up")
    for _ in range(10):  # the node says nothing
        clock.t += 1.0
        app.tick()
        link.inflight.clear()
    assert events == ["down"] and not app.alive
    node.tick()  # the node's heartbeat
    link.pump()
    assert events == ["down", "up"] and app.alive


def test_no_retransmission_before_the_backoff_is_due():
    clock, link = Clock(), Link(drop=1.0)
    app, node, _, _ = _pair(link, clock, retry_base=1.0, heartbeat_interval=1e9)
    app.send(_probe(1))
    sent = link.sent
    clock.t += 0.5  # the first retry is due at 1.0
    app.tick()
    assert link.sent == sent
    clock.t += 0.6
    app.tick()
    assert link.sent == sent + 1


def test_heartbeats_go_out_at_the_interval_not_every_tick():
    clock, link = Clock(), Link()
    app, node, _, _ = _pair(link, clock, heartbeat_interval=1.0)
    sent = link.sent
    for _ in range(8):  # 0.25 .. 2.0: heartbeats due at 1.0 and 2.0
        clock.t += 0.25
        app.tick()
    assert link.sent - sent == 2


def test_heartbeats_keep_both_sides_alive_and_are_not_delivered():
    clock, link = Clock(), Link()
    app, node, got_app, got_node = _pair(link, clock, heartbeat_interval=1.0, dead_after=3)
    for _ in range(20):
        clock.t += 1.0
        app.tick()
        node.tick()
        link.pump()
    assert app.alive and node.alive and got_app == [] and got_node == []


# ---- hostile input -----------------------------------------------------------------------


def test_hostile_and_malformed_frames_are_harmless():
    clock, link = Clock(), Link()
    app, node, _, got_node = _pair(link, clock, max_reassemblies=4)
    frames = [b"", b"D", b"A123", b"Zjunk", b"D" + b"\x00" * 13,
              fragments(1, b"x", reliable=True)[0].replace(b"\x00\x01\x01", b"\x00\x01\x00", 1),  # total 0
              b"D" + (5).to_bytes(8, "big") + (3).to_bytes(2, "big") + (2).to_bytes(2, "big") + b"\x01x",  # idx>=total
              b"D" + (6).to_bytes(8, "big") + (0).to_bytes(2, "big") + (0xFFFF).to_bytes(2, "big") + b"\x01x",  # huge
              fragments(7, b"not json", reliable=True)[0],
              fragments(8, b'{"header": 1}', reliable=True)[0]]
    for f in frames:
        node.receive(f)
    for i in range(10):  # more concurrent reassemblies than allowed
        node.receive(fragments(100 + i, b"y" * 40000, reliable=False, size=16 * 1024)[0])
    assert got_node == [] and len(node._reassembly) <= 4


def test_a_mismatched_or_oversized_fragment_is_dropped():
    clock, link = Clock(), Link()
    app, node, _, got_node = _pair(link, clock)
    f1, f2 = fragments(9, b"a" * 20000, reliable=False, size=16 * 1024)
    lie = f2[:1 + 8 + 2] + (3).to_bytes(2, "big") + f2[1 + 8 + 2 + 2:]  # claims a different total
    node.receive(f1)
    node.receive(lie)
    assert node._reassembly[9].parts.keys() == {0}
    # an oversized chunk of a two-part message: never stored (with total=1 it
    # would complete at once and vanish either way, proving nothing)
    node.receive(b"D" + (10).to_bytes(8, "big") + (0).to_bytes(2, "big") + (2).to_bytes(2, "big") + b"\x00"
                 + b"z" * (16 * 1024 + 1))
    assert 10 not in node._reassembly


def test_a_fragment_outside_its_message_or_impossibly_large_starts_nothing():
    clock, link = Clock(), Link()
    app, node, _, got_node = _pair(link, clock)

    def hdr(mid, idx, total):
        return b"D" + mid.to_bytes(8, "big") + idx.to_bytes(2, "big") + total.to_bytes(2, "big") + b"\x00"

    node.receive(hdr(21, 1, 1) + b"x")  # index past the end of a one-fragment message
    node.receive(hdr(22, 0, 0xFFFF) + b"x")  # would exceed the message size limit
    assert node._reassembly == {} and got_node == []


def test_a_duplicated_fragment_is_stored_and_counted_once():
    clock, link = Clock(), Link()
    app, node, _, _ = _pair(link, clock)
    f0, _ = fragments(24, b"c" * 20000, reliable=False, size=16 * 1024)
    node.receive(f0)
    node.receive(f0)
    r = node._reassembly[24]
    assert r.parts.keys() == {0} and r.size == 16 * 1024  # not counted twice against the limit


def test_stale_reassemblies_expire():
    clock, link = Clock(), Link()
    app, node, _, _ = _pair(link, clock, reassembly_timeout=5.0, heartbeat_interval=1e9)
    node.receive(fragments(11, b"b" * 20000, reliable=False, size=16 * 1024)[0])
    clock.t += 6
    node.tick()
    assert 11 not in node._reassembly


def test_a_trusted_key_that_is_not_this_sessions_peer_is_not_delivered():
    clock, link = Clock(), Link()
    other = Identity.generate()
    node = DialogueSession(NODE, APP.did, link.sender(None), trust=Allowlist({APP.did, other.did}),
                           clock=clock, wall_ns=clock.wall)
    got = []
    node.on_envelope = got.append
    import json

    sealed = Sender(other, NODE.did, clock_ns=clock.wall).seal(_probe(1))
    for f in fragments(1, json.dumps(sealed).encode(), reliable=False):
        node.receive(f)
    assert got == []


def test_a_session_needs_a_trust_policy():
    with pytest.raises(ValueError):
        DialogueSession(APP, NODE.did, lambda f: True, trust=None)


# ---- routing and a real round trip -------------------------------------------------------


def test_router_over_a_real_udp_loopback():
    pytest.importorskip("secdogie_transport")
    from secdogie_transport import ChannelMux, DirectUDPTransport, Endpoint, PeerIdentity, Session, UDPChannel

    allow = Allowlist({APP.did, NODE.did})
    nets = {}
    for ident in (APP, NODE):
        ch = UDPChannel()
        tr = DirectUDPTransport(ident, ch, allowlist=allow)
        mux = ChannelMux(tr, Session("s", PeerIdentity(ident.did, "x"), active=Endpoint("local", *ch.address)))
        nets[ident.did] = (ch, tr, mux)
    nets[APP.did][1].set_peer_endpoint(NODE.did, *nets[NODE.did][0].address)
    nets[NODE.did][1].set_peer_endpoint(APP.did, *nets[APP.did][0].address)

    inbox_node, inbox_app = queue.Queue(), queue.Queue()
    node_router = None

    def accept(did):
        if did != APP.did:  # the node only opens sessions for the App it trusts
            return None
        s = DialogueSession(NODE, did, node_router.sender_for(did), trust=Allowlist({APP.did}))
        s.on_envelope = lambda env: (inbox_node.put(env.packet),
                                     s.send(DialoguePacket("r1", DialogueType.SYSTEM_STATUS, "got it",
                                                           in_reply_to=env.packet.probe_id)))
        s.start(0.05)
        return s

    node_router = SessionRouter(nets[NODE.did][2], accept=accept)
    app_router = SessionRouter(nets[APP.did][2])
    app = app_router.add(DialogueSession(APP, NODE.did, app_router.sender_for(NODE.did),
                                         trust=Allowlist({NODE.did})))
    app.on_envelope = lambda env: inbox_app.put(env.packet)
    app.start(0.05)
    try:
        app.send(DialoguePacket("hello", DialogueType.SOCRATIC_QUESTION, "are you there?"))
        assert inbox_node.get(timeout=3).probe_id == "hello"
        reply = inbox_app.get(timeout=3)
        assert reply.in_reply_to == "hello" and reply.content == "got it"
    finally:
        app.close()
        for s in node_router.sessions():
            s.close()
        for ch, _, _ in nets.values():
            ch.close()


def test_a_bye_is_best_effort_and_close_stops_ticking():
    clock, link = Clock(), Link()
    app, node, _, got_node = _pair(link, clock)
    app.close()
    link.pump()
    assert [e.packet.event for e in got_node] == [SessionEvent.BYE]
    assert SessionPacket(SessionEvent.BYE) == got_node[0].packet
