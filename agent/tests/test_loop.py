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


def test_look_with_point_sends_a_fovea_and_translates_coords(monkeypatch):
    executed = []
    prepares = []

    monkeypatch.setattr(screen, "capture_screenshot", lambda region=None: (b"fake-png", (1920, 1080)))
    monkeypatch.setattr(
        screen, "prepare_for_model",
        lambda raw, size, **kw: prepares.append(("full", size)) or (raw, size, 1.0),
    )

    def fake_fovea(png, size, cx, cy, **kw):
        prepares.append(("fovea", (cx, cy)))
        return screen.Fovea(
            image=b"crop",
            model_size=(768, 768),
            scale=1.0,
            origin=(100, 200),
            size=(768, 768),
            anchor=(cx, cy),
        )

    monkeypatch.setattr(screen, "prepare_fovea", fake_fovea)
    monkeypatch.setattr(actions, "execute", lambda action, **kw: executed.append(action) or "ok")

    provider = ScriptedProvider([
        {"action": "look", "x": 400, "y": 500},
        {"action": "left_click", "x": 50, "y": 60},
        {"action": "done", "text": "ok"},
    ])
    config = loop.AgentConfig(task="t", auto=True, max_steps=10, verify_actions=False)
    rc = loop.run(provider, config)
    assert rc == 0
    assert prepares[0][0] == "full"
    assert prepares[1] == ("fovea", (400, 500))
    click = executed[0]
    assert (click.x, click.y) == (150, 260)  # crop-local + origin


def test_no_change_click_foveates_the_next_frame(monkeypatch):
    executed = []
    prepares = []

    monkeypatch.setattr(screen, "capture_screenshot", lambda region=None: (b"fake-png", (1920, 1080)))
    monkeypatch.setattr(
        screen, "prepare_for_model",
        lambda raw, size, **kw: prepares.append("full") or (raw, size, 1.0),
    )
    monkeypatch.setattr(
        screen, "prepare_fovea",
        lambda png, size, cx, cy, **kw: prepares.append(("fovea", cx, cy)) or screen.Fovea(
            image=b"crop", model_size=(768, 768), scale=1.0,
            origin=(0, 0), size=(768, 768), anchor=(cx, cy),
        ),
    )
    monkeypatch.setattr(screen, "changed_ratio", lambda *a, **k: 0.0)
    monkeypatch.setattr(actions, "execute", lambda action, **kw: executed.append(action.kind) or "ok")

    provider = ScriptedProvider([
        {"action": "left_click", "x": 300, "y": 400},
        {"action": "left_click", "x": 10, "y": 10},
        {"action": "done", "text": "ok"},
    ])
    config = loop.AgentConfig(
        task="t", auto=True, max_steps=10, verify_actions=True, action_retries=0,
    )
    rc = loop.run(provider, config)
    assert rc == 0
    assert prepares[0] == "full"
    assert prepares[1] == ("fovea", 300, 400)
    assert executed == ["left_click", "left_click"]


def test_fovea_edge_zero_falls_back_to_whole_frame_boost(monkeypatch):
    prepares = []
    monkeypatch.setattr(screen, "capture_screenshot", lambda region=None: (b"fake-png", (1920, 1080)))

    def fake_prepare(raw, size, **kw):
        prepares.append(kw.get("max_edge"))
        return raw, size, 1.0

    monkeypatch.setattr(screen, "prepare_for_model", fake_prepare)

    def _no_fovea(*a, **k):
        raise AssertionError("fovea must stay off when fovea_edge=0")

    monkeypatch.setattr(screen, "prepare_fovea", _no_fovea)
    monkeypatch.setattr(actions, "execute", lambda action, **kw: "ok")

    provider = ScriptedProvider([
        {"action": "look", "x": 10, "y": 10},
        {"action": "done", "text": "ok"},
    ])
    config = loop.AgentConfig(
        task="t", auto=True, max_steps=5, verify_actions=False,
        fovea_edge=0, max_image_edge=1536,
    )
    rc = loop.run(provider, config)
    assert rc == 0
    # First frame is a normal prepare; second is a whole-frame boost (1.25x, capped).
    assert prepares[0] == 1536
    assert prepares[1] == max(1536, min(1920, int(1536 * 1.25)))
