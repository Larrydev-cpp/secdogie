"""Replication.1 -- signed journal / StateStore convergence over a transport.

Two layers of coverage:

  * Pure protocol (citadel + identity, always runs in CI): an in-process "wire"
    hands each peer's ``send`` to the other's ``on_message`` (json round-tripping
    the payload, exactly as a real transport would), so two -- and three, via a
    hub -- journals converge and their ``StateStore``s materialize the *same*
    entities. Also: idempotent after convergence, and authenticity (an
    unauthorized author's or a tampered event is dropped on merge).
  * Real UDP loopback (skipped where ``secdogie_transport`` is absent, e.g. the
    citadel-only CI job): the same ``ReplicationPeer`` wired over two
    ``DirectUDPTransport``s on 127.0.0.1 drives real datagrams to convergence.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("nacl")

from secdogie_citadel import sync  # noqa: E402
from secdogie_citadel.journal import Journal  # noqa: E402
from secdogie_citadel.replication import ReplicationPeer  # noqa: E402
from secdogie_citadel.state import StateStore, record_state  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402


def _counter(start=0.0):
    n = {"t": start}

    def clock():
        n["t"] += 1.0
        return n["t"]

    return clock


def _materialize(journal) -> dict:
    """Fold a journal's `state` events into materialized entities."""
    store = StateStore()
    store.merge_events(journal.events())
    return store.materialize()


class Wire:
    """An in-process message bus. Each peer's ``send(to_did, payload)`` enqueues a
    json-round-tripped copy (proving the messages are transport-serializable);
    ``drain`` delivers everything queued to the addressed peer's ``on_message``,
    following the cascade until the exchange goes silent. Returns the total number
    of events merged across all deliveries."""

    def __init__(self):
        self._peers: dict[str, ReplicationPeer] = {}
        self._queue: list[tuple[str, str, dict]] = []

    def attach(self, did: str, journal) -> ReplicationPeer:
        peer = ReplicationPeer(journal, self._sender(did))
        self._peers[did] = peer
        return peer

    def _sender(self, from_did: str):
        def send(to_did: str, payload: dict) -> None:
            # json round-trip: what a socket would carry, not a shared dict ref.
            self._queue.append((from_did, to_did, json.loads(json.dumps(payload))))

        return send

    def drain(self) -> int:
        merged = 0
        while self._queue:
            from_did, to_did, payload = self._queue.pop(0)
            merged += self._peers[to_did].on_message(from_did, payload)
        return merged


# --- pure protocol convergence ---------------------------------------------


def test_two_peers_converge_and_materialize_same_entities():
    a, b = Identity.generate(), Identity.generate()
    allow = Allowlist({a.did, b.did})
    ja = Journal(identity=a, allowlist=allow, clock=_counter())
    jb = Journal(identity=b, allowlist=allow, clock=_counter(100.0))

    # each node writes its own signed state; neither has seen the other's
    record_state(ja, "goal", "g1", "set", {"title": "learn"})
    record_state(jb, "task", "t1", "set", {"of": "g1"})

    wire = Wire()
    pa = wire.attach(a.did, ja)
    wire.attach(b.did, jb)

    pa.initiate(b.did)
    wire.drain()

    # journals converged
    assert ja.heads() == jb.heads()
    assert set(ja.heads()) == {a.did, b.did}
    # and the state materialized from each is identical, holding BOTH entities
    ma, mb = _materialize(ja), _materialize(jb)
    assert ma == mb
    assert ma["goal"]["g1"] == {"title": "learn"}
    assert ma["task"]["t1"] == {"of": "g1"}


def test_idempotent_after_convergence():
    a, b = Identity.generate(), Identity.generate()
    allow = Allowlist({a.did, b.did})
    ja = Journal(identity=a, allowlist=allow, clock=_counter())
    jb = Journal(identity=b, allowlist=allow, clock=_counter(100.0))
    record_state(ja, "goal", "g1", "set", {"n": 1})
    record_state(jb, "goal", "g2", "set", {"n": 2})

    wire = Wire()
    pa = wire.attach(a.did, ja)
    wire.attach(b.did, jb)

    pa.initiate(b.did)
    assert wire.drain() > 0            # first round actually moves events
    pa.initiate(b.did)
    assert wire.drain() == 0          # nothing new: converged and stable


