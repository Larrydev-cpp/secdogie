"""Stage 3 (mesh): two nodes whose journals replicate. What a node learns from a
peer is shared -- a caution earned by the peer's failing runs reaches this
node's Gate 1 -- but what a node *does* stays its own: a peer's goals, stops
and interrupted runs arrive through replication and are never run, obeyed or
recovered here."""
from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from secdogie_citadel.authz import action_hash  # noqa: E402
from secdogie_citadel.journal import Journal  # noqa: E402
from secdogie_citadel.loop_gate import to_planned  # noqa: E402
from secdogie_citadel.supervisor import MemoryConfig, Supervisor  # noqa: E402
from secdogie_citadel.sync import sync_round  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402

A, B = Identity.generate(), Identity.generate()
MESH = Allowlist({A.did, B.did})
CLICK = {"kind": "left_click", "element": None, "x": 10, "y": 20, "text": "", "keys": [], "path": "",
         "high_risk": False, "rollback": "", "irreversible": False}


class Loop:
    """A fake agent loop: asks the plan gate, records the step, reports."""

    def __init__(self, outcome):
        self.outcome, self.gate, self.ran = outcome, [], []

    def __call__(self, task, *, should_stop, on_status, confirm, record_step, plan_gate=None, **_):
        self.ran.append(task)
        allowed, note = plan_gate(CLICK, [])
        self.gate.append((allowed, note))
        record_step(observation={"f": 1}, action=CLICK, result="clicked" if allowed else note,
                    outcome=self.outcome if allowed else "rejected")
        return 0, "done"


def _node(identity, loop):
    journal = Journal(identity=identity, allowlist=MESH)
    return Supervisor(journal, loop, memory=MemoryConfig(min_runs=3), unrestricted=True)


def test_a_peers_goals_are_never_this_nodes_to_run():
    a, b = _node(A, Loop("ok")), _node(B, Loop("ok"))
    a.add_goal("g1", "file the report")
    sync_round(a.journal, b.journal)
    assert a.pending_ready() == ["g1"]
    assert b.pending_ready() == []  # A's goal is in B's journal, not in B's queue
    b.add_goal("g1", "tidy up")  # the same id on B is B's own, separate goal
    assert b.pending_ready() == ["g1"]
    b.run_goal("g1")
    assert b.run_task.ran == ["tidy up"]
    sync_round(a.journal, b.journal)
    assert a.pending_ready() == ["g1"]  # B finishing its g1 does not finish A's


def test_a_peers_stop_and_interrupted_goals_stay_the_peers():
    a, b = _node(A, Loop("ok")), _node(B, Loop("ok"))
    a.add_goal("g1", "file the report")
    a.journal.append("goal", {"op": "update", "id": "g1", "status": "active"})  # A crashed mid-goal...
    a.recorder.start_run("g1", state="executing")  # ...while acting
    assert a.recover_runs() != []  # on A, that run needs recovering
    a.request_stop("g2")
    b.add_goal("g2", "tidy up")
    sync_round(a.journal, b.journal)
    before = len(b.journal.events())
    assert b.recover() == []  # nothing of A's is requeued on B
    assert b.recover_runs() == []
    assert len(b.journal.events()) == before
    assert b.pending_ready() == ["g2"]  # A's stop names A's g2, not B's


def test_a_caution_learned_by_a_peer_reaches_this_nodes_gate():
    loop_a, loop_b = Loop("failed"), Loop("ok")
    a, b = _node(A, loop_a), _node(B, loop_b)
    for i in range(3):
        a.add_goal(f"g{i}", "click the toolbar")
        a.run_goal(f"g{i}")
    key = action_hash(to_planned(CLICK))
    assert key in a.memory_view().known_failures
    assert key not in b.memory_view().known_failures
    sync_round(a.journal, b.journal)
    assert key in b.memory_view().known_failures
    b.add_goal("h1", "click the toolbar")
    b.run_goal("h1")
    allowed, note = loop_b.gate[-1]
    assert not allowed and "failed repeatedly" in note  # refused on B's first try


def test_a_peers_failed_attempts_do_not_use_up_this_nodes_tries():
    def failing(task, **_):
        return 1, "nope"

    a = Supervisor(Journal(identity=A, allowlist=MESH), failing, max_attempts=2, unrestricted=True)
    b = Supervisor(Journal(identity=B, allowlist=MESH), failing, max_attempts=2, unrestricted=True)
    a.add_goal("g1", "file the report")
    a.run_goal("g1")
    sync_round(a.journal, b.journal)
    b.add_goal("g1", "file the report")
    b.run_goal("g1")  # B's first try: one left
    assert b.pending_ready() == ["g1"]
    b.run_goal("g1")  # B's second: now it has failed
    assert b.pending_ready() == []
