"""The mode state machine: its table, the one rule that matters (only a reset
leaves lockdown), and the machine running inside every participant."""
from __future__ import annotations

import itertools

import pytest

pytest.importorskip("nacl")

import sim  # noqa: E402
from secdogie_inference import (  # noqa: E402
    MODE_DEGRADED,
    MODE_LOCKDOWN,
    MODE_NORMAL,
    make_incident,
    make_reset,
)
from secdogie_inference.mutiny import DECIDER_SELF_REPORTED, decider_target  # noqa: E402
from secdogie_inference.protocol import MODES  # noqa: E402
from secdogie_inference.state import (  # noqa: E402
    ALLOWED,
    CAUSE_ADVISORY,
    CAUSE_LATCH,
    CAUSE_LIVENESS,
    CAUSE_RESET,
    CAUSE_START,
    CAUSES,
    IllegalTransition,
    ModeInputs,
    ModeMachine,
    allowed,
)

LOCKED = ModeInputs(lockdown_latches=frozenset({"decider:x"}))
DEGRADED = ModeInputs(stale=frozenset({"did:key:z6Mk..."}))
NORMAL = ModeInputs()
INPUTS = {MODE_NORMAL: NORMAL, MODE_DEGRADED: DEGRADED, MODE_LOCKDOWN: LOCKED}


def machine_at(mode):
    m = ModeMachine()
    m.step(INPUTS[mode], causes=(), now=0.0)
    return m


def test_derive_orders_lockdown_over_degraded_over_normal():
    assert ModeMachine.derive(NORMAL) == MODE_NORMAL
    assert ModeMachine.derive(DEGRADED) == MODE_DEGRADED
    assert ModeMachine.derive(ModeInputs(lockdown_latches=frozenset({"x"}), failed=frozenset({"y"}))) == MODE_LOCKDOWN
    for field in ("isolated", "failed", "quarantined", "stale"):
        assert ModeMachine.derive(ModeInputs(**{field: frozenset({"d"})})) == MODE_DEGRADED


@pytest.mark.parametrize("old,new,cause", list(itertools.product(sorted(MODES), sorted(MODES), sorted(CAUSES))))
def test_every_move_is_taken_exactly_when_the_table_allows_it(old, new, cause):
    if old == new:
        return
    m = machine_at(old)
    if allowed(old, new, cause):
        t = m.step(INPUTS[new], causes={cause}, now=1.0)
        assert (t.old, t.new, t.cause) == (old, new, cause) and m.state == new
    else:
        with pytest.raises(IllegalTransition):
            m.step(INPUTS[new], causes={cause}, now=1.0)
        assert m.state == old  # a refused move changes nothing


def test_only_a_reset_leaves_lockdown():
    exits = {(o, n): c for (o, n), c in ALLOWED.items() if o == MODE_LOCKDOWN}
    assert exits and all(c == frozenset({CAUSE_RESET}) for c in exits.values())
    m = machine_at(MODE_LOCKDOWN)
    with pytest.raises(IllegalTransition, match="reset"):
        m.step(NORMAL, causes=set(CAUSES) - {CAUSE_RESET}, now=1.0)
    t = m.step(NORMAL, causes={CAUSE_RESET, CAUSE_LIVENESS}, now=2.0)
    assert (t.old, t.new, t.cause) == (MODE_LOCKDOWN, MODE_NORMAL, CAUSE_RESET)


def test_the_first_step_is_a_start_and_no_change_is_no_transition():
    m = ModeMachine()
    t = m.step(DEGRADED, causes=(), now=0.0)
    assert (t.old, t.new, t.cause) == (None, MODE_DEGRADED, CAUSE_START)
    assert m.step(DEGRADED, causes={CAUSE_ADVISORY}, now=1.0) is None


def test_the_most_specific_cause_is_recorded():
    m = machine_at(MODE_NORMAL)
    t = m.step(DEGRADED, causes={CAUSE_LIVENESS, CAUSE_ADVISORY}, now=1.0)
    assert t.cause == CAUSE_ADVISORY


