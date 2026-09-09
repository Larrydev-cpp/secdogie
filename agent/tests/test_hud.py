"""Operator HUD / bridge: persistent console so a windowed exe is never a
silent background process after Start. Tk is not required — the queue
protocol is what the loop and CLI attach to."""
from __future__ import annotations

import threading
import time

from secdogie_agent import actions, cli, dialog, hud, loop, screen
from secdogie_agent.providers.base import Action, VisionProvider


class ScriptedProvider(VisionProvider):
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def next_action(self, task, screenshot_png, screen_size, history):
        self.calls += 1
        return Action.from_dict(self.script.pop(0))


def _patch_io(monkeypatch, executed):
    monkeypatch.setattr(screen, "capture_screenshot", lambda region=None: (b"png", (1920, 1080)))
    monkeypatch.setattr(screen, "prepare_for_model", lambda raw, size, **kw: (raw, size, 1.0))
    monkeypatch.setattr(actions, "execute", lambda action, **kw: executed.append(action.kind) or "ok")
    monkeypatch.setattr(loop.time, "sleep", lambda s: None)


def test_bridge_stop_is_visible_to_should_stop():
    b = hud.OperatorBridge()
    assert b.should_stop() is False
    b.request_stop()
    assert b.should_stop() is True
    kind, _ = b.events.get_nowait()
    assert kind == "stop"


def test_bridge_confirm_from_worker_thread():
    b = hud.OperatorBridge()
    result: list[bool] = []

    def worker():
        result.append(b.confirm("go?", high_risk=True, timeout=2))

    t = threading.Thread(target=worker)
    t.start()
    kind, payload = b.events.get(timeout=1)
    assert kind == "confirm"
    assert payload["high_risk"] is True
    assert "go?" in payload["prompt"]
    b.answer(payload, True)
    t.join(timeout=1)
    assert result == [True]


def test_bridge_confirm_fail_closed_on_stop():
    b = hud.OperatorBridge()
    result: list[bool] = []

    def worker():
        result.append(b.confirm("open /etc/hosts", high_risk=True, timeout=2))

    t = threading.Thread(target=worker)
    t.start()
    b.events.get(timeout=1)
    b.request_stop()
    t.join(timeout=2)
    assert result == [False]


def test_loop_emits_capture_model_action_done(monkeypatch):
    executed = []
    _patch_io(monkeypatch, executed)
    events: list[tuple] = []

    def on_event(kind, payload):
        events.append((kind, payload))

    provider = ScriptedProvider(
        [{"action": "left_click", "x": 1, "y": 1}, {"action": "done", "text": "ok"}]
    )
    rc = loop.run(
        provider,
        loop.AgentConfig(task="click", auto=True, max_steps=5, on_event=on_event),
    )
    assert rc == 0
    kinds = [k for k, _ in events]
    assert "start" in kinds
    assert "capture" in kinds
    assert "model" in kinds
    assert "action" in kinds
    assert "result" in kinds
    assert "done" in kinds
    assert "finished" in kinds
    assert events[-1][1]["rc"] == 0
    assert executed == ["left_click"]


def test_hud_skip_plan_dialog_when_on_event(monkeypatch):
    """Start is the approval. A second plan window was a gap with no UI."""
    executed = []
    _patch_io(monkeypatch, executed)
    events = []

    def boom(*a, **k):
        raise AssertionError("confirm_plan must not run when the HUD is attached")

    monkeypatch.setattr(dialog, "confirm_plan", boom)
    provider = ScriptedProvider(
        [{"action": "left_click", "x": 1, "y": 1}, {"action": "done", "text": "ok"}]
    )
    rc = loop.run(
        provider,
        loop.AgentConfig(
            task="do it",
            gui=True,
            auto=False,
            max_steps=5,
            on_event=lambda k, p: events.append(k),
        ),
    )
    assert rc == 0
    assert "briefing" in events
    assert executed == ["left_click"]


def test_hud_high_risk_goes_through_approve_action(monkeypatch):
    executed = []
    _patch_io(monkeypatch, executed)
    prompts = []

    def approve(prompt, high_risk=False):
        prompts.append((prompt, high_risk))
        return True

    provider = ScriptedProvider(
        [{"action": "open", "path": "/tmp/x"}, {"action": "done", "text": "ok"}]
    )
    rc = loop.run(
        provider,
        loop.AgentConfig(
            task="open it",
            auto=True,
            max_steps=5,
            approve_action=approve,
        ),
    )
    assert rc == 0
    assert executed == ["open"]
    assert prompts and prompts[0][1] is True


def test_stop_from_bridge_ends_loop(monkeypatch):
    executed = []
    _patch_io(monkeypatch, executed)
    b = hud.OperatorBridge()
    calls = {"n": 0}

    class SlowProvider(ScriptedProvider):
        def next_action(self, task, screenshot_png, screen_size, history):
            calls["n"] += 1
            if calls["n"] == 1:
                b.request_stop()
            return super().next_action(task, screenshot_png, screen_size, history)

    provider = SlowProvider([{"action": "wait"}, {"action": "left_click", "x": 1, "y": 1}])
    rc = loop.run(
        provider,
        loop.AgentConfig(
            task="x",
            auto=True,
            max_steps=8,
            should_stop=b.should_stop,
            on_event=b.emit,
        ),
    )
    assert rc == 5
    assert "left_click" not in executed


def test_cli_gui_uses_hud_session(monkeypatch):
    monkeypatch.setattr(dialog, "gui_available", lambda: True)
    from secdogie_agent import config as config_mod

    monkeypatch.setattr(config_mod, "has_configured_api_key", lambda: True)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr("secdogie_agent.cli_common.resolve_provider", lambda *a, **k: object())
    seen = {}

    def fake_session(provider, config):
        seen["hud"] = True
        seen["task"] = config.task
        seen["gui"] = config.gui
        return 0

    monkeypatch.setattr(cli, "run_gui_session", fake_session)
    rc = cli.main(["--gui", "zoom the drawing"])
    assert rc == 0
    assert seen.get("hud") is True
    assert seen.get("gui") is True
    assert seen.get("task") == "zoom the drawing"


def test_attach_wires_should_stop_and_confirm():
    b = hud.OperatorBridge()
    console = hud.OperatorHud.__new__(hud.OperatorHud)
    console.task = "t"
    console.bridge = b
    console._root = None
    cfg = loop.AgentConfig(task="t")
    console.attach(cfg)
    assert cfg.on_event is not None
    assert cfg.on_event.__self__ is b
    assert callable(cfg.approve_action)
    assert callable(cfg.approve_plan)
    assert callable(cfg.ask_operator)
    assert cfg.should_stop is not None
    b.request_stop()
    assert cfg.should_stop() is True


def test_run_worker_headless_runs_inline():
    console = hud.OperatorHud.__new__(hud.OperatorHud)
    console.task = "t"
    console.bridge = hud.OperatorBridge()
    console._root = None
    console._closed = False
    assert console.run_worker(lambda: 0) == 0
    # finished was emitted by the worker wrapper
    kinds = []
    while True:
        try:
            k, _ = console.bridge.events.get_nowait()
            kinds.append(k)
        except Exception:
            break
    assert "finished" in kinds
