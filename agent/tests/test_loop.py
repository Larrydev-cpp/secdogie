import pytest
from secdogie_agent import actions, loop, screen
from secdogie_agent.backend import ElementSelector
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
    # The loop's action_pause defaults to 0.4s; never actually sleep in tests.
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


def test_loop_scales_model_coordinates_to_screen(monkeypatch):
    executed = []
    monkeypatch.setattr(screen, "capture_screenshot", lambda region=None: (b"fake-png", (1920, 1080)))
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


def test_loop_region_is_passed_to_capture_and_offsets_actions(monkeypatch):
    executed = []
    captured_regions = []

    def fake_capture(region=None):
        captured_regions.append(region)
        return b"fake-png", (400, 300)

    monkeypatch.setattr(screen, "capture_screenshot", fake_capture)
    monkeypatch.setattr(screen, "prepare_for_model", lambda raw, size, **kw: (raw, size, 1.0))
    monkeypatch.setattr(actions, "execute", lambda action, **kw: executed.append(action) or "ok")
    provider = ScriptedProvider([
        {"action": "left_click", "x": 10, "y": 20},
        {"action": "done", "text": "done"},
    ])
    region = (100, 200, 400, 300)
    config = loop.AgentConfig(task="click in a window", auto=True, max_steps=5, region=region)
    rc = loop.run(provider, config)
    assert rc == 0
    assert captured_regions == [region, region]  # one capture per step (left_click, then done)
    # model coordinates are region-relative; the region's (left, top) must be
    # added back before the click is executed against the real, full screen.
    assert (executed[0].x, executed[0].y) == (110, 220)


def test_loop_logger_name_isolates_concurrent_runs(monkeypatch):
    _patch_screen_and_actions(monkeypatch, [])
    provider_a = ScriptedProvider([{"action": "done", "text": "a"}])
    provider_b = ScriptedProvider([{"action": "done", "text": "b"}])
    loop.run(provider_a, loop.AgentConfig(task="a", auto=True, max_steps=1, logger_name="test.a"))
    loop.run(provider_b, loop.AgentConfig(task="b", auto=True, max_steps=1, logger_name="test.b"))
    import logging

    assert logging.getLogger("test.a") is not logging.getLogger("test.b")
    assert logging.getLogger("test.a").handlers
    assert logging.getLogger("test.b").handlers


def test_loop_uses_injected_backend_instead_of_desktop(monkeypatch):
    # A custom backend fully replaces mss/pyautogui: the loop must route
    # capture/execute through it and never touch the desktop screen module.
    monkeypatch.setattr(screen, "prepare_for_model", lambda raw, size, **kw: (raw, size, 1.0))

    class FakeBackend:
        def __init__(self):
            self.setup_called = False
            self.executed = []

        def setup(self, logger):
            self.setup_called = True

        def capture(self, region):
            return b"device-png", (720, 1600)

        def execute(self, action):
            self.executed.append(action.kind)
            return "tapped"

    backend = FakeBackend()
    provider = ScriptedProvider([
        {"action": "left_click", "x": 10, "y": 20},
        {"action": "done", "text": "done"},
    ])
    rc = loop.run(provider, loop.AgentConfig(task="tap", auto=True, max_steps=5, backend=backend))
    assert rc == 0
    assert backend.setup_called
    assert backend.executed == ["left_click"]


def test_loop_should_stop_halts_before_next_step(monkeypatch):
    executed = []
    _patch_screen_and_actions(monkeypatch, executed)
    provider = ScriptedProvider([{"action": "wait", "seconds": 0}] * 10)
    config = loop.AgentConfig(
        task="wait forever", auto=True, max_steps=10, should_stop=lambda: provider.calls >= 2
    )
    rc = loop.run(provider, config)
    assert rc == 5
    assert provider.calls == 2  # stopped before a 3rd step was requested


# -- RPA macro replay/record integration ---------------------------------------------------------

