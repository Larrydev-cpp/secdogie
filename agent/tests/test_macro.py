import json

import pytest

from secdogie_agent.backend import ElementSelector
from secdogie_agent.macro import Macro, MacroRecorder, MacroStep, resolve_replay_step
from secdogie_agent.providers.base import Action


class FakeLocatableBackend:
    """A Backend that implements Locatable (describe_target/locate)."""

    def __init__(self, describe_result=None, locate_result=None):
        self._describe_result = describe_result
        self._locate_result = locate_result
        self.describe_calls = []
        self.locate_calls = []

    def setup(self, logger):
        pass

    def capture(self, region):
        return b"", (1000, 500)

    def execute(self, action):
        return "ok"

    def describe_target(self, x, y):
        self.describe_calls.append((x, y))
        return self._describe_result

    def locate(self, selector):
        self.locate_calls.append(selector)
        return self._locate_result


class FakePlainBackend:
    """A Backend that does NOT implement Locatable."""

    def setup(self, logger):
        pass

    def capture(self, region):
        return b"", (1000, 500)

    def execute(self, action):
        return "ok"


# -- Macro save/load round-trip ---------------------------------------------------------

def test_macro_save_load_round_trip(tmp_path):
    m = Macro(task="click the button", created_at=123.5)
    m.steps.append(
        MacroStep(
            kind="left_click",
            selector=ElementSelector(kind="android-uiautomator", attrs={"resource_id": "btn"}),
            recorded_result="tapped",
        )
    )
    m.steps.append(MacroStep(kind="type", fields={"text": "hello"}, recorded_result="typed 5 character(s)"))
    m.steps.append(MacroStep(kind="drag", point=(0.1, 0.2), to_point=(0.8, 0.9)))

    path = tmp_path / "m.json"
    m.save(path)
    loaded = Macro.load(path)

    assert loaded.task == "click the button"
    assert loaded.created_at == 123.5
    assert len(loaded.steps) == 3
    assert loaded.steps[0].selector == ElementSelector(kind="android-uiautomator", attrs={"resource_id": "btn"})
    assert loaded.steps[0].recorded_result == "tapped"
    assert loaded.steps[1].fields == {"text": "hello"}
    assert loaded.steps[2].point == (0.1, 0.2)
    assert loaded.steps[2].to_point == (0.8, 0.9)


def test_macro_load_missing_file_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        Macro.load(tmp_path / "nope.json")


def test_macro_load_wrong_format_version_raises_value_error(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"format_version": 999, "task": "x", "created_at": 0, "steps": []}))
    with pytest.raises(ValueError, match="format_version"):
        Macro.load(path)


def test_macro_load_malformed_json_raises_value_error(tmp_path):
    path = tmp_path / "m.json"
    path.write_text("{not valid json")
    with pytest.raises(ValueError):
        Macro.load(path)


def test_macro_save_is_human_readable_json(tmp_path):
    m = Macro(task="t")
    path = tmp_path / "m.json"
    m.save(path)
    text = path.read_text()
    assert '"task": "t"' in text
    assert "\n" in text  # indented, not minified


# -- MacroRecorder.record() ---------------------------------------------------------

def test_record_uses_selector_when_backend_is_locatable():
    backend = FakeLocatableBackend(describe_result=ElementSelector(kind="k", attrs={"a": "1"}))
    rec = MacroRecorder("task")
    action = Action.from_dict({"action": "left_click", "x": 100, "y": 200})
    rec.record(action, "tapped", backend, (1000, 500))

    step = rec.steps[0]
    assert step.selector == ElementSelector(kind="k", attrs={"a": "1"})
    assert step.point is None
    assert backend.describe_calls == [(100, 200)]


def test_record_falls_back_to_normalized_point_when_not_locatable():
    backend = FakePlainBackend()
    rec = MacroRecorder("task")
    action = Action.from_dict({"action": "left_click", "x": 250, "y": 100})
    rec.record(action, "tapped", backend, (1000, 500))

    step = rec.steps[0]
    assert step.selector is None
    assert step.point == pytest.approx((0.25, 0.2))


