"""Crash recovery for supervised runs (Phase 2.8).

A run left non-terminal by a crash is recovered from the materialized state. The
key rule: a crash while ``executing`` must re-observe before any retry (the action
may already have taken effect), so a crash never double-acts. All headless: the
decision is a pure function of the state; enforcing it rides the agent seam.
"""
from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from secdogie_citadel import recovery  # noqa: E402
from secdogie_citadel import run as runmod  # noqa: E402
from secdogie_citadel.journal import Journal  # noqa: E402
from secdogie_citadel.replication import ReplicationPeer  # noqa: E402
from secdogie_citadel.run import RunRecorder  # noqa: E402
from secdogie_citadel.state import StateStore  # noqa: E402
from secdogie_citadel.supervisor import Supervisor  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402


def _counter(start=0.0):
    n = {"t": start}

    def clock():
        n["t"] += 1.0
        return n["t"]

    return clock


def _journal(identity=None, allow=None):
    return Journal(identity=identity or Identity.generate(), allowlist=allow, clock=_counter())


def _materialize(journal):
    store = StateStore()
    store.merge_events(journal.events())
    return store


# --- per-state recovery decisions -------------------------------------------


def test_executing_crash_reobserves_before_retry():
    j = _journal()
    rec = RunRecorder(j)
    run_id = rec.start_run("g1")
    rec.record_step(run_id, observation={"win": 1}, action={"kind": "click", "target": "Submit"},
                    result="", state=runmod.EXECUTING)  # crashed here: no finish_run
    d = recovery.recovery_for(_materialize(j), run_id)
    assert d.action == recovery.REOBSERVE_BEFORE_RETRY
    assert d.from_state == runmod.EXECUTING
    # it carries the ids the agent must re-check before retrying
    assert d.verify_observation_id and d.verify_action_id


def test_verifying_crash_reverifies():
    j = _journal()
    rec = RunRecorder(j)
    run_id = rec.start_run("g1")
    rec.record_step(run_id, observation={"o": 1}, action={"a": 1}, result="did", state=runmod.VERIFYING)
    d = recovery.recovery_for(_materialize(j), run_id)
    assert d.action == recovery.REVERIFY and d.from_state == runmod.VERIFYING


def test_pre_side_effect_crash_resumes_step():
    j = _journal()
    rec = RunRecorder(j)
    # a step recorded in a pre-side-effect state (e.g. proposing) is safe to redo
    run_id = rec.start_run("g1")
    rec.record_step(run_id, observation={"o": 1}, action={"a": 1}, result="", state=runmod.PROPOSING)
    assert recovery.recovery_for(_materialize(j), run_id).action == recovery.RESUME_STEP


def test_run_with_no_steps_resumes():
    j = _journal()
    rec = RunRecorder(j)
    run_id = rec.start_run("g1")  # opened, crashed before any step
    d = recovery.recovery_for(_materialize(j), run_id)
    assert d.action == recovery.RESUME_STEP and "no step" in d.reason


def test_terminal_run_is_complete():
    j = _journal()
    rec = RunRecorder(j)
    run_id = rec.start_run("g1")
    rec.record_step(run_id, observation={"o": 1}, action={"a": 1}, result="ok", state=runmod.VERIFYING)
    rec.finish_run(run_id, 0, "done")
    assert recovery.recovery_for(_materialize(j), run_id).action == recovery.COMPLETE


def test_in_flight_runs_selects_only_crashed():
    j = _journal()
    rec = RunRecorder(j)
    done = rec.start_run("g-done")
    rec.finish_run(done, 0, "ok")
    crashed = rec.start_run("g-crash")
    rec.record_step(crashed, observation={"o": 1}, action={"a": 1}, result="", state=runmod.EXECUTING)
    store = _materialize(j)
    assert recovery.in_flight_runs(store) == [crashed]
    plans = recovery.plan_recovery(store)
    assert [p.run_id for p in plans] == [crashed]
    assert plans[0].action == recovery.REOBSERVE_BEFORE_RETRY


# --- recording + convergence ------------------------------------------------


def test_record_recovery_materializes_and_converges():
    a, b = Identity.generate(), Identity.generate()
    allow = Allowlist({a.did, b.did})
    ja = Journal(identity=a, allowlist=allow, clock=_counter())
    jb = Journal(identity=b, allowlist=allow, clock=_counter(100.0))
    rec = RunRecorder(ja)
    run_id = rec.start_run("g1")
    rec.record_step(run_id, observation={"o": 1}, action={"a": 1}, result="", state=runmod.EXECUTING)
    d = recovery.recovery_for(_materialize(ja), run_id)
    rec.record_recovery(run_id, d.action, from_state=d.from_state)

    entry = _materialize(ja).get("run", run_id)
    assert entry["state"] == runmod.RECOVERING
    assert entry["recovery"] == recovery.REOBSERVE_BEFORE_RETRY
    assert entry["recovered_from"] == runmod.EXECUTING

    # the recovery state is signed state, so it converges to B
    queue, peers = [], {}

    def sender(frm):
        return lambda to, payload: queue.append((frm, to, payload))

    pa = ReplicationPeer(ja, sender(a.did))
    pb = ReplicationPeer(jb, sender(b.did))
    peers[a.did], peers[b.did] = pa, pb
    pa.initiate(b.did)
    while queue:
        frm, to, payload = queue.pop(0)
        peers[to].on_message(frm, payload)
    assert _materialize(jb).get("run", run_id) == entry


# --- supervisor integration -------------------------------------------------


def _noop_run_task(task, *, should_stop, on_status, confirm, record_step=None):
    return (0, "ok")


def test_supervisor_recover_runs_marks_executing_crash():
    j = _journal()
    sup = Supervisor(j, _noop_run_task)
    # simulate a crash: a run opened + a step left executing, never finished
    run_id = sup.recorder.start_run("g1")
    sup.recorder.record_step(run_id, observation={"win": 1}, action={"kind": "click"},
                             result="", state=runmod.EXECUTING)
    # a second run that completed cleanly -> no recovery
    done = sup.recorder.start_run("g2")
    sup.recorder.finish_run(done, 0, "ok")

    decisions = sup.recover_runs()
    assert [d.run_id for d in decisions] == [run_id]
    assert decisions[0].action == recovery.REOBSERVE_BEFORE_RETRY

    store = _materialize(j)
    assert store.get("run", run_id)["state"] == runmod.RECOVERING
    assert store.get("run", done)["state"] == runmod.COMPLETED  # untouched
    # a second pass is stable (the executing step still dictates re-observe)
    again = sup.recover_runs()
    assert [d.action for d in again] == [recovery.REOBSERVE_BEFORE_RETRY]