class FakeReplayBackend:
    """Non-Locatable backend for macro tests: replay steps with a selector
    can never resolve against it, so it exercises the live-fallback path."""

    def __init__(self, size=(1000, 500)):
        self.size = size
        self.executed = []

    def setup(self, logger):
        pass

    def capture(self, region):
        return b"device-png", self.size

    def execute(self, action):
        self.executed.append(action)
        return "ok"


def test_macro_replay_skips_model_calls_for_resolved_steps(monkeypatch, tmp_path):
    monkeypatch.setattr(screen, "prepare_for_model", lambda raw, size, **kw: (raw, size, 1.0))
    macro_path = tmp_path / "macro.json"
    macro = Macro(task="click then type")
    macro.steps.append(MacroStep(kind="left_click", point=(0.5, 0.5)))
    macro.steps.append(MacroStep(kind="type", fields={"text": "hi"}))
    macro.save(macro_path)

    backend = FakeReplayBackend(size=(1000, 500))
    # Only the final `done` should ever reach the model -- both macro steps
    # must resolve from the replay path without a model call.
    provider = ScriptedProvider([{"action": "done", "text": "done"}])
    config = loop.AgentConfig(
        task="click then type", auto=True, max_steps=10, backend=backend, macro_path=str(macro_path)
    )
    rc = loop.run(provider, config)

    assert rc == 0
    assert provider.calls == 1
    assert [a.kind for a in backend.executed] == ["left_click", "type"]
    assert (backend.executed[0].x, backend.executed[0].y) == (500, 250)  # denormalized against real size
    assert backend.executed[1].text == "hi"


def test_macro_replay_falls_back_to_live_model_mid_run(monkeypatch, tmp_path):
    monkeypatch.setattr(screen, "prepare_for_model", lambda raw, size, **kw: (raw, size, 1.0))
    macro_path = tmp_path / "macro.json"
    macro = Macro(task="click twice")
    macro.steps.append(MacroStep(kind="left_click", point=(0.1, 0.1)))  # resolves fine
    macro.steps.append(
        MacroStep(kind="left_click", selector=ElementSelector(kind="k", attrs={"a": "1"}))
    )  # backend isn't Locatable -- can never resolve
    macro.save(macro_path)

    backend = FakeReplayBackend(size=(1000, 500))
    provider = ScriptedProvider([
        {"action": "left_click", "x": 5, "y": 5},
        {"action": "done", "text": "done"},
    ])
    config = loop.AgentConfig(
        task="click twice", auto=True, max_steps=10, backend=backend, macro_path=str(macro_path)
    )
    rc = loop.run(provider, config)

    assert rc == 0
    # First step replayed (no model call); second step's selector can't
    # resolve, so it and everything after fall back to the live model.
    assert provider.calls == 2
    assert [a.kind for a in backend.executed] == ["left_click", "left_click"]
    assert (backend.executed[0].x, backend.executed[0].y) == (100, 50)  # from replay
    assert (backend.executed[1].x, backend.executed[1].y) == (5, 5)  # from the live model


def test_loop_records_and_saves_macro_on_success(monkeypatch, tmp_path):
    monkeypatch.setattr(screen, "prepare_for_model", lambda raw, size, **kw: (raw, size, 1.0))
    macro_path = tmp_path / "macro.json"
    assert not macro_path.exists()

    backend = FakeReplayBackend(size=(1000, 500))
    provider = ScriptedProvider([
        {"action": "left_click", "x": 100, "y": 50},
        {"action": "done", "text": "done"},
    ])
    config = loop.AgentConfig(
        task="click something", auto=True, max_steps=10, backend=backend, macro_path=str(macro_path)
    )
    rc = loop.run(provider, config)

    assert rc == 0
    assert macro_path.exists()
    saved = Macro.load(macro_path)
    assert len(saved.steps) == 1
    assert saved.steps[0].kind == "left_click"
    assert saved.steps[0].point == pytest.approx((0.1, 0.1))


# -- action pause + stall guard (latency / stuck-loop hardening) --------------