def test_record_falls_back_to_point_when_describe_target_returns_none():
    backend = FakeLocatableBackend(describe_result=None)
    rec = MacroRecorder("task")
    action = Action.from_dict({"action": "left_click", "x": 500, "y": 250})
    rec.record(action, "tapped", backend, (1000, 500))

    step = rec.steps[0]
    assert step.selector is None
    assert step.point == pytest.approx((0.5, 0.5))


def test_record_drag_gets_normalized_to_point():
    backend = FakePlainBackend()
    rec = MacroRecorder("task")
    action = Action.from_dict({"action": "drag", "x": 100, "y": 100, "to_x": 900, "to_y": 400})
    rec.record(action, "dragged", backend, (1000, 500))

    step = rec.steps[0]
    assert step.to_point == pytest.approx((0.9, 0.8))


def test_record_non_positional_action_has_no_selector_or_point():
    backend = FakeLocatableBackend()
    rec = MacroRecorder("task")
    action = Action.from_dict({"action": "type", "text": "hello"})
    rec.record(action, "typed 5 character(s)", backend, (1000, 500))

    step = rec.steps[0]
    assert step.selector is None
    assert step.point is None
    assert step.fields == {"text": "hello"}
    assert backend.describe_calls == []  # never even tried -- no position to describe


def test_record_captures_only_non_none_fields():
    backend = FakePlainBackend()
    rec = MacroRecorder("task")
    action = Action.from_dict({"action": "key", "keys": ["enter"]})
    rec.record(action, "sent", backend, (1000, 500))
    assert rec.steps[0].fields == {"keys": ["enter"]}


def test_record_step_appends_verbatim():
    rec = MacroRecorder("task")
    step = MacroStep(kind="left_click", point=(0.5, 0.5))
    rec.record_step(step)
    assert rec.steps == [step]


def test_build_returns_macro_with_task_and_steps():
    rec = MacroRecorder("my task")
    rec.record_step(MacroStep(kind="wait"))
    macro = rec.build()
    assert macro.task == "my task"
    assert len(macro.steps) == 1
    assert macro.created_at > 0


# -- resolve_replay_step ---------------------------------------------------------

def test_resolve_with_selector_success():
    backend = FakeLocatableBackend(locate_result=(321, 654))
    step = MacroStep(kind="left_click", selector=ElementSelector(kind="k", attrs={"a": "1"}))
    action = resolve_replay_step(step, backend, (1000, 500))
    assert action is not None
    assert (action.x, action.y) == (321, 654)
    assert action.kind == "left_click"


def test_resolve_with_selector_not_found_returns_none():
    backend = FakeLocatableBackend(locate_result=None)
    step = MacroStep(kind="left_click", selector=ElementSelector(kind="k", attrs={"a": "1"}))
    assert resolve_replay_step(step, backend, (1000, 500)) is None


def test_resolve_with_selector_on_non_locatable_backend_returns_none():
    backend = FakePlainBackend()
    step = MacroStep(kind="left_click", selector=ElementSelector(kind="k", attrs={"a": "1"}))
    assert resolve_replay_step(step, backend, (1000, 500)) is None


def test_resolve_with_point_denormalizes_against_current_screen_size():
    backend = FakePlainBackend()
    step = MacroStep(kind="left_click", point=(0.25, 0.5))
    action = resolve_replay_step(step, backend, (800, 400))
    assert (action.x, action.y) == (200, 200)


def test_resolve_with_to_point_for_drag():
    backend = FakePlainBackend()
    step = MacroStep(kind="drag", point=(0.0, 0.0), to_point=(1.0, 1.0))
    action = resolve_replay_step(step, backend, (1000, 500))
    assert (action.x, action.y) == (0, 0)
    assert (action.to_x, action.to_y) == (1000, 500)


def test_resolve_non_positional_step_carries_fields_with_no_position():
    backend = FakePlainBackend()
    step = MacroStep(kind="type", fields={"text": "hi"})
    action = resolve_replay_step(step, backend, (1000, 500))
    assert action.text == "hi"
    assert action.x is None and action.y is None


def test_resolve_raw_reflects_resolved_coordinates_for_confirmation_prompts():
    backend = FakePlainBackend()
    step = MacroStep(kind="left_click", point=(0.5, 0.5))
    action = resolve_replay_step(step, backend, (800, 400))
    assert action.raw == {"action": "left_click", "x": 400, "y": 200}
