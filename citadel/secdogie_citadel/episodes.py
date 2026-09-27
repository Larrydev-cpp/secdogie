"""Episodic memory (S1): what happened in each run, as a view of the journal.

Stage S1 of the staged memory stores nothing of its own. A run and its steps are
already signed ``state`` events on the journal (``run.RunRecorder``), hash-chained
per run and replicated over the mesh; an *episode* is simply that run folded
into one immutable record -- which goal it served, how it ended, and for each
step the action's effect hash, the gate's verdict and findings, and the outcome.

Every episode carries ``verified``: whether its step chain re-derives
(``run.check_chain``). Later stages learn only from verified, finished
episodes, so a tampered or half-written run never becomes a lesson.

Pure: ``(StateStore | events) -> {run_id: Episode}``, like ``goals.py``. The
store is materialized once, whatever the number of runs.
"""
from __future__ import annotations

from dataclasses import dataclass

from .run import OUTCOMES, TERMINAL_STATES, check_chain
from .state import StateStore


@dataclass(frozen=True)
class StepRecord:
    run_id: str
    seq: int
    action_key: str  # authz.action_hash of the gated action; "" if not reported
    verdict: str
    findings: tuple[str, ...]
    outcome: str  # one of run.OUTCOMES; "unknown" when not reported
    result: str


@dataclass(frozen=True)
class Episode:
    run_id: str
    goal_id: str
    state: str
    code: int | None
    steps: tuple[StepRecord, ...]
    verified: bool  # the run's step chain re-derives
    problem: str | None = None  # why it is not verified

    @property
    def finished(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def usable(self) -> bool:
        """Safe to learn from: finished and untampered."""
        return self.finished and self.verified


def build_episodes(store: StateStore) -> dict[str, Episode]:
    """Every run in ``store`` as an Episode, keyed by run id."""
    state = store.materialize()
    runs = state.get("run", {})
    by_run: dict[str, list[dict]] = {}
    for s in state.get("step", {}).values():
        by_run.setdefault(str(s.get("run_id", "")), []).append(s)
    out: dict[str, Episode] = {}
    for run_id, run in runs.items():
        raw_steps = by_run.get(run_id, [])
        ok, problem = check_chain(run_id, run, raw_steps)
        steps = tuple(sorted((_step(run_id, s) for s in raw_steps), key=lambda r: r.seq))
        code = run.get("code")
        out[run_id] = Episode(
            run_id=run_id,
            goal_id=str(run.get("goal_id", "")),
            state=str(run.get("state", "")),
            code=code if type(code) is int else None,
            steps=steps,
            verified=ok,
            problem=problem,
        )
    return out


def episodes_from_events(events) -> dict[str, Episode]:
    """Convenience: fold a journal event list straight into episodes."""
    store = StateStore()
    store.merge_events(events)
    return build_episodes(store)


def _step(run_id: str, s: dict) -> StepRecord:
    try:
        seq = int(s.get("seq", 0))
    except (TypeError, ValueError):
        seq = 0
    outcome = s.get("outcome")
    findings = s.get("findings")
    return StepRecord(
        run_id=run_id,
        seq=seq,
        action_key=str(s.get("action_key") or ""),
        verdict=str(s.get("verdict") or ""),
        findings=tuple(str(f) for f in findings) if isinstance(findings, list) else (),
        outcome=outcome if outcome in OUTCOMES else "unknown",
        result=str(s.get("result") or ""),
    )


__all__ = ["StepRecord", "Episode", "build_episodes", "episodes_from_events"]
