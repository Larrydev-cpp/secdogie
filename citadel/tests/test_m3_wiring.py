"""M3 wiring: the Supervisor feeds grants and crash recovery into the run.

  * Capability grants carried in the journal reach the agent loop as a plan gate
    (untrusted grants give nothing; without issuers no gate is passed at all).
  * A goal whose previous run crashed while executing is re-run with a
    check-before-redoing note, and the crashed run is closed as superseded.
"""
from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from secdogie_citadel.journal import Journal  # noqa: E402
from secdogie_citadel.recovery import REOBSERVE_BEFORE_RETRY, recovery_preamble  # noqa: E402
from secdogie_citadel.run import EXECUTING, STOPPED  # noqa: E402
from secdogie_citadel.state import StateStore  # noqa: E402
from secdogie_citadel.supervisor import Supervisor  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_identity.capability import create_capability  # noqa: E402


def _counter():
    n = {"t": 0.0}

    def clock():
        n["t"] += 1.0
        return n["t"]

    return clock


def _view(kind, **kw):
    return {"kind": kind, **kw}


def test_grants_in_the_journal_reach_the_loop_gate():
    op, node = Identity.generate(), Identity.generate()
    j = Journal(identity=node, clock=_counter())
    seen = {}

    def run_task(task, *, should_stop, on_status, confirm, record_step=None, plan_gate=None):
        seen["click"] = plan_gate(_view("left_click", x=1, y=1), [])
        seen["type"] = plan_gate(_view("type", text="hi"), [])
        return (0, "ok")

    sup = Supervisor(j, run_task, issuers=Allowlist({op.did}))
    sup.add_grant(create_capability(op, node.did, ["physical.click"], ttl=3600))
    sup.add_goal("g1", title="g1")
    assert sup.run_goal("g1") == (0, "ok")
    assert seen["click"][0] is True
    assert seen["type"][0] is False


def test_untrusted_grant_gives_nothing():
    op, node, rogue = Identity.generate(), Identity.generate(), Identity.generate()
    sup = Supervisor(Journal(identity=node, clock=_counter()), lambda *a, **k: (0, "ok"),
                     issuers=Allowlist({op.did}))
    sup.add_grant(create_capability(rogue, node.did, ["physical.click"]))
    assert sup.node_scopes() == frozenset()


def test_without_issuers_no_gate_is_passed():
    # A run_task that doesn't know about the new hooks keeps working.
    def old_style(task, *, should_stop, on_status, confirm, record_step=None):
        return (0, "ok")

    sup = Supervisor(Journal(identity=Identity.generate(), clock=_counter()), old_style)
    sup.add_goal("g1", title="g1")
    assert sup.run_goal("g1") == (0, "ok")


def test_executing_crash_is_superseded_and_recovery_is_passed():
    j = Journal(identity=Identity.generate(), clock=_counter())
    got = {}

    def run_task(task, *, should_stop, on_status, confirm, record_step=None, recovery=None):
        got["recovery"] = recovery
        return (0, "ok")

    sup = Supervisor(j, run_task)
    sup.add_goal("g1", title="g1")
    # an earlier attempt crashed mid-execute and never finished
    old = sup.recorder.start_run("g1")
    sup.recorder.record_step(old, observation={"w": 1}, action={"kind": "click"},
                             result="", state=EXECUTING)

    sup.run_goal("g1")
    assert got["recovery"]["action"] == REOBSERVE_BEFORE_RETRY
    assert got["recovery"]["run_id"] == old

    store = StateStore()
    store.merge_events(j.events())
    assert store.get("run", old)["state"] == STOPPED  # closed as superseded
    assert sup.recover_runs() == []                    # nothing left in flight


def test_recovery_preamble_only_for_executing_crash():
    assert "already took effect" in recovery_preamble({"action": REOBSERVE_BEFORE_RETRY})
    assert recovery_preamble({"action": "resume_step"}) == ""