def test_star_topology_converges_through_a_hub():
    a, b, c, hub = (Identity.generate() for _ in range(4))
    allow = Allowlist({a.did, b.did, c.did, hub.did})
    ja = Journal(identity=a, allowlist=allow, clock=_counter())
    jb = Journal(identity=b, allowlist=allow, clock=_counter(100.0))
    jc = Journal(identity=c, allowlist=allow, clock=_counter(200.0))
    jhub = Journal(identity=hub, allowlist=allow, clock=_counter(300.0))  # pure relay

    record_state(ja, "goal", "ga", "set", {"who": "a"})
    record_state(jb, "goal", "gb", "set", {"who": "b"})
    record_state(jc, "goal", "gc", "set", {"who": "c"})

    wire = Wire()
    spokes = [wire.attach(ident.did, j) for ident, j in ((a, ja), (b, jb), (c, jc))]
    wire.attach(hub.did, jhub)

    # each spoke only ever talks to the hub; two passes fan the union to everyone
    for _ in range(2):
        for spoke in spokes:
            spoke.initiate(hub.did)
            wire.drain()

    everyone = {a.did, b.did, c.did}
    for j in (ja, jb, jc, jhub):
        assert set(j.heads()) == everyone
    union = {"goal": {"ga": {"who": "a"}, "gb": {"who": "b"}, "gc": {"who": "c"}}}
    for j in (ja, jb, jc):
        assert _materialize(j) == union


# --- authenticity (per-event, independent of the channel) -------------------


def test_unauthorized_author_event_dropped_on_merge():
    a, rogue = Identity.generate(), Identity.generate()
    # a authorizes only itself; rogue authorizes both (so it accepts a's events)
    ja = Journal(identity=a, allowlist=Allowlist({a.did}), clock=_counter())
    jrogue = Journal(identity=rogue, allowlist=Allowlist({a.did, rogue.did}), clock=_counter(100.0))
    record_state(ja, "goal", "g1", "set", {})
    record_state(jrogue, "goal", "evil", "set", {"tamper": True})

    wire = Wire()
    wire.attach(a.did, ja)
    progue = wire.attach(rogue.did, jrogue)

    progue.initiate(a.did)
    wire.drain()

    # a never accepted the rogue's event; its state has no 'evil' entity
    assert set(ja.heads()) == {a.did}
    assert _materialize(ja) == {"goal": {"g1": {}}}


def test_tampered_event_dropped_on_merge():
    a, b = Identity.generate(), Identity.generate()
    allow = Allowlist({a.did, b.did})
    ja = Journal(identity=a, allowlist=allow, clock=_counter())
    jb = Journal(identity=b, allowlist=allow, clock=_counter(100.0))
    ev = record_state(ja, "goal", "g1", "set", {"safe": True})

    # forge the payload after signing -- entry_hash/signature no longer match
    tampered = {**ev, "body": {"entity_type": "goal", "entity_id": "g1",
                               "operation": "set", "payload": {"safe": False, "owned": True}}}
    pb = ReplicationPeer(jb, lambda *_: None)
    assert pb.on_message(a.did, {"kind": sync.EVENTS, "events": [tampered]}) == 0
    assert jb.heads() == {}
    assert _materialize(jb) == {}


# --- real UDP loopback (integration; skipped without the transport package) --


def test_real_udp_loopback_converges():
    pytest.importorskip("secdogie_transport")
    import time

    from secdogie_transport import Endpoint, PeerIdentity, Session
    from secdogie_transport.udp import DirectUDPTransport, UDPChannel

    a, b = Identity.generate(), Identity.generate()
    allow = Allowlist({a.did, b.did})
    ja = Journal(identity=a, allowlist=allow, clock=_counter())
    jb = Journal(identity=b, allowlist=allow, clock=_counter(100.0))
    record_state(ja, "goal", "g1", "set", {"who": "a"})
    record_state(jb, "task", "t1", "set", {"who": "b"})

    cha, chb = UDPChannel(), UDPChannel()
    ta = DirectUDPTransport(a, cha, allowlist=allow)
    tb = DirectUDPTransport(b, chb, allowlist=allow)

    def send_over(transport, from_did):
        def send(to_did: str, payload: dict) -> None:
            transport.route(from_did, to_did, json.dumps(payload).encode("utf-8"))

        return send

    pa = ReplicationPeer(ja, send_over(ta, a.did))
    pb = ReplicationPeer(jb, send_over(tb, b.did))

    def deliver_to(peer):
        def deliver(from_did: str, data: bytes) -> None:
            peer.on_message(from_did, json.loads(data.decode("utf-8")))

        return deliver

    ta.register(Session("sa", PeerIdentity(a.did, "unused"),
                        active=Endpoint("local", *cha.address)), deliver_to(pa))
    tb.register(Session("sb", PeerIdentity(b.did, "unused"),
                        active=Endpoint("local", *chb.address)), deliver_to(pb))
    ta.set_peer_endpoint(b.did, *chb.address)  # b's addr is learned via roaming

    try:
        pa.initiate(b.did)
        deadline = time.time() + 5.0
        converged = False
        while time.time() < deadline:
            try:
                if ja.heads() == jb.heads() and set(ja.heads()) == {a.did, b.did}:
                    converged = True
                    break
            except Exception:  # noqa: BLE001 -- cross-thread sqlite read, retry
                pass
            time.sleep(0.05)
    finally:
        cha.close()
        chb.close()

    assert converged, "journals did not converge over UDP within the deadline"
    # recv threads are stopped; safe to read on the main thread now
    assert _materialize(ja) == _materialize(jb)
    assert _materialize(ja)["goal"]["g1"] == {"who": "a"}
    assert _materialize(ja)["task"]["t1"] == {"who": "b"}


