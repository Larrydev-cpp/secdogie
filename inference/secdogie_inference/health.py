"""Failure detection beyond "it stopped heartbeating".

Crash failures are the easy case: a node that stops sending new heartbeats
ages out of every ``LinkStateDB`` and routes avoid it. The diagram's "(wrong)
P2P" is the harder one -- a node that heartbeats fine but mishandles traffic
(drops it, mangles it). Two pieces find those:

  * ``Suspicion`` runs at each decider. A node inside a leg that timed out gains
    a point; a node inside a leg that delivered loses one. A node that crosses
    the threshold is avoided locally for a cooldown and reported once to the
    coordinator. Intersecting many paths localises the bad node without anyone
    having to prove which hop dropped a frame.
  * ``FaultTracker`` runs at the coordinator. Reports are claims, not proof, so
    one reporter can never quarantine a node: it takes ``threshold`` distinct
    reporters inside ``window``. A quarantine lasts a cooldown that doubles each
    time; afterwards the node is on probation, where a single report sends it
    back. A clean probation reinstates it -- the crossed-out "wrong" in the
    diagram. Strikes are forgiven after a long clean run.

Neither latches anything: proven mutiny is ``mutiny.MutinyLedger``'s job, and
only an operator clears that.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field


class Suspicion:
    def __init__(self, *, threshold: int = 2, cooldown: float = 60.0):
        self.threshold = int(threshold)
        self.cooldown = float(cooldown)
        self._score: dict[str, int] = {}
        self._until: dict[str, float] = {}

    def timed_out(self, nodes: Iterable[str], now: float) -> list[str]:
        """Charge the nodes of a leg that timed out; returns the newly suspected."""
        newly = []
        for d in nodes:
            self._score[d] = self._score.get(d, 0) + 1
            if self._score[d] >= self.threshold and self._until.get(d, 0.0) <= now:
                self._until[d] = now + self.cooldown
                self._score[d] = 0
                newly.append(d)
        return newly

    def delivered(self, nodes: Iterable[str]) -> None:
        for d in nodes:
            if self._score.get(d, 0) > 0:
                self._score[d] -= 1

    def suspects(self, now: float) -> set[str]:
        return {d for d, until in self._until.items() if until > now}


QUARANTINED = "quarantined"
PROBATION = "probation"
HEALTHY = "healthy"


@dataclass
class _Record:
    reports: dict[str, float] = field(default_factory=dict)  # reporter -> when
    strikes: int = 0
    until: float = 0.0          # quarantine ends
    probation_until: float = 0.0


class FaultTracker:
    def __init__(self, *, threshold: int = 2, window: float = 60.0, cooldown: float = 60.0,
                 max_cooldown: float = 3600.0, probation: float = 30.0):
        if threshold < 1:
            raise ValueError("threshold must be at least 1")
        self.threshold = int(threshold)
        self.window = float(window)
        self.cooldown = float(cooldown)
        self.max_cooldown = float(max_cooldown)
        self.probation = float(probation)
        self._records: dict[str, _Record] = {}

    def report(self, accused: str, reporter: str, now: float) -> bool:
        """Count one report; True when it quarantines ``accused``."""
        if accused == reporter:
            return False
        rec = self._records.setdefault(accused, _Record())
        if now < rec.until:
            return False  # already quarantined
        if rec.strikes and now > rec.probation_until + 10 * self.cooldown:
            rec.strikes = 0  # a long clean run is forgiven
        rec.reports = {r: t for r, t in rec.reports.items() if now - t <= self.window}
        rec.reports[reporter] = now
        need = 1 if now < rec.probation_until else self.threshold
        if len(rec.reports) < need:
            return False
        rec.strikes += 1
        rec.until = now + min(self.cooldown * 2 ** (rec.strikes - 1), self.max_cooldown)
        rec.probation_until = rec.until + self.probation
        rec.reports.clear()
        return True

    def status(self, did: str, now: float) -> str:
        rec = self._records.get(did)
        if rec is None:
            return HEALTHY
        if now < rec.until:
            return QUARANTINED
        if now < rec.probation_until:
            return PROBATION
        return HEALTHY

    def quarantined(self, now: float) -> set[str]:
        return {d for d, rec in self._records.items() if now < rec.until}


__all__ = ["Suspicion", "FaultTracker", "QUARANTINED", "PROBATION", "HEALTHY"]
