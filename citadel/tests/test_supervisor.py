from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from secdogie_citadel.goals import build_goal_tree  # noqa: E402
from secdogie_citadel.journal import Journal  # noqa: E402
from secdogie_citadel.supervisor import Supervisor  # noqa: E402
from secdogie_identity import Identity  # noqa: E402


def _counter():
    n = {"t": 0.0}

    def clock():
        n["t"] += 1.0
        return n["t"]

    return clock


def _journal():
    return Journal(identity=Identity.generate(), clock=_counter())


def _status_of(sup, gid):
    return build_goal_tree(sup.journal.events()).nodes[gid].status


def _behaviors(mapping):
    """run_task that dispatches on the task string (we set title == goal id)."""
    def run_task(task, *, should_stop, on_status, confirm, record_step=None):
        fn = mapping.get(task, lambda **k: (0, "ok"))
        return fn(should_stop=should_stop, on_status=on_status, confirm=confirm)
    return run_task


def test_runs_goals_in_dependency_order():
    ran = []

    def record(name):
        def fn(should_stop, on_status, confirm):
            ran.append(name)
            return (0, "ok")
        return fn

    j = _journal()
    sup = Supervisor(j, _behaviors({"g1": record("g1"), "g2": record("g2")}))
    sup.add_goal("g1", title="g1")
    sup.add_goal("g2", title="g2", deps=["g1"])
    results = sup.run_ready()
    assert ran == ["g1", "g2"]  # g2 waited for g1
    assert [gid for gid, _c, _s in results] == ["g1", "g2"]
    assert _status_of(sup, "g1") == "done"
    assert _status_of(sup, "g2") == "done"


def test_failure_marks_failed_and_does_not_loop():
    j = _journal()
    sup = Supervisor(j, _behaviors({"g1": lambda **k: (1, "boom")}), max_attempts=1)
    sup.add_goal("g1", title="g1")
    results = sup.run_ready()
    assert results == [("g1", 1, "boom")]
    assert _status_of(sup, "g1") == "failed"


def test_retry_then_succeed():
    calls = {"n": 0}

    def flaky(should_stop, on_status, confirm):
        calls["n"] += 1
        return (0, "ok") if calls["n"] >= 2 else (1, "transient")

    j = _journal()
    sup = Supervisor(j, _behaviors({"g1": flaky}), max_attempts=2)
    sup.add_goal("g1", title="g1")
    sup.run_ready()
    assert calls["n"] == 2
    assert _status_of(sup, "g1") == "done"


def test_resume_does_not_rerun_done_goals():
    ran = []

    def once(should_stop, on_status, confirm):
        ran.append(1)
        return (0, "ok")

    j = _journal()
    Supervisor(j, _behaviors({"g1": once})).add_goal("g1", title="g1")
    Supervisor(j, _behaviors({"g1": once})).run_ready()  # first process: runs g1
    assert ran == [1]
    # a fresh supervisor over the SAME journal (restart) must not re-run g1
    restarted = Supervisor(j, _behaviors({"g1": once}))
    assert restarted.run_ready() == []
    assert ran == [1]


def test_recover_requeues_a_crashed_active_goal():
    j = _journal()
    sup = Supervisor(j, _behaviors({"g1": lambda **k: (0, "ok")}))
    sup.add_goal("g1", title="g1")
    # simulate a crash mid-run: marked active, no result
    j.append("goal", {"op": "update", "id": "g1", "status": "active"})
    assert sup.pending_ready() == []          # active is not ready
    assert sup.recover() == ["g1"]            # re-queued to pending
    assert sup.run_ready() == [("g1", 0, "ok")]
    assert _status_of(sup, "g1") == "done"


def test_pause_parks_then_resume_runs():
    ran = []

    def go(should_stop, on_status, confirm):
        ran.append(1)
        return (0, "ok")

    j = _journal()
    sup = Supervisor(j, _behaviors({"g1": go}))
    sup.add_goal("g1", title="g1")
    sup.request_pause("g1")
    assert sup.run_ready() == []   # parked
    assert ran == []
    sup.resume("g1")
    assert sup.run_ready() == [("g1", 0, "ok")]
    assert ran == [1]


def test_stop_control_is_seen_mid_run_via_should_stop():
    j = _journal()

    def stopper(should_stop, on_status, confirm):
        # a stop lands for this goal while it runs
        j.append("control", {"op": "stop", "goal_id": "g1"})
        return (5, "stopped") if should_stop() else (0, "ok")

    sup = Supervisor(j, _behaviors({"g1": stopper}))
    sup.add_goal("g1", title="g1")
    code = sup.run_goal("g1")
    assert code == (5, "stopped")             # should_stop() reflected the journal
    assert _status_of(sup, "g1") == "pending"  # parked, not failed


def test_confirm_gate_fails_closed_by_default_and_records():
    seen = {}

    def risky(should_stop, on_status, confirm):
        seen["approved"] = confirm("delete temp files", True)
        return (0, "ok")

    j = _journal()
    sup = Supervisor(j, _behaviors({"g1": risky}))  # no confirm handler -> deny
    sup.add_goal("g1", title="g1")
    sup.run_goal("g1")
    assert seen["approved"] is False
    kinds = [e["kind"] for e in j.events()]
    assert "confirm_request" in kinds and "confirm_result" in kinds


def test_confirm_handler_can_approve():
    seen = {}

    def risky(should_stop, on_status, confirm):
        seen["approved"] = confirm("save the drawing", True)
        return (0, "ok")

    j = _journal()
    sup = Supervisor(j, _behaviors({"g1": risky}), confirm_handler=lambda prompt, hr: True)
    sup.add_goal("g1", title="g1")
    sup.run_goal("g1")
    assert seen["approved"] is True
