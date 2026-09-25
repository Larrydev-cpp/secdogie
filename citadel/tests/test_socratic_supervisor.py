"""The Socratic step inside Supervisor.run_goal: a goal runs as its accepted
(possibly revised) instruction, an overlong goal becomes ordered sub-goals, and
only what cannot be rewritten waits for the operator -- all on the record."""
from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from secdogie_citadel.cli import main  # noqa: E402
from secdogie_citadel.goals import build_goal_tree  # noqa: E402
from secdogie_citadel.journal import Journal  # noqa: E402
from secdogie_citadel.supervisor import DECOMPOSED, NEEDS_INPUT, Supervisor  # noqa: E402
from secdogie_identity import Identity  # noqa: E402


def _counter():
    n = {"t": 0.0}

    def clock():
        n["t"] += 1.0
        return n["t"]

    return clock


def _sup():
    tasks = []

    def run_task(task, *, should_stop, on_status, confirm, record_step=None):
        tasks.append(task)
        return (0, "ok")

    journal = Journal(identity=Identity.generate(), clock=_counter())
    return Supervisor(journal, run_task), journal, tasks


def _kinds(journal, kind):
    return [e["body"] for e in journal.events() if e["kind"] == kind]


def test_goal_runs_as_its_revised_instruction():
    sup, journal, tasks = _sup()
    sup.add_goal("g1", title="post the weekly report to the team channel automatically without asking")
    assert sup.run_goal("g1") == (0, "ok")
    assert len(tasks) == 1 and "ask the user to confirm" in tasks[0] and "automatically" not in tasks[0]
    # the goal now carries the revised instruction
    assert build_goal_tree(journal.events()).nodes["g1"].title == tasks[0]
    # every round and the rewrite are on the record
    reviews = _kinds(journal, "socratic")
    assert [r["verdict"] for r in reviews] == ["revise", "accept"]
    assert all(r["goal_id"] == "g1" for r in reviews)
    (rev,) = _kinds(journal, "socratic_revision")
    assert "automatically" in rev["from"] and rev["to"] == tasks[0] and "unattended_posting" in rev["codes"]


def test_clean_goal_runs_unchanged_with_one_accepting_review():
    sup, journal, tasks = _sup()
    sup.add_goal("g1", title="open the settings page and read the version")
    sup.run_goal("g1")
    assert tasks == ["open the settings page and read the version"]
    assert [r["verdict"] for r in _kinds(journal, "socratic")] == ["accept"]
    assert _kinds(journal, "socratic_revision") == []


def test_overlong_goal_runs_as_ordered_sub_goals():
    sup, journal, tasks = _sup()
    steps = [f"step {i}" for i in range(8)]
    sup.add_goal("big", title=" and then ".join(steps))
    sup.add_goal("after", title="write the summary", deps=["big"])
    results = sup.run_ready()
    # the sub-goals ran in order, the overlong text itself never ran, and the
    # dependent waited for all of it
    assert tasks == steps + ["write the summary"]
    assert results[0] == ("big", DECOMPOSED, results[0][2])
    tree = build_goal_tree(journal.events())
    assert all(tree.nodes[f"big.{i}"].status == "done" for i in range(1, 9))
    assert tree.nodes["big"].status == "done" and tree.nodes["after"].status == "done"
    (dec,) = _kinds(journal, "socratic_decomposed")
    assert dec["children"] == [f"big.{i}" for i in range(1, 9)]


def test_unrewritable_goal_waits_for_the_operator_then_runs(tmp_path, capsys):
    key = tmp_path / "node.key"
    Identity.generate().save(key)
    db = str(tmp_path / "j.db")
    tasks = []

    def run_task(task, *, should_stop, on_status, confirm, record_step=None):
        tasks.append(task)
        return (0, "ok")

    journal = Journal(db, identity=Identity.load(key))
    sup = Supervisor(journal, run_task)
    sup.add_goal("g1", title="   ")
    code, summary = sup.run_goal("g1")
    assert code == NEEDS_INPUT and "empty instruction" in summary
    assert tasks == []
    assert build_goal_tree(journal.events()).nodes["g1"].status == "needs_input"
    assert "g1" not in sup.pending_ready()  # parked, not retried in a loop

    # the operator fills it in; it is back in the queue and runs
    assert main(["set-goal", db, "g1", "--identity", str(key), "--title", "read the version"]) == 0
    sup2 = Supervisor(Journal(db, identity=Identity.load(key)), run_task)
    assert sup2.run_goal("g1") == (0, "ok")
    assert tasks == ["read the version"]
