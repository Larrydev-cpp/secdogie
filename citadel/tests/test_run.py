"""Agent<->Citadel run closed loop (Phase 2.7).

A run and its steps are recorded as signed ``state`` events, so they materialize
through ``StateStore``, carry a tamper-evident per-run ``state_hash`` chain, and
converge over the mesh (Replication.1). All headless: the agent loop is faked by
a ``run_task`` that just emits synthetic steps via ``record_step``.
"""
from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from secdogie_citadel import run as runmod  # noqa: E402
from secdogie_citadel.journal import Journal  # noqa: E402
from secdogie_citadel.replication import ReplicationPeer  # noqa: E402
from secdogie_citadel.run import RunRecorder, verify_run  # noqa: E402
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
    ident = identity or Identity.generate()
    return Journal(identity=ident, allowlist=allow, clock=_counter())


def _materialize(journal):
    store = StateStore()
    store.merge_events(journal.events())
    return store


# --- recorder primitives ----------------------------------------------------


def test_start_run_materializes_a_run_entity():
    j = _journal()
    rec = RunRecorder(j)
    run_id = rec.start_run("g1")
    store = _materialize(j)
    entry = store.get("run", run_id)
    assert entry is not None
    assert entry["goal_id"] == "g1"
    assert entry["steps"] == 0
    assert entry["head_state_hash"] == runmod.GENESIS


def test_record_step_chains_state_hash_and_materializes_steps():
    j = _journal()
    rec = RunRecorder(j)
    run_id = rec.start_run("g1")
    s1 = rec.record_step(run_id, observation={"win": 1, "node": "Save"},
                         action={"kind": "click", "target": "Save"}, result="clicked")
    s2 = rec.record_step(run_id, observation={"win": 1, "node": "OK"},
                         action={"kind": "click", "target": "OK"}, result="done")
    assert s1 != s2
    store = _materialize(j)

    steps = store.entities("step")
    assert set(steps) == {s1, s2}
    assert steps[s1]["seq"] == 1 and steps[s2]["seq"] == 2
    assert steps[s1]["run_id"] == steps[s2]["run_id"] == run_id
    # each step carries observation/action ids + a state_hash; the chain advances
    assert steps[s1]["observation_id"] and steps[s1]["action_id"]
    assert steps[s2]["state_hash"] != steps[s1]["state_hash"]

    run_entry = store.get("run", run_id)
    assert run_entry["steps"] == 2
    assert run_entry["head_state_hash"] == steps[s2]["state_hash"]
    assert verify_run(run_id, store) == (True, None)


def test_state_hash_is_deterministic_and_content_bound():
    # the same inputs hash the same; changing the result changes the hash
    h1 = runmod.step_state_hash(runmod.GENESIS, "r", 1, "oid", "aid", "ok")
    h2 = runmod.step_state_hash(runmod.GENESIS, "r", 1, "oid", "aid", "ok")
    h3 = runmod.step_state_hash(runmod.GENESIS, "r", 1, "oid", "aid", "TAMPERED")
    assert h1 == h2 and h1 != h3


class _StubStore:
    """A materialized-state stand-in so a tampered chain can be verified without
    forging signed journal events (which merge would simply reject)."""

    def __init__(self, steps, run=None):
        self._steps, self._run = steps, run

    def entities(self, etype):
        return self._steps if etype == "step" else {}

    def get(self, etype, eid):
        return self._run if etype == "run" else None


def test_verify_run_detects_a_tampered_step():
    j = _journal()
    rec = RunRecorder(j)
    run_id = rec.start_run("g1")
    rec.record_step(run_id, observation={"o": 1}, action={"a": 1}, result="ok")
    rec.record_step(run_id, observation={"o": 2}, action={"a": 2}, result="ok")
    store = _materialize(j)
    assert verify_run(run_id, store) == (True, None)

    # forge a step's result but leave its state_hash: the chain no longer derives
    steps = {k: dict(v) for k, v in store.entities("step").items()}
    run_entry = dict(store.get("run", run_id))
    victim = min(steps, key=lambda k: steps[k]["seq"])
    steps[victim]["result"] = "owned"
    ok, reason = verify_run(run_id, _StubStore(steps, run_entry))
    assert ok is False and "state_hash" in (reason or "")