def test_end_to_end_grant_gates_the_real_agent_loop(monkeypatch):
    # Real chain: grant in the journal -> Supervisor -> plan gate -> the actual
    # agent loop -> run recorded. Skipped where the agent package isn't installed.
    loop = pytest.importorskip("secdogie_agent.loop")
    pytest.importorskip("PIL")
    from secdogie_agent import actions, screen
    from secdogie_agent.providers.base import Action, VisionProvider

    class Scripted(VisionProvider):
        def __init__(self, script):
            self.script = list(script)

        def next_action(self, task, screenshot_png, screen_size, history):
            return Action.from_dict(self.script.pop(0))

    executed = []
    monkeypatch.setattr(screen, "capture_screenshot", lambda region=None: (b"png", (100, 100)))
    monkeypatch.setattr(screen, "prepare_for_model", lambda raw, size, **kw: (raw, size, 1.0))
    monkeypatch.setattr(actions, "execute", lambda action, **kw: executed.append(action.kind) or "ok")
    monkeypatch.setattr(loop.time, "sleep", lambda s: None)

    op, node = Identity.generate(), Identity.generate()
    j = Journal(identity=node, clock=_counter())

    def run_task(task, *, should_stop, on_status, confirm, record_step=None, plan_gate=None):
        provider = Scripted([
            {"action": "left_click", "x": 1, "y": 1},
            {"action": "type", "text": "hi"},
            {"action": "done", "text": "ok"},
        ])
        cfg = loop.AgentConfig(
            task=task, auto=True, max_steps=10, should_stop=should_stop, plan_gate=plan_gate,
            trace_on_entry=lambda e: record_step(
                observation=e.frame_sha256, action=e.action, result=e.result),
        )
        return loop.run(provider, cfg), "done"

    sup = Supervisor(j, run_task, issuers=Allowlist({op.did}))
    sup.add_grant(create_capability(op, node.did, ["physical.click"], ttl=3600))
    sup.add_goal("g1", title="g1")
    assert sup.run_goal("g1")[0] == 0
    assert executed == ["left_click"]  # typing was not granted, so it never ran

    store = StateStore()
    store.merge_events(j.events())
    results = [s["result"] for s in store.entities("step").values()]
    assert any("refused by plan gate" in r for r in results)  # the refusal is on record


def test_dib_pid_reaches_the_task_only_when_set():
    got = {}

    def run_task(task, *, should_stop, on_status, confirm, record_step=None, dib_pid=None):
        got["dib_pid"] = dib_pid
        return (0, "ok")

    sup = Supervisor(Journal(identity=Identity.generate(), clock=_counter()), run_task, dib_pid=4242)
    sup.add_goal("g1", title="g1")
    sup.run_goal("g1")
    assert got["dib_pid"] == 4242

    def old_style(task, *, should_stop, on_status, confirm, record_step=None):
        return (0, "ok")  # does not accept dib_pid: must still work when unset

    sup2 = Supervisor(Journal(identity=Identity.generate(), clock=_counter()), old_style)
    sup2.add_goal("g1", title="g1")
    assert sup2.run_goal("g1") == (0, "ok")


def test_agent_run_task_is_structural(monkeypatch):
    # The project's execution path perceives through the accessibility tree and
    # never captures the screen.
    loop = pytest.importorskip("secdogie_agent.loop")
    from secdogie_agent import cli_common
    from secdogie_citadel.supervisor import agent_run_task

    seen = {}
    monkeypatch.setattr(cli_common, "resolve_provider", lambda args, name: object())
    monkeypatch.setattr(loop, "run", lambda provider, cfg: seen.setdefault("cfg", cfg) and 0)
    code, _ = agent_run_task("t", should_stop=lambda: False, on_status=lambda s: None,
                             confirm=lambda p, h: False)
    assert seen["cfg"].structural is True and seen["cfg"].desktop_ax is True
    assert seen["cfg"].confirm_high_risk is True
