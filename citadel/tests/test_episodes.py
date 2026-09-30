"""Episodic memory (S1): runs folded from the journal into episodes, each checked
for an intact step chain. Only finished, verified episodes are usable."""
from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from secdogie_citadel import run as runmod  # noqa: E402
from secdogie_citadel.episodes import build_episodes, episodes_from_events  # noqa: E402
from secdogie_citadel.journal import Journal  # noqa: E402
from secdogie_citadel.run import RunRecorder, verify_run  # noqa: E402
from secdogie_citadel.state import StateStore, record_state  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402


def _counter():
    n = {"t": 0.0}

    def clock():
        n["t"] += 1.0
        return n["t"]

    return clock


def _journal(identity=None, allow=None):
    return Journal(identity=identity or Identity.generate(), allowlist=allow, clock=_counter())


def _store(journal):
    s = StateStore()
    s.merge_events(journal.events())
    return s


def _run(journal, goal="g1", steps=(("k1", "ok"), ("k2", "failed")), code=0):
    rec = RunRecorder(journal)
    rid = rec.start_run(goal)
    for i, (key, outcome) in enumerate(steps):
        rec.record_step(rid, observation={"o": i}, action={"a": key}, result=f"r{i}", verdict="allow",
                        action_key=key, outcome=outcome, findings=("missing-verification",) if i else ())
    rec.finish_run(rid, code)
    return rid


def test_a_run_becomes_an_episode():
    j = _journal()
    rid = _run(j)
    ep = build_episodes(_store(j))[rid]
    assert ep.goal_id == "g1" and ep.state == runmod.COMPLETED and ep.code == 0
    assert ep.verified and ep.finished and ep.usable and ep.problem is None
    assert [(s.seq, s.action_key, s.outcome) for s in ep.steps] == [(1, "k1", "ok"), (2, "k2", "failed")]
    assert ep.steps[0].findings == () and ep.steps[1].findings == ("missing-verification",)
    assert ep.steps[1].verdict == "allow" and ep.steps[1].result == "r1"


def test_the_new_fields_do_not_touch_the_chain():
    j = _journal()
    rid = _run(j)
    assert verify_run(rid, _store(j)) == (True, None)
    # same author, same step, with and without the memory fields: same hash
    same = Identity.generate()
    a, b = _journal(same), _journal(same)
    for journal, extra in ((a, {}), (b, {"action_key": "k", "outcome": "ok", "findings": ("x",)})):
        rec = RunRecorder(journal)
        rid2 = rec.start_run("g")
        rec.record_step(rid2, observation={"o": 1}, action={"a": 1}, result="r", **extra)
    sa = next(iter(_store(a).entities("step").values()))
    sb = next(iter(_store(b).entities("step").values()))
    assert sa["state_hash"] == sb["state_hash"]
    assert "action_key" not in sa and sb["action_key"] == "k"


def test_old_steps_without_memory_fields_read_as_unknown():
    j = _journal()
    rec = RunRecorder(j)
    rid = rec.start_run("g")
    rec.record_step(rid, observation={"o": 1}, action={"a": 1}, result="r")
    ep = build_episodes(_store(j))[rid]
    assert ep.steps[0].action_key == "" and ep.steps[0].outcome == "unknown" and ep.steps[0].findings == ()


def test_an_unknown_outcome_is_refused_at_record_time():
    rec = RunRecorder(_journal())
    rid = rec.start_run("g")
    with pytest.raises(ValueError):
        rec.record_step(rid, action_key="k", outcome="great")


def test_an_in_flight_run_is_not_usable():
    j = _journal()
    rec = RunRecorder(j)
    rid = rec.start_run("g")
    rec.record_step(rid, action_key="k", outcome="failed")
    ep = build_episodes(_store(j))[rid]
    assert ep.verified and not ep.finished and not ep.usable


def test_a_tampered_step_makes_the_episode_unusable():
    j = _journal()
    rid = _run(j)
    step_id = next(sid for sid, s in _store(j).entities("step").items() if s["seq"] == 2)
    # a validly signed later patch rewrites the step's result: the chain no longer re-derives
    record_state(j, "step", step_id, "patch", {"result": "it worked, honest"})
    ep = build_episodes(_store(j))[rid]
    assert not ep.verified and not ep.usable and "state_hash mismatch" in ep.problem


def test_malformed_steps_do_not_crash_the_fold():
    j = _journal()
    rec = RunRecorder(j)
    rid = rec.start_run("g")
    record_state(j, "step", "bogus", "set", {"run_id": rid, "seq": "two", "findings": "nope", "outcome": 7})
    rec.finish_run(rid, 0)
    ep = build_episodes(_store(j))[rid]
    assert not ep.verified and ep.problem == "malformed step"
    assert ep.steps[0].seq == 0 and ep.steps[0].findings == () and ep.steps[0].outcome == "unknown"


def test_steps_written_out_of_order_come_back_in_order():
    j = _journal()
    rec = RunRecorder(j)
    rid = rec.start_run("g")
    h1 = runmod.step_state_hash(runmod.GENESIS, rid, 1, "o1", "a1", "r1")
    h2 = runmod.step_state_hash(h1, rid, 2, "o2", "a2", "r2")
    for sid, seq, h in (("s2", 2, h2), ("s1", 1, h1)):  # seq 2 lands first
        record_state(j, "step", sid, "set", {"run_id": rid, "seq": seq, "observation_id": f"o{seq}",
                                             "action_id": f"a{seq}", "result": f"r{seq}", "state_hash": h,
                                             "action_key": f"k{seq}", "outcome": "ok"})
    record_state(j, "run", rid, "patch", {"steps": 2, "head_state_hash": h2})
    rec.finish_run(rid, 0)
    ep = build_episodes(_store(j))[rid]
    assert ep.verified and [s.seq for s in ep.steps] == [1, 2]


def test_a_non_integer_exit_code_is_not_trusted():
    j = _journal()
    rid = _run(j)
    for bad in ("0", True, 1.5):
        record_state(j, "run", rid, "patch", {"code": bad})
        assert build_episodes(_store(j))[rid].code is None


def test_episodes_from_replicated_journals():
    a_id, b_id = Identity.generate(), Identity.generate()
    allow = Allowlist({a_id.did, b_id.did})
    a, b = _journal(a_id, allow), _journal(b_id, allow)
    ra = _run(a, goal="ga")
    rb = _run(b, goal="gb", steps=(("k9", "no_change"),), code=1)
    a.merge(b.events())
    eps = episodes_from_events(a.events())
    assert set(eps) == {ra, rb}
    assert eps[rb].state == runmod.FAILED and eps[rb].steps[0].outcome == "no_change" and eps[rb].usable
