"""Tie each live-loop step to what episodic memory needs: the action's effect
hash, the gate's findings, and how the step came out.

The agent loop calls the plan gate right before it acts, then writes one trace
entry for that step (executed, refused or skipped). The gate sees the action as
a structured view; the trace entry carries a differently shaped action dict. So
the effect hash is taken where the view is -- at the gate -- and handed to the
very next trace entry, provided the action kinds agree. A step that never went
through the gate (done, look, ask_user, ...) gets no key, so it can never be
mistaken for evidence about an action.

Single-threaded like the loop itself; pure otherwise.
"""
from __future__ import annotations

from .action_gate import GateDecision, PlannedAction
from .authz import action_hash
from .loop_gate import to_planned


class StepCorrelator:
    """Observer for ``make_plan_gate`` plus the matching lookup for the trace."""

    def __init__(self):
        self._pending: tuple[str, str, tuple[str, ...]] | None = None  # (kind, key, findings)

    def observe(self, planned: PlannedAction, decision: GateDecision) -> None:
        self._pending = (planned.kind, action_hash(planned), tuple(decision.findings))

    def take(self, action) -> tuple[str, tuple[str, ...]]:
        """``(action_key, findings)`` for the trace entry of ``action`` (the
        loop's action dict), or ``("", ())`` if the gate did not judge this
        step. Always clears what it held: a key is used at most once."""
        pending, self._pending = self._pending, None
        if pending is None or not isinstance(action, dict):
            return "", ()
        kind = to_planned({"kind": action.get("kind")}).kind
        if kind != pending[0]:
            return "", ()
        return pending[1], pending[2]


__all__ = ["StepCorrelator"]