def test_finish_run_maps_code_to_terminal_state():
    j = _journal()
    rec = RunRecorder(j)
    assert rec.finish_run(rec.start_run("g1"), 0, "done") == runmod.COMPLETED
    assert rec.finish_run(rec.start_run("g2"), 5, "stopped") == runmod.STOPPED
    assert rec.finish_run(rec.start_run("g3"), 1, "boom") == runmod.FAILED
    store = _materialize(j)
    codes = {e["goal_id"]: (e["state"], e["code"]) for e in store.entities("run").values()}
    assert codes["g1"] == (runmod.COMPLETED, 0)
    assert codes["g2"] == (runmod.STOPPED, 5)
    assert codes["g3"] == (runmod.FAILED, 1)


# --- supervisor integration -------------------------------------------------


def _run_task_two_steps(task, *, should_stop, on_status, confirm, record_step=None):
    record_step(observation={"win": 1, "node": "Save"},
                action={"kind": "click", "target": "Save"}, result="clicked")
    record_step(observation={"win": 1, "node": "OK"},
                action={"kind": "click", "target": "OK"}, result="done", state=runmod.VERIFYING)
    return (0, "done")


def test_supervisor_run_goal_records_run_and_steps():
    j = _journal()
    sup = Supervisor(j, _run_task_two_steps)
    sup.add_goal("g1", title="g1")
    code = sup.run_goal("g1")
    assert code == (0, "done")

    store = _materialize(j)
    runs = store.entities("run")
    assert len(runs) == 1
    run_id, run_entry = next(iter(runs.items()))
    assert run_entry["goal_id"] == "g1"
    assert run_entry["steps"] == 2
    assert run_entry["state"] == runmod.COMPLETED and run_entry["code"] == 0
    assert verify_run(run_id, store) == (True, None)

    # the existing goal-level events are untouched (backward compatible)
    kinds = {e["kind"] for e in j.events()}
    assert {"goal", "status", "result"} <= kinds
    # the goal itself completed as before
    from secdogie_citadel.goals import build_goal_tree
    assert build_goal_tree(j.events()).nodes["g1"].status == "done"


def test_run_state_converges_over_replication():
    # a run recorded on A converges to B via ReplicationPeer -> both materialize
    # the identical run + steps (Phase 2.7 x Replication.1 = M2's state side).
    a, b = Identity.generate(), Identity.generate()
    allow = Allowlist({a.did, b.did})
    ja = Journal(identity=a, allowlist=allow, clock=_counter())
    jb = Journal(identity=b, allowlist=allow, clock=_counter(100.0))

    sup = Supervisor(ja, _run_task_two_steps)
    sup.add_goal("g1", title="g1")
    sup.run_goal("g1")

    # drive an in-proc replication exchange A <-> B to silence
    peers, queue = {}, []

    def sender(frm):
        return lambda to, payload: queue.append((frm, to, payload))

    pa = ReplicationPeer(ja, sender(a.did))
    pb = ReplicationPeer(jb, sender(b.did))
    peers[a.did], peers[b.did] = pa, pb
    pa.initiate(b.did)
    while queue:
        frm, to, payload = queue.pop(0)
        peers[to].on_message(frm, payload)

    ma, mb = _materialize(ja).materialize(), _materialize(jb).materialize()
    assert ma == mb                       # full state converged
    assert ma.get("run") and ma.get("step")
    run_id = next(iter(ma["run"]))
    assert verify_run(run_id, _materialize(jb)) == (True, None)
