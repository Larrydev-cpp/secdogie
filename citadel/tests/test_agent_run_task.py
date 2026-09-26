"""agent_run_task is the production path from a supervised Citadel node into the
real agent loop. The model provider and the loop itself are stood in for (no
desktop, no API key), so these check headlessly that the node's human-oversight
and recording hooks reach the loop's config intact."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("secdogie_agent.loop")

from secdogie_agent import cli_common, loop  # noqa: E402
from secdogie_citadel.recovery import REOBSERVE_BEFORE_RETRY  # noqa: E402
from secdogie_citadel.supervisor import agent_run_task  # noqa: E402

TASK = "tidy the desktop"


@pytest.fixture
def captured(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(cli_common, "resolve_provider", lambda args, prog: "fake-provider")

    def fake_run(provider, config):
        seen["provider"] = provider
        seen["config"] = config
        return 0

    monkeypatch.setattr(loop, "run", fake_run)
    return seen


def _run(captured, **kwargs):
    calls: list = []
    statuses: list = []

    def confirm(prompt, high_risk):
        calls.append((prompt, high_risk))
        return True

    code, summary = agent_run_task(
        TASK, should_stop=lambda: False, on_status=statuses.append, confirm=confirm, **kwargs
    )
    return code, summary, captured["config"], calls, statuses


def test_drives_the_loop_with_the_goal_as_its_task(captured):
    code, summary, cfg, _, _ = _run(captured)
    assert code == 0 and summary == "agent exited 0"
    assert captured["provider"] == "fake-provider"
    assert cfg.task == TASK


def test_no_api_key_is_a_clean_failure(monkeypatch):
    monkeypatch.setattr(cli_common, "resolve_provider", lambda args, prog: None)
    code, summary = agent_run_task(
        TASK, should_stop=lambda: False, on_status=lambda s: None, confirm=lambda p, h: True
    )
    assert code == 1 and "no API key" in summary


def test_step_approval_reaches_confirm_with_its_risk_flag(captured):
    _, _, cfg, calls, _ = _run(captured)
    assert cfg.approve_action("Execute HIGH-RISK open(report.pdf)?", True) is True
    assert cfg.approve_action("Execute left_click(10, 20)?", False) is True
    assert calls == [("Execute HIGH-RISK open(report.pdf)?", True), ("Execute left_click(10, 20)?", False)]


def test_questions_to_the_operator_are_treated_as_high_risk(captured):
    _, _, cfg, calls, _ = _run(captured)
    cfg.ask_operator("which folder should old files go to?")
    assert calls == [("which folder should old files go to?", True)]


def test_plan_approval_shows_the_plan_not_the_task(captured):
    _, _, cfg, calls, _ = _run(captured)
    cfg.approve_plan(TASK, "1. open the file manager\n2. sort by date")  # loop calls approver(task, plan)
    assert calls == [("approve plan: 1. open the file manager\n2. sort by date", False)]


def test_stop_status_and_recording_hooks_are_wired(captured):
    recorded: list = []
    gate = object()
    _, _, cfg, _, statuses = _run(captured, record_step=lambda **kw: recorded.append(kw), plan_gate=gate)
    assert cfg.should_stop() is False
    cfg.on_event("step", {"n": 1})
    assert statuses == ["step: {'n': 1}"]
    cfg.trace_on_entry(SimpleNamespace(frame_sha256="f00d", action="left_click", result="ok"))
    assert recorded == [{"observation": "f00d", "action": "left_click", "result": "ok"}]
    assert cfg.plan_gate is gate


def test_recovery_puts_a_check_before_redoing_note_in_front_of_the_task(captured):
    _, _, cfg, _, _ = _run(captured, recovery={"action": REOBSERVE_BEFORE_RETRY})
    assert cfg.task.endswith(TASK) and "already took effect" in cfg.task
    _, _, cfg, _, _ = _run(captured, recovery={"action": "resume_step"})
    assert cfg.task == TASK