# --- large journals: size-bounded replies ------------------------------------


def _big_journal(identity, allow, n=300, note_bytes=500):
    j = Journal(identity=identity, allowlist=allow, clock=_counter())
    for i in range(n):
        record_state(j, "knowledge", f"k{i}", "set", {"note": "x" * note_bytes, "i": i})
    return j


def test_events_messages_are_bounded_contiguous_and_complete():
    a = Identity.generate()
    j = _big_journal(a, Allowlist({a.did}))
    events = j.events()
    assert len(json.dumps(sync.events_message(events))) > 150_000  # far over one datagram
    msgs = sync.events_messages(events, max_bytes=40_000)
    assert len(msgs) > 3
    assert all(len(json.dumps(m)) <= 40_000 for m in msgs)
    flat = [e for m in msgs for e in m["events"]]
    assert [e["seq"] for e in flat] == list(range(1, len(events) + 1))  # all, in order
    for m in msgs:
        seqs = [e["seq"] for e in m["events"]]
        assert seqs == list(range(seqs[0], seqs[0] + len(seqs)))  # contiguous run


def test_empty_and_oversized_cases():
    assert sync.events_messages([]) == [sync.events_message([])]
    a = Identity.generate()
    j = Journal(identity=a, clock=_counter())
    record_state(j, "knowledge", "big", "set", {"note": "y" * 5000})
    record_state(j, "knowledge", "small", "set", {"note": "z"})
    msgs = sync.events_messages(j.events(), max_bytes=1000)
    assert [len(m["events"]) for m in msgs] == [1, 1]  # the oversized one travels alone


def test_large_journal_converges_in_process():
    a, b = Identity.generate(), Identity.generate()
    allow = Allowlist({a.did, b.did})
    ja, jb = _big_journal(a, allow), Journal(identity=b, allowlist=allow, clock=_counter(1000.0))
    wire = Wire()
    pa = wire.attach(a.did, ja)
    wire.attach(b.did, jb)
    pa.initiate(b.did)
    assert wire.drain() == 300
    assert ja.heads() == jb.heads()


def _udp_sync(max_bytes, *, rounds=5, wait=1.0):
    """Two journals over real UDP on 127.0.0.1: A holds a large journal, B is
    empty. Runs up to `rounds` anti-entropy rounds; returns (converged, peer_a) --
    A is the side that replies with the large journal."""
    pytest.importorskip("secdogie_transport")
    import time

    from secdogie_transport import Endpoint, PeerIdentity, Session
    from secdogie_transport.udp import DirectUDPTransport, UDPChannel

    a, b = Identity.generate(), Identity.generate()
    allow = Allowlist({a.did, b.did})
    ja, jb = _big_journal(a, allow), Journal(identity=b, allowlist=allow, clock=_counter(1000.0))
    cha, chb = UDPChannel(), UDPChannel()
    ta, tb = DirectUDPTransport(a, cha, allowlist=allow), DirectUDPTransport(b, chb, allowlist=allow)

    def send_over(transport, from_did):
        return lambda to_did, payload: transport.route(from_did, to_did, json.dumps(payload).encode())

    pa = ReplicationPeer(ja, send_over(ta, a.did), max_bytes=max_bytes)
    pb = ReplicationPeer(jb, send_over(tb, b.did), max_bytes=max_bytes)
    ta.register(Session("sa", PeerIdentity(a.did, "u"), active=Endpoint("local", *cha.address)),
                lambda f, d: pa.on_message(f, json.loads(d)))
    tb.register(Session("sb", PeerIdentity(b.did, "u"), active=Endpoint("local", *chb.address)),
                lambda f, d: pb.on_message(f, json.loads(d)))
    ta.set_peer_endpoint(b.did, *chb.address)
    converged = False
    try:
        for _ in range(rounds):
            pa.initiate(b.did)  # a node repeats rounds; a lost/reordered chunk comes again
            deadline = time.time() + wait
            while time.time() < deadline:
                try:
                    if jb.heads().get(a.did) == 300:
                        converged = True
                        break
                except Exception:  # noqa: BLE001 -- cross-thread sqlite read, retry
                    pass
                time.sleep(0.05)
            if converged:
                break
    finally:
        cha.close()
        chb.close()
    return converged, pa


def test_large_journal_did_not_replicate_over_udp_unchunked():
    # The bug this fixes: one reply holding everything exceeds a datagram, the
    # send fails ("Message too long"), and B never receives A's events.
    converged, pa = _udp_sync(max_bytes=10**9, rounds=2, wait=0.5)
    assert not converged
    assert pa.send_failures > 0  # and the failure is now counted and logged


def test_large_journal_replicates_over_udp_chunked():
    converged, pa = _udp_sync(max_bytes=sync.DEFAULT_MAX_BYTES)
    assert converged
    assert pa.send_failures == 0
