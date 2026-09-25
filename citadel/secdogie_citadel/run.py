"""Agent<->Citadel run closed loop (Phase 2.7): record a supervised run as signed
state, step by step, so it materializes into the ``StateStore`` and converges over
the mesh (Replication.1).

A goal handed to the ``Supervisor`` becomes a *run*; each observe->gate->execute->
verify iteration the agent takes becomes a *step*. This module records both as
signed ``state`` events on the journal:

    goal -> run -> step[0..n]  (observation_id, action_id, result, state_hash)

Two properties, both from reusing what already exists:

  * **Authentic + convergent for free.** Runs and steps are ordinary ``state``
    events (``state.record_state``), so they inherit the journal's per-event
    signature + allowlist gate and materialize through ``StateStore``; a run
    executed on one node replicates to the rest of the mesh unchanged.
  * **Tamper-evident per run.** Each step commits to the previous step's
    ``state_hash`` (``H(prev, run_id, seq, observation_id, action_id, result)``),
    the same hash-chain shape as the journal and the agent's ExecutionTrace, so
    ``verify_run`` can re-derive the chain and detect any altered step.

This layer only *records*; it never executes. Execution still passes the
Safety/Socratic gate and human-in-the-loop. No new crypto, no memory writes, no
network here (the recorder holds an injected journal). Pure and headless-testable.
"""
from __future__ import annotations

import hashlib
from typing import Any

from secdogie_identity import canonical

from .state import record_state

# GENESIS anchor for a run's step chain (same shape as journal.GENESIS / trace).
GENESIS = "0" * 64

# Run lifecycle states. 2.7 drives the normal path + terminal states; the
# crash-recovery transitions between them are Phase 2.8's concern.
CREATED = "created"
PLANNING = "planning"
OBSERVING = "observing"
PROPOSING = "proposing"
AWAITING_GATE = "awaiting_gate"
AWAITING_HUMAN = "awaiting_human"
EXECUTING = "executing"
VERIFYING = "verifying"
RECOVERING = "recovering"  # re-entered after a crash; see recovery.py (2.8)
COMPLETED = "completed"
FAILED = "failed"
STOPPED = "stopped"

RUN_STATES = frozenset({
    CREATED, PLANNING, OBSERVING, PROPOSING, AWAITING_GATE, AWAITING_HUMAN,
    EXECUTING, VERIFYING, RECOVERING, COMPLETED, FAILED, STOPPED,
})

# Non-terminal states: a run left in one of these by a crash needs recovery (2.8).
TERMINAL_STATES = frozenset({COMPLETED, FAILED, STOPPED})

# Terminal code -> run state (mirrors the agent loop's exit codes: 0 ok, 5 stopped).
_CODE_STATE = {0: COMPLETED, 5: STOPPED}


def _content_id(obj: Any) -> str:
    """A stable id for an observation/action: its own ``content_hash`` when it has
    one (e.g. observation.Observation), the string itself when already an id, or a
    canonical-JSON sha256 of a dict. ``None``/empty -> ""."""
    if obj is None or obj == "":
        return ""
    ch = getattr(obj, "content_hash", None)
    if isinstance(ch, str) and ch:
        return ch
    if isinstance(obj, str):
        return obj
    return hashlib.sha256(canonical(obj)).hexdigest()


def _short(material: dict) -> str:
    return hashlib.sha256(canonical(material)).hexdigest()[:16]


def step_state_hash(prev: str, run_id: str, seq: int, observation_id: str, action_id: str, result: str) -> str:
    """The chained hash for one step -- commits to the previous step's hash, so a
    run's steps form a tamper-evident chain (re-derivable by ``verify_run``)."""
    return hashlib.sha256(canonical({
        "prev": prev, "run_id": run_id, "seq": int(seq),
        "observation_id": observation_id, "action_id": action_id, "result": str(result),
    })).hexdigest()


