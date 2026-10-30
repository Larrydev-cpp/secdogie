"""Suspicion, quarantine with probation, and the circuit breaker."""
from __future__ import annotations

from secdogie_inference.breaker import CLOSED, HALF_OPEN, OPEN, CircuitBreaker
from secdogie_inference.health import HEALTHY, PROBATION, QUARANTINED, FaultTracker, Suspicion


def test_suspicion_threshold_decay_and_cooldown():
    s = Suspicion(threshold=2, cooldown=10)
    assert s.timed_out(["x", "y"], 0) == []
    s.delivered(["y"])                       # y also sat on a leg that delivered
    assert s.timed_out(["x", "y"], 1) == ["x"]
    assert s.suspects(5) == {"x"} and s.suspects(11) == set()


def test_one_reporter_cannot_quarantine():
    t = FaultTracker(threshold=2, window=60, cooldown=20, probation=10)
    assert not t.report("w", "s2", 0)
    assert not t.report("w", "s2", 1)            # the same reporter again
    assert not t.report("w", "w", 2)             # nobody accuses themselves
    assert t.report("w", "s3", 3)                # a second, distinct reporter
    assert t.status("w", 4) == QUARANTINED


def test_reports_outside_the_window_do_not_add_up():
    t = FaultTracker(threshold=2, window=10)
    t.report("w", "s2", 0)
    assert not t.report("w", "s3", 11)


def test_cooldown_doubles_and_probation_is_strict():
    t = FaultTracker(threshold=2, window=60, cooldown=20, probation=10)
    t.report("w", "a", 0)
    t.report("w", "b", 0)
    assert t.status("w", 19) == QUARANTINED and t.status("w", 21) == PROBATION
    assert t.report("w", "a", 22)                # one report on probation is enough
    assert t.status("w", 22 + 39) == QUARANTINED and t.status("w", 22 + 41) == PROBATION
    assert t.status("w", 22 + 40 + 11) == HEALTHY  # a clean probation reinstates


def test_breaker_opens_half_opens_and_backs_off():
    b = CircuitBreaker(threshold=2, cooldown=10, max_cooldown=100)
    assert not b.failure("node:x", 0)
    assert b.failure("node:x", 1) and b.state("node:x", 2) == OPEN and not b.available("node:x", 2)
    assert b.state("node:x", 12) == HALF_OPEN and b.available("node:x", 12)
    assert b.failure("node:x", 12) and b.state("node:x", 12 + 19) == OPEN  # doubled
    assert b.state("node:x", 12 + 21) == HALF_OPEN
    b.success("node:x")
    assert b.state("node:x", 40) == CLOSED
