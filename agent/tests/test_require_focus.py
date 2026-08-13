"""require_focus: abort rather than act on a bystander window."""
from secdogie_agent import actions, loop, screen
from secdogie_agent.providers.base import Action, VisionProvider


class ScriptedProvider(VisionProvider):
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def next_action(self, task, screenshot_png, screen_size, history):
        self.calls += 1
        return Action.from_dict(self.script.pop(0))


def _patch(monkeypatch, executed):
    monkeypatch.setattr(screen, "capture_screenshot", lambda region=None: (b"fake-png", (1920, 1080)))
    monkeypatch.setattr(screen, "prepare_for_model", lambda raw, size, **kw: (raw, size, 1.0))
    monkeypatch.setattr(actions, "execute", lambda action, **kw: executed.append(action.kind) or "ok")
    monkeypatch.setattr(loop.time, "sleep", lambda s: None)


def test_initial_focus_failure_aborts_when_require_focus(monkeypatch):
    executed = []
    _patch(monkeypatch, executed)
    provider = ScriptedProvider([{"action": "left_click", "x": 1, "y": 1}])
    config = loop.AgentConfig(
        task="x", auto=True, max_steps=5, require_focus=True, initial_focus=lambda: False
    )
    assert loop.run(provider, config) == 7
    assert executed == []
    assert provider.calls == 0


def test_initial_focus_failure_warns_without_require_focus(monkeypatch):
    executed = []
    _patch(monkeypatch, executed)
    provider = ScriptedProvider([{"action": "done", "text": "done"}])
    config = loop.AgentConfig(
        task="x", auto=True, max_steps=5, require_focus=False, initial_focus=lambda: False
    )
    assert loop.run(provider, config) == 0


def test_unconfirmed_action_focus_aborts_when_require_focus(monkeypatch):
    monkeypatch.setattr(screen, "prepare_for_model", lambda raw, size, **kw: (raw, size, 1.0))
    monkeypatch.setattr(loop.time, "sleep", lambda s: None)

    class Unfocused:
        def setup(self, logger):
            pass

        def capture(self, region):
            return b"fake-png", (800, 600)

        def execute(self, action):
            return "clicked left at (1, 1)" + actions.FOCUS_UNCONFIRMED_NOTE

    provider = ScriptedProvider([
        {"action": "left_click", "x": 1, "y": 1},
        {"action": "done", "text": "done"},
    ])
    config = loop.AgentConfig(
        task="x", auto=True, max_steps=5, verify_actions=False,
        require_focus=True, backend=Unfocused(),
    )
    assert loop.run(provider, config) == 7
    assert provider.calls == 1  # stopped after first unconfirmed action