class RunRecorder:
    """Records a supervised run + its steps as signed ``state`` events on a
    journal. One recorder per node; it holds only the in-memory head of each
    active run (durable truth is the journal). ``record_step`` is the seam the
    Supervisor / agent adapter feeds."""

    def __init__(self, journal: Any, *, clock=None):
        self.journal = journal
        # default clock: the journal's own, so run timestamps match its events
        self._clock = clock or getattr(journal, "_clock", None)
        self._run_seq = 0
        self._runs: dict[str, dict] = {}  # run_id -> {"head": hash, "steps": n}

    def _now(self) -> float:
        return float(self._clock()) if self._clock is not None else 0.0

    def _author(self) -> str:
        ident = getattr(self.journal, "identity", None)
        return getattr(ident, "did", "") if ident is not None else ""

    def start_run(self, goal_id: str, *, state: str = CREATED) -> str:
        """Open a run for ``goal_id`` and record its ``run`` entity. Returns the
        deterministic ``run_id``."""
        self._run_seq += 1
        ts = self._now()
        run_id = _short({"author": self._author(), "goal_id": goal_id, "run_seq": self._run_seq, "ts": ts})
        self._runs[run_id] = {"head": GENESIS, "steps": 0}
        record_state(self.journal, "run", run_id, "set", {
            "goal_id": goal_id, "state": state, "started_at": ts,
            "steps": 0, "head_state_hash": GENESIS,
        })
        return run_id

    def record_step(
        self, run_id: str, *, observation: Any = None, action: Any = None,
        result: str = "", verdict: str = "", state: str = EXECUTING,
    ) -> str:
        """Record one observe->act->result step: chain its ``state_hash``, write the
        ``step`` entity, and advance the run's head/step-count. Returns ``step_id``."""
        run = self._runs.get(run_id)
        if run is None:  # recorder restarted mid-run: resume from GENESIS-at-0
            run = self._runs.setdefault(run_id, {"head": GENESIS, "steps": 0})
        seq = run["steps"] + 1
        oid, aid = _content_id(observation), _content_id(action)
        sh = step_state_hash(run["head"], run_id, seq, oid, aid, result)
        step_id = _short({"run_id": run_id, "seq": seq})
        record_state(self.journal, "step", step_id, "set", {
            "run_id": run_id, "seq": seq, "observation_id": oid, "action_id": aid,
            "result": str(result), "verdict": verdict, "state": state, "state_hash": sh,
        })
        run["head"], run["steps"] = sh, seq
        record_state(self.journal, "run", run_id, "patch", {
            "steps": seq, "head_state_hash": sh, "state": state,
        })
        return step_id

    def transition(self, run_id: str, state: str) -> None:
        """Move a run to a new lifecycle ``state`` (no step recorded)."""
        if state not in RUN_STATES:
            raise ValueError(f"unknown run state {state!r}")
        record_state(self.journal, "run", run_id, "patch", {"state": state})

    def record_recovery(self, run_id: str, action: str, *, from_state: str) -> None:
        """Record a crash-recovery decision (2.8) on a run: move it to
        ``recovering`` and stamp the chosen action + the state it crashed in. A
        signed ``state`` patch, so it materializes and converges like any other."""
        record_state(self.journal, "run", run_id, "patch", {
            "state": RECOVERING, "recovery": action, "recovered_from": from_state,
        })

    def finish_run(self, run_id: str, code: int, summary: str = "") -> str:
        """Close a run: map the exit ``code`` to a terminal state and record it.
        Returns the terminal state."""
        state = _CODE_STATE.get(int(code), FAILED)
        record_state(self.journal, "run", run_id, "patch", {
            "state": state, "code": int(code), "summary": str(summary), "finished_at": self._now(),
        })
        return state


def verify_run(run_id: str, store: Any) -> tuple[bool, str | None]:
    """Re-derive a run's step chain from a materialized ``StateStore`` and check
    every ``state_hash``. Returns ``(ok, reason)``; ``reason`` names the first bad
    step. A run with no steps is trivially ok."""
    steps = [s for s in store.entities("step").values() if s.get("run_id") == run_id]
    steps.sort(key=lambda s: int(s.get("seq", 0)))
    prev = GENESIS
    for i, s in enumerate(steps):
        if int(s.get("seq", 0)) != i + 1:
            return False, f"step {i + 1}: seq out of order"
        want = step_state_hash(
            prev, run_id, int(s["seq"]), s.get("observation_id", ""),
            s.get("action_id", ""), s.get("result", ""),
        )
        if s.get("state_hash") != want:
            return False, f"step {s.get('seq')}: state_hash mismatch (chain broken)"
        prev = want
    run = store.get("run", run_id)
    if run is not None and run.get("steps") and run.get("head_state_hash") != prev:
        return False, "run head_state_hash does not match its last step"
    return True, None


__all__ = [
    "RunRecorder", "verify_run", "step_state_hash", "GENESIS", "RUN_STATES",
    "TERMINAL_STATES",
    "CREATED", "PLANNING", "OBSERVING", "PROPOSING", "AWAITING_GATE",
    "AWAITING_HUMAN", "EXECUTING", "VERIFYING", "RECOVERING",
    "COMPLETED", "FAILED", "STOPPED",
]
