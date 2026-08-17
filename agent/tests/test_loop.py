import logging

import pytest
from secdogie_agent import actions, axtree, loop, screen
from secdogie_agent.backend import DesktopBackend, ElementSelector
from secdogie_agent.macro import Macro, MacroStep
from secdogie_agent.providers.base import Action, VisionProvider


class ScriptedProvider(VisionProvider):
    """Replays a fixed list of actions, ignoring the screenshot/history --
    stands in for a real vision LLM in tests."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def next_action(self, task, screenshot_png, screen_size, history):
        self.calls += 1
        d = self.script.pop(0)
        return Action.from_dict(d)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    # The loop's action_pause defaults to 0.15s; never actually sleep in tests.
    # Tests that assert on the pause re-patch this to record the durations.
    monkeypatch.setattr(loop.time, "sleep", lambda s: None)


def _patch_screen_and_actions(monkeypatch, executed):
    monkeypatch.setattr(screen, "capture_screenshot", lambda region=None: (b"fake-png", (1920, 1080)))
    # Bypass real image handling; pass the capture through with scale 1.0.
    monkeypatch.setattr(screen, "prepare_for_model", lambda raw, size, **kw: (raw, size, 1.0))
    monkeypatch.setattr(actions, "execute", lambda action, **kw: executed.append(action.kind) or "ok")


def test_loop_stops_on_done(monkeypatch):
    executed = []
    _patch_screen_and_actions(monkeypatch, executed)
    provider = ScriptedProvider([
        {"action": "left_click", "x": 1, "y": 1},
        {"action": "done", "text": "all set"},
    ])
    config = loop.AgentConfig(task="click something", auto=True, max_steps=10)
    rc = loop.run(provider, config)
    assert rc == 0
    assert executed == ["left_click"]
    assert provider.calls == 2


def test_loop_respects_max_steps(monkeypatch):
    executed = []
    _patch_screen_and_actions(monkeypatch, executed)
    provider = ScriptedProvider([{"action": "wait", "seconds": 0}] * 5)
    config = loop.AgentConfig(task="wait forever", auto=True, max_steps=3)
    rc = loop.run(provider, config)
    assert rc == 3
    assert provider.calls == 3
    assert executed == ["wait", "wait", "wait"]


def test_loop_dry_run_never_executes(monkeypatch):
    executed = []
    _patch_screen_and_actions(monkeypatch, executed)
    provider = ScriptedProvider([
        {"action": "left_click", "x": 1, "y": 1},
        {"action": "done", "text": "done"},
    ])
    config = loop.AgentConfig(task="click something", dry_run=True, max_steps=10)
    rc = loop.run(provider, config)
    assert rc == 0
    assert executed == []  # actions.execute must never be called in dry-run


def test_loop_ask_user_declined_stops_run(monkeypatch):
    executed = []
    _patch_screen_and_actions(monkeypatch, executed)
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    provider = ScriptedProvider([
        {"action": "ask_user", "text": "ok to proceed?"},
        {"action": "left_click", "x": 1, "y": 1},
    ])
    config = loop.AgentConfig(task="do something risky", auto=True, max_steps=10)
    rc = loop.run(provider, config)
    assert rc == 2
    assert executed == []
    assert provider.calls == 1


def test_loop_reports_no_display_cleanly(monkeypatch):
    def raise_no_display(region=None):
        raise screen.NoDisplayError("no display")

    monkeypatch.setattr(screen, "capture_screenshot", raise_no_display)
    provider = ScriptedProvider([{"action": "done", "text": "unreached"}])
    config = loop.AgentConfig(task="anything", auto=True, max_steps=5)
    rc = loop.run(provider, config)
    assert rc == 4
    assert provider.calls == 0  # never even reached the model


def test_read_only_blocks_mutating(monkeypatch):
    executed = []
    _patch_screen_and_actions(monkeypatch, executed)
    provider = ScriptedProvider([
        {"action": "type", "text": "hello"},
        {"action": "left_click", "x": 1, "y": 1},
        {"action": "done", "text": "done"},
    ])
    config = loop.AgentConfig(task="type then click", auto=True, max_steps=10, read_only=True)
    rc = loop.run(provider, config)
    assert rc == 0
    assert executed == ["left_click"]  # type blocked, click allowed


def test_high_risk_open_confirms_even_under_auto(monkeypatch):
    executed = []
    _patch_screen_and_actions(monkeypatch, executed)
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "n")
    provider = ScriptedProvider([
        {"action": "open", "path": "/etc/hosts"},
        {"action": "done", "text": "done"},
    ])
    rc = loop.run(provider, loop.AgentConfig(task="open a file", auto=True, max_steps=5))
    assert rc == 0
    assert executed == []                      # declined -> not launched
    assert prompts and "HIGH-RISK open" in prompts[0]


def test_high_risk_open_runs_when_confirmed_under_auto(monkeypatch):
    executed = []
    _patch_screen_and_actions(monkeypatch, executed)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    provider = ScriptedProvider([
        {"action": "open", "path": "/etc/hosts"},
        {"action": "done", "text": "done"},
    ])
    rc = loop.run(provider, loop.AgentConfig(task="open a file", auto=True, max_steps=5))
    assert rc == 0
    assert executed == ["open"]


def test_low_risk_click_never_prompts_under_auto(monkeypatch):
    executed = []
    _patch_screen_and_actions(monkeypatch, executed)

    def _no_input(prompt):
        raise AssertionError("a low-risk action must not confirm under --auto")

    monkeypatch.setattr("builtins.input", _no_input)
    provider = ScriptedProvider([
        {"action": "left_click", "x": 1, "y": 1},
        {"action": "done", "text": "done"},
    ])
    rc = loop.run(provider, loop.AgentConfig(task="click", auto=True, max_steps=5))
    assert rc == 0
    assert executed == ["left_click"]


def test_require_focus_aborts_on_initial_failure(monkeypatch):
    executed = []
    _patch_screen_and_actions(monkeypatch, executed)
    provider = ScriptedProvider([{"action": "done", "text": "done"}])
    config = loop.AgentConfig(
        task="x", auto=True, max_steps=5,
        initial_focus=lambda: False, require_focus=True,
    )
    rc = loop.run(provider, config)
    assert rc == 7
    assert executed == []


def test_require_focus_false_continues_on_failure(monkeypatch):
    executed = []
    _patch_screen_and_actions(monkeypatch, executed)
    provider = ScriptedProvider([
        {"action": "left_click", "x": 1, "y": 1},
        {"action": "done", "text": "done"},
    ])
    config = loop.AgentConfig(
        task="x", auto=True, max_steps=5,
        initial_focus=lambda: False, require_focus=False,
    )
    rc = loop.run(provider, config)
    assert rc == 0
    assert executed == ["left_click"]
