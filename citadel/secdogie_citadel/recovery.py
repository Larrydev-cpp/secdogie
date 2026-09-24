"""Crash recovery for supervised runs (Phase 2.8).

2.7 records each run and step as signed state, carrying a per-step ``state``
(``executing``/``verifying``/...) and a chained ``state_hash``. When a node
restarts after a crash, some runs are left mid-flight (a non-terminal state).
This layer, from the materialized ``StateStore``, finds those runs and decides
how to resume each *safely*.

The one non-obvious rule -- and the reason this exists -- is the ``executing``
case. A crash while an action was being executed must NOT blindly retry: the
click/keystroke/submit may already have taken effect, and repeating it would act
twice. So an ``executing`` crash yields ``REOBSERVE_BEFORE_RETRY``, carrying the
last step's observation/action ids, so the agent re-observes and checks whether
the action already happened before deciding to retry. Everything before a side
effect (planning/observing/proposing/awaiting_*) is safe to redo.

Pure and headless: the recovery *decision* is a function of the materialized
state. Enforcing it (actually re-observing) rides the agent seam, exactly like
2.7's run recording. No new primitives; recovery is recorded as ordinary signed
state, so it converges over the mesh like everything else.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import run as runmod

# Recovery actions.
COMPLETE = "complete"                          # run already terminal; nothing to do
RESUME_STEP = "resume_step"                    # crashed before any side effect; safe to redo the step
REOBSERVE_BEFORE_RETRY = "reobserve_before_retry"  # crashed mid-execute; verify the action took effect first
REVERIFY = "reverify"                          # crashed during verification; re-run the check only

# States reached before an action's side effect -- safe to resume/redo directly.
_PRE_SIDE_EFFECT = frozenset({
    runmod.CREATED, runmod.PLANNING, runmod.OBSERVING, runmod.PROPOSING,
    runmod.AWAITING_GATE, runmod.AWAITING_HUMAN, runmod.RECOVERING,
})


@dataclass(frozen=True)
class RecoveryDecision:
    """How to resume one crashed run. For an ``executing`` crash, the verify_*
    ids name the observation/action to re-check before any retry."""

    run_id: str
    action: str
    reason: str
    from_state: str
    last_step_id: str = ""
    verify_observation_id: str = ""
    verify_action_id: str = ""


def in_flight_runs(store: Any) -> list[str]:
    """Run ids left non-terminal (i.e. crashed mid-run), sorted for determinism."""
    return sorted(
        rid for rid, r in store.entities("run").items()
        if r.get("state") not in runmod.TERMINAL_STATES
    )


def _last_step(store: Any, run_id: str) -> tuple[str, dict] | None:
    """The highest-seq step of a run, as (step_id, payload), or None."""
    steps = [(sid, s) for sid, s in store.entities("step").items() if s.get("run_id") == run_id]
    if not steps:
        return None
    return max(steps, key=lambda kv: int(kv[1].get("seq", 0)))


def recovery_for(store: Any, run_id: str) -> RecoveryDecision | None:
    """The recovery decision for one run, or None if the run is unknown.

    Terminal -> ``COMPLETE``. Otherwise the last step's ``state`` decides:
    ``executing`` -> ``REOBSERVE_BEFORE_RETRY`` (carrying its ids); ``verifying``
    -> ``REVERIFY``; anything pre-side-effect (or no steps yet) -> ``RESUME_STEP``."""
    run = store.get("run", run_id)
    if run is None:
        return None
    run_state = run.get("state", "")
    if run_state in runmod.TERMINAL_STATES:
        return RecoveryDecision(run_id, COMPLETE, "run already terminal", run_state)

    last = _last_step(store, run_id)
    if last is None:
        return RecoveryDecision(run_id, RESUME_STEP, "no step executed yet", run_state)
    step_id, step = last
    step_state = step.get("state", "")

    if step_state == runmod.EXECUTING:
        return RecoveryDecision(
            run_id, REOBSERVE_BEFORE_RETRY,
            "crashed mid-execute; re-observe to check the action already happened",
            run_state, last_step_id=step_id,
            verify_observation_id=step.get("observation_id", ""),
            verify_action_id=step.get("action_id", ""),
        )
    if step_state == runmod.VERIFYING:
        return RecoveryDecision(
            run_id, REVERIFY, "crashed during verification; re-run the check",
            run_state, last_step_id=step_id,
        )
    return RecoveryDecision(
        run_id, RESUME_STEP, f"crashed before a side effect (step state {step_state!r})",
        run_state, last_step_id=step_id,
    )


def plan_recovery(store: Any) -> list[RecoveryDecision]:
    """Recovery decisions for every in-flight run that actually needs one (i.e.
    excluding ``COMPLETE``)."""
    out: list[RecoveryDecision] = []
    for rid in in_flight_runs(store):
        d = recovery_for(store, rid)
        if d is not None and d.action != COMPLETE:
            out.append(d)
    return out


__all__ = [
    "COMPLETE", "RESUME_STEP", "REOBSERVE_BEFORE_RETRY", "REVERIFY",
    "RecoveryDecision", "in_flight_runs", "recovery_for", "plan_recovery",
]
