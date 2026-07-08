"""GUI/briefing flow tests. tkinter and a display aren't needed: the actual
window functions are monkeypatched, and we test the orchestration in the loop
plus the graceful-fallback logic."""
from secdogie_agent import actions, cli, dialog, loop, screen
from secdogie_agent.providers.base import Action, VisionProvider


class ScriptedProvider(VisionProvider):
    def __init__(self, script, plan=None):
        self.script = list(script)
        self.calls = 0
        self.plan = plan
        self.explain_calls = 0

    def next_action(self, task, screenshot_png, screen_size, history):
        self.calls += 1
        return Action.from_dict(self.script.pop(0))

    def explain_task(self, task, screenshot_png, screen_size):
        self.explain_calls += 1
        return self.plan


def _patch_io(monkeypatch, executed):
    monkeypatch.setattr(screen, "capture_screenshot", lambda region=None: (b"png", (1920, 1080)))
    monkeypatch.setattr(screen, "prepare_for_model", lambda raw, size, **kw: (raw, size, 1.0))
    monkeypatch.setattr(actions, "execute", lambda action, **kw: executed.append(action.kind) or "ok")


def test_briefing_cancel_stops_before_acting(monkeypatch):
    executed = []
    _patch_io(monkeypatch, executed)
    monkeypatch.setattr(dialog, "confirm_plan", lambda task, plan: False)
    provider = ScriptedProvider([{"action": "left_click", "x": 1, "y": 1}], plan="my plan")
    rc = loop.run(provider, loop.AgentConfig(task="do it", gui=True, auto=True, max_steps=5))
    assert rc == 2
    assert provider.explain_calls == 1
    assert provider.calls == 0  # never asked for an action
    assert executed == []


def test_briefing_proceed_runs_the_loop(monkeypatch):
    executed = []
    _patch_io(monkeypatch, executed)
    monkeypatch.setattr(dialog, "confirm_plan", lambda task, plan: True)
    provider = ScriptedProvider(
        [{"action": "left_click", "x": 1, "y": 1}, {"action": "done", "text": "ok"}],
        plan="my plan",
    )
    rc = loop.run(provider, loop.AgentConfig(task="do it", gui=True, auto=True, max_steps=5))
    assert rc == 0
    assert provider.explain_calls == 1
    assert executed == ["left_click"]


def test_briefing_skipped_when_provider_returns_no_plan(monkeypatch):
    executed = []
    _patch_io(monkeypatch, executed)
    # confirm_plan must NOT be called if there's no plan to show.
    def boom(*a, **k):
        raise AssertionError("confirm_plan should not be called without a plan")

    monkeypatch.setattr(dialog, "confirm_plan", boom)
    provider = ScriptedProvider([{"action": "done", "text": "ok"}], plan=None)
    rc = loop.run(provider, loop.AgentConfig(task="do it", gui=True, auto=True, max_steps=5))
    assert rc == 0


def test_briefing_failure_does_not_block_run(monkeypatch):
    executed = []
    _patch_io(monkeypatch, executed)

    class FlakyProvider(ScriptedProvider):
        def explain_task(self, task, screenshot_png, screen_size):
            raise RuntimeError("api down")

    monkeypatch.setattr(dialog, "confirm_plan", lambda t, p: (_ for _ in ()).throw(AssertionError()))
    provider = FlakyProvider([{"action": "done", "text": "ok"}])
    rc = loop.run(provider, loop.AgentConfig(task="do it", gui=True, auto=True, max_steps=5))
    assert rc == 0  # briefing error is swallowed, run proceeds


def test_gui_ask_user_uses_dialog(monkeypatch):
    executed = []
    _patch_io(monkeypatch, executed)
    monkeypatch.setattr(dialog, "confirm_plan", lambda t, p: True)
    monkeypatch.setattr(dialog, "ask_user", lambda q: False)  # user says no in the popup
    provider = ScriptedProvider([{"action": "ask_user", "text": "ok?"}], plan="p")
    rc = loop.run(provider, loop.AgentConfig(task="risky", gui=True, auto=True, max_steps=5))
    assert rc == 2


def test_cli_falls_back_to_terminal_when_gui_unavailable(monkeypatch, capsys):
    monkeypatch.setattr(dialog, "gui_available", lambda: False)
    # No task + no GUI -> argparse error (SystemExit), proving GUI task-entry was skipped.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    try:
        cli.main(["--gui"])
    except SystemExit as e:
        assert e.code != 0
    err = capsys.readouterr().err
    assert "falling back to the terminal" in err


def test_gui_available_returns_bool():
    # Whatever the environment, this must not raise and must return a bool.
    assert isinstance(dialog.gui_available(), bool)
