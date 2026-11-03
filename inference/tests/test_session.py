"""Sessions and memory bounds: every request has an explicit, bounded life, and
nothing a node or user holds grows with what arrives."""
from __future__ import annotations

from dataclasses import replace

import pytest

pytest.importorskip("nacl")

import sim  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_inference import (  # noqa: E402
    OUTCOME_ACCEPTED,
    OUTCOME_REFUSED,
    make_incident,
    make_request,
    make_reset,
)
from secdogie_inference.health import FaultTracker  # noqa: E402
from secdogie_inference.lease import (  # noqa: E402
    MAX_LEASE_TTL,
    LeaseBook,
    LeaseSecret,
    lease_shape,
    make_lease,
    make_lease_revocation,
    new_canary,
)
from secdogie_inference.mutiny import DECIDER_SELF_REPORTED, MAX_HOLDERS, decider_target  # noqa: E402
from secdogie_inference.node import _Pending  # noqa: E402
from secdogie_inference.protocol import MAX_SKEW, TrustRoots, digest  # noqa: E402
from secdogie_inference.session import (  # noqa: E402
    CLOSED,
    DECIDING,
    DISPATCHED,
    SETTLING,
    BoundedCache,
    IllegalSessionEdge,
    Session,
    SessionTable,
)

NOW = 1_000_000.0


class Held(Session):
    released = False

    def release(self):
        self.released = True


def table(**kw):
    kw = {"capacity": 3, "per_owner": 2, "retain_count": 4, "retain_bytes": 1 << 20, **kw}
    return SessionTable(**kw)


# -- the table ---------------------------------------------------------------------------

def test_admission_is_capped_per_table_and_per_owner():
    t = table()
    assert t.admit(Held("a", "u1", NOW, NOW + 10)) and t.admit(Held("b", "u1", NOW, NOW + 10))
    assert not t.admit(Held("c", "u1", NOW, NOW + 10))  # u1 holds its share
    assert t.admit(Held("c", "u2", NOW, NOW + 10))
    assert not t.admit(Held("d", "u3", NOW, NOW + 10))  # the table is full
    assert not t.admit(Held("a", "u3", NOW, NOW + 10))  # a key is live once
    assert (len(t), t.owned("u1")) == (3, 2)


def test_edges_are_enforced_and_closed_is_terminal():
    t = table()
    s = Held("a", "u", NOW, NOW + 10)
    t.admit(s)
    with pytest.raises(IllegalSessionEdge):
        t.advance(s, SETTLING)  # open -> settling skips the decision
    for state in (DECIDING, DISPATCHED, SETTLING, DECIDING, DISPATCHED, SETTLING):
        t.advance(s, state)     # one retry: settling -> deciding
    t.close(s, "accepted", ("result", {"x": 1}), now=NOW)
    assert (s.state, s.outcome, s.released) == (CLOSED, "accepted", True)
    assert t.live("a") is None and t.final("a") == ("result", {"x": 1}) and t.owned("u") == 0
    with pytest.raises(IllegalSessionEdge):
        t.advance(s, DECIDING)
    with pytest.raises(IllegalSessionEdge):
        t.close(s, "refused")
    with pytest.raises(ValueError):
        t.close(Held("b", "u", NOW, NOW), "fine")


def test_expiry_lists_live_sessions_past_their_time():
    t = table(per_owner=3)
    for key, exp in (("a", NOW + 1), ("b", NOW + 5), ("c", NOW + 2)):
        t.admit(Held(key, "u", NOW, exp))
    assert [s.key for s in t.expired(NOW + 3)] == ["a", "c"]


def test_finals_are_bounded_by_count_bytes_and_age():
    t = table(retain_count=3, retain_bytes=200, retain_for=10.0)
    for i in range(5):
        t.remember(f"k{i}", {"i": i}, now=NOW + i)
    assert [t.final(f"k{i}") for i in range(5)] == [None, None, {"i": 2}, {"i": 3}, {"i": 4}]
    t.remember("big", {"blob": "x" * 180}, now=NOW + 5)
    assert t.stats()["retained_bytes"] <= 200 and t.final("big") is not None
    t.prune(NOW + 30)
    assert t.stats()["retained"] == 0
    t.remember("k", 1, now=NOW)
    assert t.forget("k") == 1 and t.final("k") is None


