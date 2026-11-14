"""The operating mode as an explicit state machine -- the three drawings.

Every participant is in one of three modes, computed from what it holds:

  * ``lockdown``  -- drawing 3: a verified mutiny incident holds a latch;
  * ``degraded``  -- drawing 2: an advisory lists failed or quarantined nodes,
    a node this participant knows has stopped heartbeating, or a node is
    isolated by proof;
  * ``normal``    -- drawing 1: none of that.

``ModeMachine.derive`` is that rule, pure. ``ModeMachine.step`` moves the
machine and checks each move against ``ALLOWED``: a move must have a cause
that may make it. The one rule that matters most is in the table rather than
in a comment -- **only an operator's reset leaves lockdown**. A move out of
lockdown without a verified reset in the step's causes raises
``IllegalTransition``: latches are cleared nowhere else, so that would be a
bug, and it fails loudly instead of quietly reopening the decision layer.

Causes are what the participant just absorbed (an advisory, an incident, a
reset), plus the passage of time (``liveness``: heartbeats keep arriving or
stop), which is always a possible cause. Each move is recorded as a
``Transition`` in a bounded history and handed to subscribers.

``NodeHealth`` is the read-only view of the per-node machines that already
exist -- liveness, local suspicion, the coordinator's quarantine, the
breaker, latches -- for operators and tests.
"""
from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .protocol import MODE_DEGRADED, MODE_LOCKDOWN, MODE_NORMAL, MODES

log = logging.getLogger("secdogie_inference")

CAUSE_START = "start"          # the first evaluation
CAUSE_LIVENESS = "liveness"    # heartbeats stopped or resumed (time passing)
CAUSE_ADVISORY = "advisory"    # a coordinator's advisory listed or cleared failures
CAUSE_ISOLATION = "isolation"  # a node was isolated by proof (not mutiny)
CAUSE_LATCH = "latch"          # a verified mutiny incident latched a target
CAUSE_RESET = "reset"          # a verified operator reset cleared latches
CAUSES = frozenset({CAUSE_START, CAUSE_LIVENESS, CAUSE_ADVISORY, CAUSE_ISOLATION, CAUSE_LATCH, CAUSE_RESET})

# Most specific first: the cause a transition is recorded under when several apply.
_PRIORITY = (CAUSE_RESET, CAUSE_LATCH, CAUSE_ISOLATION, CAUSE_ADVISORY, CAUSE_LIVENESS, CAUSE_START)

ALLOWED: dict[tuple[str | None, str], frozenset[str]] = {
    **{(None, m): frozenset({CAUSE_START}) for m in MODES},
    (MODE_NORMAL, MODE_DEGRADED): frozenset({CAUSE_LIVENESS, CAUSE_ADVISORY, CAUSE_ISOLATION}),
    (MODE_DEGRADED, MODE_NORMAL): frozenset({CAUSE_LIVENESS, CAUSE_ADVISORY, CAUSE_RESET}),
    (MODE_NORMAL, MODE_LOCKDOWN): frozenset({CAUSE_LATCH}),
    (MODE_DEGRADED, MODE_LOCKDOWN): frozenset({CAUSE_LATCH}),
    # Only an operator's reset leaves lockdown.
    (MODE_LOCKDOWN, MODE_NORMAL): frozenset({CAUSE_RESET}),
    (MODE_LOCKDOWN, MODE_DEGRADED): frozenset({CAUSE_RESET}),
}


class IllegalTransition(RuntimeError):
    """A mode change no cause at hand may make."""


@dataclass(frozen=True)
class ModeInputs:
    """Everything the mode depends on, captured when it was evaluated."""

    lockdown_latches: frozenset[str] = frozenset()  # targets latched by mutiny evidence
    isolated: frozenset[str] = frozenset()          # nodes isolated by proof, without lockdown
    failed: frozenset[str] = frozenset()            # listed failed by a coordinator's advisory
    quarantined: frozenset[str] = frozenset()       # listed quarantined by a coordinator's advisory
    stale: frozenset[str] = frozenset()             # known nodes whose heartbeats stopped, by our clock


@dataclass(frozen=True)
class Transition:
    at: float
    old: str | None
    new: str
    cause: str
    inputs: ModeInputs


def allowed(old: str | None, new: str, cause: str) -> bool:
    return cause in ALLOWED.get((old, new), frozenset())


class ModeMachine:
    def __init__(self, *, history: int = 256):
        self.state: str | None = None
        self.history: deque[Transition] = deque(maxlen=history)
        self._subscribers: list[Callable[[Transition], None]] = []

    @staticmethod
    def derive(inputs: ModeInputs) -> str:
        if inputs.lockdown_latches:
            return MODE_LOCKDOWN
        if inputs.isolated or inputs.failed or inputs.quarantined or inputs.stale:
            return MODE_DEGRADED
        return MODE_NORMAL

    def step(self, inputs: ModeInputs, *, causes: Iterable[str], now: float) -> Transition | None:
        """Move to the mode ``inputs`` imply. Returns the transition, or None
        when the mode is unchanged; raises ``IllegalTransition`` when none of
        ``causes`` may make the move."""
        new = self.derive(inputs)
        old = self.state
        if new == old:
            return None
        have = set(causes) | ({CAUSE_START} if old is None else set())
        cause = next((c for c in _PRIORITY if c in have and allowed(old, new, c)), None)
        if cause is None:
            raise IllegalTransition(f"{old} -> {new} needs one of {sorted(ALLOWED.get((old, new), ()))}, "
                                    f"have {sorted(have)}")
        t = Transition(float(now), old, new, cause, inputs)
        self.state = new
        self.history.append(t)
        for fn in list(self._subscribers):
            try:
                fn(t)
            except Exception:  # noqa: BLE001 - an observer must not stop the machine
                log.exception("mode subscriber failed")
        return t

    def subscribe(self, fn: Callable[[Transition], None]) -> Callable[[], None]:
        """Call ``fn`` on every transition; returns the unsubscribe function."""
        self._subscribers.append(fn)

        def unsubscribe() -> None:
            if fn in self._subscribers:
                self._subscribers.remove(fn)

        return unsubscribe


@dataclass(frozen=True)
class NodeHealth:
    """One participant's view of one node, across the per-node machines."""

    did: str
    roles: frozenset
    alive: bool         # new heartbeats keep arriving (LinkStateDB)
    suspect: bool       # locally excluded after timed-out legs (Suspicion)
    quarantine: str     # healthy | quarantined | probation (advisories; the coordinator's FaultTracker)
    breaker: str        # closed | open | half-open for a model node; "" where not tracked
    latched: bool       # off every path by mutiny evidence (MutinyLedger)
    isolated: bool      # off every path by proof, without lockdown


__all__ = [
    "ALLOWED",
    "CAUSES",
    "CAUSE_ADVISORY",
    "CAUSE_ISOLATION",
    "CAUSE_LATCH",
    "CAUSE_LIVENESS",
    "CAUSE_RESET",
    "CAUSE_START",
    "IllegalTransition",
    "ModeInputs",
    "ModeMachine",
    "NodeHealth",
    "Transition",
    "allowed",
]
