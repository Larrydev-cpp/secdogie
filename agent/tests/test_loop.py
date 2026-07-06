from secdogie_agent import actions, loop, screen
from secdogie_agent.providers.base import Action, HistoryStep, VisionProvider


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


def _patch_screen_and_actions(monkeypatch, executed):
    monkeypatch.setattr(screen, "capture_screenshot", lambda: (b"fake-png", (1920, 1080)))
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
    def raise_no_display():
        raise screen.NoDisplayError("no display")

    monkeypatch.setattr(screen, "capture_screenshot", raise_no_display)
    provider = ScriptedProvider([{"action": "done", "text": "unreached"}])
    config = loop.AgentConfig(task="anything", auto=True, max_steps=5)
    rc = loop.run(provider, config)
    assert rc == 4
    assert provider.calls == 0  # never even reached the model


def test_loop_scales_model_coordinates_to_screen(monkeypatch):
    executed = []
    monkeypatch.setattr(screen, "capture_screenshot", lambda: (b"fake-png", (1920, 1080)))
    # Model saw a half-size image, so real coords are 2x what it returned.
    monkeypatch.setattr(screen, "prepare_for_model", lambda raw, size, **kw: (raw, (960, 540), 2.0))
    monkeypatch.setattr(actions, "execute", lambda action, **kw: executed.append(action) or "ok")
    provider = ScriptedProvider([
        {"action": "left_click", "x": 100, "y": 50},
        {"action": "done", "text": "done"},
    ])
    rc = loop.run(provider, loop.AgentConfig(task="click", auto=True, max_steps=5))
    assert rc == 0
    assert (executed[0].x, executed[0].y) == (200, 100)  # scaled 2x
    assert executed[0].raw["x"] == 200  # raw updated too, for logs/confirmation


def test_benign_wait_needs_no_confirmation(monkeypatch):
    # Even without --auto, a benign `wait` should execute without prompting.
    executed = []
    _patch_screen_and_actions(monkeypatch, executed)
    provider = ScriptedProvider([
        {"action": "wait", "seconds": 0},
        {"action": "done", "text": "done"},
    ])
    config = loop.AgentConfig(task="idle", auto=False, max_steps=10)
    rc = loop.run(provider, config)  # no input() mock -> would hang if it prompted
    assert rc == 0
    assert executed == ["wait"]


def test_watch_mode_waits_until_trigger_then_acts(monkeypatch):
    executed = []
    _patch_screen_and_actions(monkeypatch, executed)
    provider = ScriptedProvider([
        {"action": "wait", "reasoning": "trigger not seen"},
        {"action": "wait", "reasoning": "still not seen"},
        {"action": "open", "path": "/tmp/log.txt", "reasoning": "condition met"},
        {"action": "done", "text": "handled"},
    ])
    config = loop.AgentConfig(task="when X appears open the log", watch=True,
                              watch_interval=0, auto=True, max_steps=20)
    rc = loop.run(provider, config)
    assert rc == 0
    # waits are the "keep watching" signal and are not executed; only the
    # triggered open runs.
    assert executed == ["open"]
    assert provider.calls == 4


def test_watch_wait_not_confirmed_even_without_auto(monkeypatch):
    executed = []
    _patch_screen_and_actions(monkeypatch, executed)
    provider = ScriptedProvider([
        {"action": "wait", "reasoning": "watching"},
        {"action": "done", "text": "stop"},
    ])
    config = loop.AgentConfig(task="watch something", watch=True, watch_interval=0,
                              auto=False, max_steps=5)
    rc = loop.run(provider, config)  # no input() mock; must not prompt on the wait
    assert rc == 0
    assert executed == []


def test_loop_confirmation_required_without_auto(monkeypatch):
    executed = []
    _patch_screen_and_actions(monkeypatch, executed)
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    provider = ScriptedProvider([
        {"action": "left_click", "x": 1, "y": 1},
        {"action": "done", "text": "done"},
    ])
    config = loop.AgentConfig(task="click something", auto=False, max_steps=10)
    rc = loop.run(provider, config)
    assert rc == 0
    assert executed == []  # declined every confirmation