def test_history_is_bounded_and_subscribers_come_and_go():
    m = ModeMachine(history=3)
    seen = []
    unsubscribe = m.subscribe(seen.append)
    m.subscribe(lambda t: 1 / 0)  # a broken observer does not stop the machine
    m.step(NORMAL, causes=(), now=0.0)
    for i in range(5):
        m.step(DEGRADED, causes={CAUSE_LIVENESS}, now=2.0 * i + 1)
        m.step(NORMAL, causes={CAUSE_LIVENESS}, now=2.0 * i + 2)
    assert len(m.history) == 3 and m.history[-1].at == 10.0
    assert len(seen) == 11
    unsubscribe()
    m.step(LOCKED, causes={CAUSE_LATCH}, now=11.0)
    assert len(seen) == 11 and m.state == MODE_LOCKDOWN


# -- inside the participants ------------------------------------------------------------

def causes_of(part):
    return [(t.old, t.new, t.cause) for t in part.machine.history]


def test_the_three_drawings_as_transitions():
    arc = sim.Arc(entries=("S2",))
    d = arc.did
    assert {p.machine.state for p in arc.everyone()} == {MODE_NORMAL}

    arc.net.down.add(d["S1"])            # drawing 2
    arc.net.settle(8)
    arc.net.down.discard(d["S1"])
    assert arc.S2.machine.state == MODE_DEGRADED
    assert causes_of(arc.S2)[-1][:2] == (MODE_NORMAL, MODE_DEGRADED)

    ev = {"reporter": d["S2"], "proposal": {"legs": []}, "violation": "quorum-downgrade"}
    arc.S2.publish(make_incident(arc.ids["S2"], DECIDER_SELF_REPORTED, ev, clock=arc.net.clock))
    arc.net.run()                        # drawing 3
    assert {p.machine.state for p in arc.everyone()} == {MODE_LOCKDOWN}
    assert all(causes_of(p)[-1][1:] == (MODE_LOCKDOWN, CAUSE_LATCH) for p in arc.everyone())

    ids = sorted(arc.C.ledger.latched()[decider_target(d["S2"])])
    arc.C.publish(make_reset(arc.ids["O"], [decider_target(d["S2"])], ids, clock=arc.net.clock))
    arc.net.run()
    assert all(causes_of(p)[-1][0] == MODE_LOCKDOWN and causes_of(p)[-1][2] == CAUSE_RESET
               for p in arc.everyone())
    arc.net.settle(10)                   # S1 is back; the next advisories clear it
    assert {p.mode() for p in arc.everyone()} == {MODE_NORMAL}


def test_a_latch_cleared_behind_the_ledgers_back_fails_loudly():
    arc = sim.Arc(entries=("S2",))
    d = arc.did
    ev = {"reporter": d["S2"], "proposal": {"legs": []}, "violation": "quorum-downgrade"}
    arc.S2.publish(make_incident(arc.ids["S2"], DECIDER_SELF_REPORTED, ev, clock=arc.net.clock))
    arc.net.run()
    arc.S3.ledger._latches.clear()  # a bug: latches cleared without an operator's reset
    arc.S3.ledger._derived = None
    with pytest.raises(IllegalTransition):
        arc.S3.tick()


def test_health_snapshot_reads_every_per_node_machine():
    arc = sim.Arc(entries=("S3", "S2"))
    d = arc.did
    arc.net.drop[d["S4"]] = lambda src, f: f["k"] == "req"  # the black hole of drawing 2
    for entry in ("S3", "S2"):
        arc.U.entries = [d[entry]]
        arc.ask([sim.BETA])
    now = arc.net.clock()
    assert arc.C.health_snapshot(now)[d["S4"]].quarantine == "quarantined"
    arc.net.settle(3)
    s3 = arc.S3.health_snapshot()
    assert s3[d["S4"]].alive and s3[d["S4"]].quarantine == "quarantined" and not s3[d["S4"]].latched
    assert s3[d["R"]].breaker in ("closed", "open", "half-open") and s3[d["S2"]].breaker == ""
    arc.net.down.add(d["S1"])
    arc.net.settle(5)
    assert not arc.S3.health_snapshot()[d["S1"]].alive
