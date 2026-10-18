"""Node health: healthy -> suspect (degrading) -> dead, and best-first picking."""

from __future__ import annotations

from secdogie_swarm import DEAD, HEALTHY, SUSPECT, HealthPolicy, NodeRegistry

POLICY = HealthPolicy(
    suspect_after_s=15.0,
    dead_after_s=45.0,
    latency_suspect_s=4.0,
    error_rate_suspect=0.4,
    dead_consecutive_failures=3,
)


def test_a_fresh_heartbeat_is_healthy() -> None:
    r = NodeRegistry(POLICY)
    r.heartbeat("a", now=0.0, latency_s=0.2)
    assert r.status("a") == HEALTHY


def test_idle_slides_to_suspect_then_dead() -> None:
    r = NodeRegistry(POLICY)
    r.heartbeat("a", now=0.0, latency_s=0.2)
    r.sweep(now=20.0)
    assert r.status("a") == SUSPECT  # quiet past suspect_after
    r.sweep(now=60.0)
    assert r.status("a") == DEAD  # quiet past dead_after


def test_high_latency_is_suspect() -> None:
    r = NodeRegistry(POLICY)
    for _ in range(5):
        r.heartbeat("a", now=1.0, latency_s=10.0)
    assert r.status("a") == SUSPECT


def test_consecutive_failures_kill_a_node() -> None:
    r = NodeRegistry(POLICY)
    r.heartbeat("a", now=0.0, latency_s=0.2)
    for i in range(3):
        r.record("a", ok=False, now=1.0 + i)
    assert r.status("a") == DEAD


def test_success_resets_the_failure_streak() -> None:
    r = NodeRegistry(POLICY)
    r.heartbeat("a", now=0.0, latency_s=0.2)
    r.record("a", ok=False, now=1.0)
    r.record("a", ok=False, now=2.0)
    r.record("a", ok=True, now=3.0, latency_s=0.2)
    assert r.get("a").consecutive_failures == 0
    assert r.status("a") == HEALTHY


def test_pick_prefers_lower_latency_and_excludes() -> None:
    r = NodeRegistry(POLICY)
    r.heartbeat("slow", now=0.0, latency_s=3.0)
    r.heartbeat("fast", now=0.0, latency_s=0.1)
    r.heartbeat("mid", now=0.0, latency_s=1.0)
    assert r.pick(2) == ["fast", "mid"]
    assert r.pick(3, exclude={"fast"}) == ["mid", "slow"]
