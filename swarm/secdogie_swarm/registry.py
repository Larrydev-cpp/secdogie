"""The node registry: who is online, how healthy, and -- the point of it --
*which nodes are degrading before they drop*.

Volunteer compute is unreliable: a node closes its tab, its network stalls, it
goes slow. The registry tracks each node's liveness (heartbeats), latency and
recent error rate, and classifies it healthy -> suspect -> dead. "suspect" is
the predictive signal: a node that is degrading but not yet gone, so the
scheduler can move its in-flight work to a fresh node *before* the deadline
rather than after. This is reliability engineering for flaky participants, not
anything adversarial.

All time is passed in (`now`, seconds); nothing reads the clock, so it tests
deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass

HEALTHY = "healthy"
SUSPECT = "suspect"
DEAD = "dead"


@dataclass(frozen=True)
class HealthPolicy:
    # EWMA smoothing for latency and error rate.
    alpha: float = 0.3
    # No heartbeat for this long -> suspect; longer -> dead.
    suspect_after_s: float = 15.0
    dead_after_s: float = 45.0
    # Latency (seconds) and error-rate (0..1) past which a node is suspect.
    latency_suspect_s: float = 4.0
    error_rate_suspect: float = 0.4
    # Consecutive failed/timed-out assignments that kill a node outright.
    dead_consecutive_failures: int = 3


@dataclass
class NodeHealth:
    did: str
    last_seen: float
    latency_ewma: float = 0.0
    error_ewma: float = 0.0
    consecutive_failures: int = 0
    status: str = HEALTHY

    def recompute(self, now: float, policy: HealthPolicy) -> None:
        idle = now - self.last_seen
        if idle > policy.dead_after_s or self.consecutive_failures >= policy.dead_consecutive_failures:
            self.status = DEAD
        elif (
            idle > policy.suspect_after_s
            or self.latency_ewma > policy.latency_suspect_s
            or self.error_ewma > policy.error_rate_suspect
        ):
            self.status = SUSPECT
        else:
            self.status = HEALTHY


class NodeRegistry:
    def __init__(self, policy: HealthPolicy | None = None) -> None:
        self.policy = policy or HealthPolicy()
        self._nodes: dict[str, NodeHealth] = {}

    def __contains__(self, did: str) -> bool:
        return did in self._nodes

    def get(self, did: str) -> NodeHealth | None:
        return self._nodes.get(did)

    def heartbeat(self, did: str, now: float, latency_s: float | None = None) -> NodeHealth:
        """A node checked in. Updates liveness and (if given) latency."""
        n = self._nodes.get(did)
        a = self.policy.alpha
        if n is None:
            n = NodeHealth(did=did, last_seen=now, latency_ewma=latency_s or 0.0)
            self._nodes[did] = n
        else:
            n.last_seen = now
            if latency_s is not None:
                n.latency_ewma = a * latency_s + (1 - a) * n.latency_ewma
        n.recompute(now, self.policy)
        return n

    def record(self, did: str, *, ok: bool, now: float, latency_s: float | None = None) -> None:
        """Outcome of an assignment: success resets the failure streak; a
        failure (error or timeout) raises error rate and the streak."""
        n = self._nodes.get(did)
        if n is None:
            return
        a = self.policy.alpha
        n.error_ewma = a * (0.0 if ok else 1.0) + (1 - a) * n.error_ewma
        if ok:
            n.consecutive_failures = 0
            n.last_seen = now
            if latency_s is not None:
                n.latency_ewma = a * latency_s + (1 - a) * n.latency_ewma
        else:
            n.consecutive_failures += 1
        n.recompute(now, self.policy)

    def sweep(self, now: float) -> None:
        for n in self._nodes.values():
            n.recompute(now, self.policy)

    def status(self, did: str) -> str:
        n = self._nodes.get(did)
        return n.status if n else DEAD

    def healthy(self, *, exclude: set[str] | None = None) -> list[str]:
        """Healthy nodes, best (lowest latency, most recently seen) first."""
        ex = exclude or set()
        picks = [n for n in self._nodes.values() if n.status == HEALTHY and n.did not in ex]
        picks.sort(key=lambda n: (n.latency_ewma, -n.last_seen))
        return [n.did for n in picks]

    def pick(self, count: int, *, exclude: set[str] | None = None) -> list[str]:
        return self.healthy(exclude=exclude)[:count]