def test_bounded_cache_evicts_oldest_by_count_and_bytes():
    c = BoundedCache(max_items=3, max_bytes=10, size=len)
    for k, v in (("a", "xx"), ("b", "xx"), ("c", "xx"), ("d", "xx")):
        c[k] = v
    assert c.keys() == ["b", "c", "d"] and c.bytes == 6
    c["e"] = "xxxxxxx"  # 7 bytes: 13 is over budget, so the oldest go until it fits
    assert c.keys() == ["d", "e"] and c.bytes == 9
    c["e"] = "x"        # replacing an entry re-costs it
    assert c.bytes == 3 and len(c) == 2


# -- a serving node's requests --------------------------------------------------------

def submit(arc, text, to="S2"):
    request = make_request(arc.ids["U"], [sim.ALPHA], text, clock=arc.net.clock)
    arc.U.send(arc.did[to], "submit", request=request)
    return request


def test_one_user_cannot_fill_a_serving_node_and_busy_keeps_nothing():
    arc = sim.Arc(entries=("S2",), config=replace(sim.FAST, max_sessions_per_user=1))
    seen = []
    arc.net.drop[arc.did["U"]] = lambda src, f: seen.append(f) or True
    first, second = submit(arc, "one"), submit(arc, "two")
    arc.net.run()
    busy = [f for f in seen if f["k"] == "refused"]
    assert [f["request_hash"] for f in busy] == [digest(second)]
    assert busy[0]["reason"].startswith("busy: 1 of your requests")
    arc.net.until(lambda: any(f["k"] == "result" for f in seen))
    assert arc.S2.sessions.final(digest(second)) is None  # a busy refusal is not remembered
    arc.U.send(arc.did["S2"], "submit", request=second)    # so the same request may come back
    arc.net.until(lambda: sum(f["k"] == "result" for f in seen) == 2)
    assert len(arc.S2.sessions) == 0 and arc.S2.sessions.final(digest(first))[0] == "result"


def test_a_session_past_its_time_is_closed_and_its_user_told():
    arc = sim.Arc(entries=("S2",))
    seen = []
    arc.net.drop[arc.did["U"]] = lambda src, f: seen.append(f) or True
    now = arc.net.clock()
    request = make_request(arc.ids["U"], [sim.ALPHA], "stuck", clock=arc.net.clock)
    p = _Pending(digest(request), arc.did["U"], now, now + 2, request=request)
    assert arc.S2.sessions.admit(p)
    arc.S2.sessions.advance(p, DECIDING)
    arc.S2.sessions.advance(p, DISPATCHED)
    p.deadline = now + 1000  # its legs would wait forever
    arc.net.settle(3)
    assert p.state == CLOSED and p.outcome == "expired" and p.request is None
    assert [f["reason"] for f in seen if f["k"] == "refused"] == ["expired: the request ran out of time here"]


def test_an_answer_for_a_closed_session_changes_nothing():
    arc = sim.Arc(entries=("S2",))
    out = arc.ask([sim.ALPHA])
    assert out.status == OUTCOME_ACCEPTED
    (rhash,) = arc.S2.sessions._final.keys()
    kind, fields = arc.S2.sessions.final(rhash)
    decision, response = fields["items"][0]
    before = arc.S2.sessions.stats()
    arc.S2.collect(decision, 0, response)              # late, duplicated
    arc.S2.leg_failed(decision, 0, arc.did["S1"], "x")
    assert arc.S2.sessions.stats() == before and arc.S2.sessions.live(rhash) is None


# -- the user's asks -------------------------------------------------------------------

def test_a_user_takes_outcomes_and_unclaimed_ones_age_out():
    arc = sim.Arc(entries=("S2",))
    arc.U.asks.retain_for = 5.0
    h1 = arc.U.ask([sim.ALPHA], "one")
    arc.net.until(lambda: arc.U.outcome(h1) is not None)
    assert arc.U.take(h1).status == OUTCOME_ACCEPTED and arc.U.outcome(h1) is None
    h2 = arc.U.ask([sim.ALPHA], "two")
    arc.net.until(lambda: arc.U.outcome(h2) is not None)
    arc.net.settle(6)
    assert arc.U.outcome(h2) is None and len(arc.U.asks) == 0  # nobody took it: gone
    h3 = arc.U.ask([sim.ALPHA], "three")
    arc.U.forget(h3)                                           # cancelled while open
    assert len(arc.U.asks) == 0 and arc.U.outcome(h3) is None