def test_action_pause_waits_after_a_real_action(monkeypatch):
    executed = []
    _patch_screen_and_actions(monkeypatch, executed)
    slept = []
    monkeypatch.setattr(loop.time, "sleep", lambda s: slept.append(s))
    provider = ScriptedProvider([
        {"action": "left_click", "x": 1, "y": 1},
        {"action": "done", "text": "done"},
    ])
    rc = loop.run(provider, loop.AgentConfig(task="t", auto=True, max_steps=5, action_pause=0.5, stall_limit=0))
    assert rc == 0
    assert slept == [0.5]  # paused once, after the click; not after done


def test_action_pause_skipped_for_benign_wait(monkeypatch):
    executed = []
    _patch_screen_and_actions(monkeypatch, executed)
    slept = []
    monkeypatch.setattr(loop.time, "sleep", lambda s: slept.append(s))
    provider = ScriptedProvider([
        {"action": "wait", "seconds": 0},
        {"action": "done", "text": "done"},
    ])
    rc = loop.run(provider, loop.AgentConfig(task="t", auto=True, max_steps=5, action_pause=0.5, stall_limit=0))
    assert rc == 0
    assert slept == []  # a benign wait doesn't get the post-action pause


def test_stall_guard_stops_when_action_repeats_on_unchanged_screen(monkeypatch):
    executed = []
    _patch_screen_and_actions(monkeypatch, executed)  # capture returns constant bytes -> screen never changes
    provider = ScriptedProvider([{"action": "left_click", "x": 5, "y": 5}] * 20)
    rc = loop.run(provider, loop.AgentConfig(task="t", auto=True, max_steps=20, stall_limit=3, action_pause=0))
    assert rc == 6  # stalled
    assert provider.calls == 4  # one baseline + three no-change repeats


def test_stall_guard_does_not_fire_when_screen_changes(monkeypatch):
    # A repeated action that DOES change the screen each step is progress, not a
    # stall (e.g. pressing Down to move a selection).
    frames = iter(range(1000))
    monkeypatch.setattr(screen, "capture_screenshot",
                        lambda region=None: (f"frame-{next(frames)}".encode(), (1920, 1080)))
    monkeypatch.setattr(screen, "prepare_for_model", lambda raw, size, **kw: (raw, size, 1.0))
    monkeypatch.setattr(actions, "execute", lambda action, **kw: "ok")
    monkeypatch.setattr(loop.time, "sleep", lambda s: None)
    provider = ScriptedProvider([{"action": "key", "keys": ["down"]}] * 10)
    rc = loop.run(provider, loop.AgentConfig(task="t", auto=True, max_steps=5, stall_limit=3, action_pause=0))
    assert rc == 3  # ran to max_steps, never tripped the stall guard
    assert provider.calls == 5


def test_stall_guard_disabled_when_limit_zero(monkeypatch):
    executed = []
    _patch_screen_and_actions(monkeypatch, executed)
    provider = ScriptedProvider([{"action": "left_click", "x": 5, "y": 5}] * 10)
    rc = loop.run(provider, loop.AgentConfig(task="t", auto=True, max_steps=4, stall_limit=0, action_pause=0))
    assert rc == 3  # no stall detection; runs out its steps instead
    assert provider.calls == 4


def test_loop_watch_mode_ignores_macro_path(monkeypatch, tmp_path):
    # Watch mode is exempted from macro replay/recording entirely -- a
    # variable-length trigger doesn't fit a fixed recorded sequence.
    macro_path = tmp_path / "macro.json"
    macro = Macro(task="anything")
    macro.steps.append(MacroStep(kind="left_click", point=(0.5, 0.5)))
    macro.save(macro_path)

    executed = []
    _patch_screen_and_actions(monkeypatch, executed)
    provider = ScriptedProvider([
        {"action": "left_click", "x": 1, "y": 1},
        {"action": "done", "text": "done"},
    ])
    config = loop.AgentConfig(
        task="anything", watch=True, watch_interval=0, auto=True, max_steps=10, macro_path=str(macro_path)
    )
    rc = loop.run(provider, config)

    assert rc == 0
    assert provider.calls == 2  # never consulted the macro
    assert executed == ["left_click"]
    # The pre-existing macro file must be left untouched -- watch mode never
    # loads or overwrites it.
    assert Macro.load(macro_path).steps[0].kind == "left_click"
