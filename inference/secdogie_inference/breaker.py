"""A circuit breaker per model node and per model, for ordinary failures.

Keys are target strings (``node:<did>``, ``model:<name>``). ``threshold``
consecutive failures open the breaker; after a cooldown it is half-open and
traffic may probe it again; a success closes it, a failure re-opens it with the
cooldown doubled (capped). This is the auto-recovering half of circuit
breaking -- timeouts, refusals, invalid or divergent answers.

Mutiny is not handled here. A breaker that a node or model could talk its way
out of by answering well for a while is the wrong tool for proven adversarial
behaviour; that latches in ``mutiny.MutinyLedger`` and only an operator's
signed reset clears it.

Half-open admits concurrent probes rather than exactly one: a decider routes
whole requests, and a second probe costs one more request, not an outage.
"""
from __future__ import annotations

from dataclasses import dataclass

CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half-open"


@dataclass
class _State:
    failures: int = 0
    trips: int = 0
    until: float = 0.0   # open until; 0 when closed
    open: bool = False


class CircuitBreaker:
    def __init__(self, *, threshold: int = 3, cooldown: float = 30.0, max_cooldown: float = 600.0):
        if threshold < 1:
            raise ValueError("threshold must be at least 1")
        self.threshold = int(threshold)
        self.cooldown = float(cooldown)
        self.max_cooldown = float(max_cooldown)
        self._states: dict[str, _State] = {}

    def state(self, key: str, now: float) -> str:
        st = self._states.get(key)
        if st is None or not st.open:
            return CLOSED
        return OPEN if now < st.until else HALF_OPEN

    def available(self, key: str, now: float) -> bool:
        return self.state(key, now) != OPEN

    def success(self, key: str) -> None:
        self._states.pop(key, None)

    def failure(self, key: str, now: float) -> bool:
        """Count a failure; True when it opens (or re-opens) the breaker."""
        st = self._states.setdefault(key, _State())
        if st.open:
            if now < st.until:
                return False  # already open; nothing new to learn
            st.trips += 1     # a failed probe while half-open
            st.until = now + min(self.cooldown * 2 ** st.trips, self.max_cooldown)
            return True
        st.failures += 1
        if st.failures < self.threshold:
            return False
        st.open, st.failures = True, 0
        st.until = now + self.cooldown
        return True


__all__ = ["CircuitBreaker", "CLOSED", "OPEN", "HALF_OPEN"]