def test_a_user_holds_a_bounded_number_of_open_asks():
    arc = sim.Arc(entries=("S2",))
    arc.U.asks.capacity = 2
    handles = [arc.U.ask([sim.ALPHA], f"q{i}") for i in range(3)]
    over = arc.U.outcome(handles[2])
    assert over.status == OUTCOME_REFUSED and over.reasons[0].startswith("busy:")
    arc.net.until(lambda: all(arc.U.outcome(h) is not None for h in handles[:2]))
    assert len(arc.U.asks) == 0


# -- leases, faults, the ledger ----------------------------------------------------------

@pytest.fixture
def lease_cast():
    broker, node = Identity.generate(), Identity.generate()
    trust = TrustRoots(mesh=Allowlist({node.did, broker.did}), users=Allowlist(), deciders=Allowlist(),
                       coordinators=Allowlist(), brokers=Allowlist({broker.did}), operators=Allowlist())
    return broker, node, trust


def test_a_revoked_lease_id_is_forgotten_once_the_lease_is_dead(lease_cast):
    broker, node, trust = lease_cast
    book = LeaseBook(trust)
    lease = make_lease(broker, node.did, "m/a", key_version=1, canary=new_canary(), ttl=60, clock=lambda: NOW)
    book.add(lease)
    unknown = "ab" * 16
    book.revoke(make_lease_revocation(broker, sorted([lease["lease_id"], unknown]), clock=lambda: NOW))
    assert book.is_revoked(lease["lease_id"]) and book.is_revoked(unknown)
    book.prune(NOW + 60 + MAX_SKEW)                      # the known lease is dead: forget its id
    assert not book.is_revoked(lease["lease_id"]) and book.is_revoked(unknown)
    book.prune(NOW + MAX_LEASE_TTL + MAX_SKEW)           # any lease the unknown id named is dead too
    assert book.revoked_count() == 0
    assert not book.add(lease) or lease not in [x for x in book.live(NOW + MAX_LEASE_TTL)]  # dead either way


def test_leases_cannot_outlive_max_lease_ttl(lease_cast):
    broker, node, _trust = lease_cast
    with pytest.raises(ValueError, match="MAX_LEASE_TTL"):
        make_lease(broker, node.did, "m/a", key_version=1, canary=new_canary(), ttl=MAX_LEASE_TTL + 1,
                   clock=lambda: NOW)
    ok = make_lease(broker, node.did, "m/a", key_version=1, canary=new_canary(), ttl=60, clock=lambda: NOW)
    assert lease_shape(ok) is None


def test_a_lease_secret_is_wiped_and_never_printed():
    held = LeaseSecret({"lease_id": "ab" * 16, "model": "m/a"}, "sk-very-secret", "sdgc_" + "0" * 32)
    assert held.secret == "sk-very-secret" and "sk-" not in repr(held)
    buf = held._secret
    held.wipe()
    assert held.wiped and not buf
    with pytest.raises(ValueError):
        _ = held.secret
    with pytest.raises(ValueError):
        _ = held.canary


def test_the_fault_tracker_forgets_what_it_no_longer_needs():
    ft = FaultTracker(threshold=2, window=10, cooldown=5, probation=5)
    ft.report("x", "a", NOW)
    ft.prune(NOW + 11)
    assert len(ft) == 0  # one stale accusation
    ft.report("y", "a", NOW)
    assert ft.report("y", "b", NOW + 1)  # quarantined
    ft.prune(NOW + 20)
    assert len(ft) == 1  # strikes are remembered for a while
    ft.prune(NOW + 1 + 5 + 5 + 10 * 5 + 1)
    assert len(ft) == 0


def self_report(arc, n):
    ev = {"reporter": arc.did["S2"], "proposal": {"n": n}, "violation": "quorum-downgrade"}
    return make_incident(arc.ids["S2"], DECIDER_SELF_REPORTED, ev, clock=arc.net.clock)


def test_the_ledger_keeps_a_bounded_number_of_incidents_per_target_and_fails_closed():
    arc = sim.Arc()
    ledger = arc.C.ledger
    target = decider_target(arc.did["S2"])
    incidents = [self_report(arc, n) for n in range(MAX_HOLDERS + 3)]
    added = [ledger.add(x) is not None for x in incidents]
    assert added == [True] * MAX_HOLDERS + [False] * 3  # the target is held; more proves nothing new
    assert ledger.sizes()["incidents"] == MAX_HOLDERS
    ids = sorted(ledger.latched()[target])
    new, unlatched = ledger.reset(make_reset(arc.ids["O"], [target], ids, clock=arc.net.clock))
    assert new and unlatched == {target} and not ledger.lockdown
    assert ledger.sizes()["incidents"] == 0 and ledger.sizes()["tombstones"] == MAX_HOLDERS
    assert ledger.add(incidents[0]) is None                # a cleared one never re-applies
    assert ledger.add(incidents[-1]) is not None           # one not kept: it latches again
    assert ledger.is_latched(target)


# -- the soak: every table under its cap after a long, rough run ---------------------------

def test_memory_stays_bounded_through_twenty_minutes_of_churn():
    # Slower heartbeats than FAST keep the run short; short leases make the broker renew,
    # and the books expire and forget, many times over.
    cfg = replace(sim.FAST, heartbeat_every=4.0, fail_after=9.0, advise_every=4.0, lease_ttl=120.0,
                  max_sessions=6, max_sessions_per_user=4, retain_results=16, retain_bytes=64 << 10,
                  answer_bytes=64 << 10)
    arc = sim.Arc(config=cfg, fanout=2)
    d = arc.did
    arc.U.asks.capacity, arc.U.asks.retain_for = 8, 120.0
    for minute in range(20):
        for i in range(6):
            arc.U.ask([sim.ALPHA, sim.BETA], f"m{minute}-q{i}", quorum=1 + i % 2)
        if minute == 3:
            arc.net.down.add(d["S1"])
        if minute == 5:
            arc.net.down.discard(d["S1"])
            arc.net.drop[d["S4"]] = lambda src, f: f["k"] == "req"
        if minute == 7:
            del arc.net.drop[d["S4"]]
        if minute == 10:
            arc.S3.publish(make_incident(arc.ids["S3"], DECIDER_SELF_REPORTED, {
                "reporter": d["S3"], "proposal": {}, "violation": "quorum-downgrade"}, clock=arc.net.clock))
        if minute == 12:
            ids = sorted(arc.C.ledger.latched()[decider_target(d["S3"])])
            arc.C.publish(make_reset(arc.ids["O"], [decider_target(d["S3"])], ids, clock=arc.net.clock))
        arc.net.settle(60, dt=2.0)

    mesh = len(arc.everyone())
    for part in arc.everyone():
        assert len(part._seen._keys) <= 8192
        assert len(part.leases) <= 16 and part.leases.revoked_count() <= 64
        assert part.ledger.sizes()["latched"] == 0 and part.ledger.sizes()["incidents"] == 0
        if hasattr(part, "sessions"):
            st = part.sessions.stats()
            assert st["live"] <= cfg.max_sessions and st["retained"] <= 16 and st["retained_bytes"] <= 64 << 10
            assert len(part._served) <= 4096 and part._served.bytes <= 64 << 10
            assert part.suspicion.size() <= 2 * mesh and len(part.safety._seen) <= 4096
            assert all(s.lease["expires_at"] > arc.net.clock() for s in part._held.values())
    assert len(arc.C.faults) <= mesh
    assert len(arc.U.asks) == 0 and arc.U.asks.stats()["retained"] <= 6 * 2 + 8
    assert len(arc.U._by_rhash) == 0 and len(arc.U._late) <= 256
    # and the mesh still works
    assert arc.ask([sim.ALPHA, sim.BETA], "after", quorum=2).status == OUTCOME_ACCEPTED
