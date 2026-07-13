import io
import json

import pytest
from secdogie_agent.backend import ElementSelector
from secdogie_agent.macro import Macro, MacroRecorder, MacroStep, VisualAnchor, resolve_replay_step
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


# -- visual anchoring (re-find a clicked element by image, not a fixed coord) ---

def _scene(px, py, w=300, h=200, patch=32):
    """A grayscale PNG: flat background with a distinctive textured square whose
    top-left is (px, py) -- so its click center is (px + patch//2, py + patch//2).
    The texture gives the patch real variance, so NCC has a sharp peak."""
    np = pytest.importorskip("numpy")
    from PIL import Image

    yy, xx = np.mgrid[0:patch, 0:patch]
    tile = ((xx * 8 + yy * 5) % 256).astype(np.uint8)
    arr = np.full((h, w), 40, np.uint8)
    arr[py:py + patch, px:px + patch] = tile
    buf = io.BytesIO()
    Image.fromarray(arr, "L").save(buf, format="PNG")
    return buf.getvalue()


def _blank(w=300, h=200):
    np = pytest.importorskip("numpy")
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(np.full((h, w), 40, np.uint8), "L").save(buf, format="PNG")
    return buf.getvalue()


def _record_click(px, py, frame):
    """Record a left_click on the element centered in `frame`'s patch."""
    rec = MacroRecorder("t")
    cx, cy = px + 16, py + 16
    action = Action(kind="left_click", x=cx, y=cy, raw={"action": "left_click", "x": cx, "y": cy})
    rec.record(action, "clicked", FakePlainBackend(), (300, 200), frame_png=frame)
    return rec.steps[0]


def test_record_captures_a_visual_anchor_for_a_non_locatable_backend():
    step = _record_click(100, 60, _scene(100, 60))
    assert step.selector is None
    assert step.anchor is not None and step.anchor.png       # captured the element's image
    assert step.anchor.offset == (32, 32)                    # click at the center of the 64px anchor box
    assert step.point is not None                            # and kept the coordinate as a fallback


def test_resolve_by_anchor_finds_a_moved_element():
    # Recorded when the element was at (100,60); on replay it's at (180,120).
    step = _record_click(100, 60, _scene(100, 60))
    resolved = resolve_replay_step(step, FakePlainBackend(), (300, 200), frame_png=_scene(180, 120))
    assert resolved is not None
    # Re-found at the MOVED center (196,136), not the stale recorded coord (116,76).
    assert abs(resolved.x - 196) <= 1 and abs(resolved.y - 136) <= 1


def test_resolve_anchor_miss_falls_back_to_the_coordinate():
    step = _record_click(100, 60, _scene(100, 60))
    # The element is gone from the frame -> NCC finds nothing -> use the point.
    resolved = resolve_replay_step(step, FakePlainBackend(), (300, 200), frame_png=_blank())
    assert resolved is not None and (resolved.x, resolved.y) == (116, 76)


def test_resolve_without_a_frame_uses_the_coordinate():
    # No frame to match against (e.g. caller didn't pass one) -> coordinate path.
    step = MacroStep(kind="left_click", anchor=VisualAnchor(png=b"x", offset=(1, 1)), point=(0.5, 0.5))
    action = resolve_replay_step(step, FakePlainBackend(), (800, 400))
    assert action is not None and (action.x, action.y) == (400, 200)


def test_resolve_anchor_only_step_is_unresolved_when_the_element_is_gone():
    # Anchor present, no coordinate fallback, element not on screen -> None, so
    # the loop hands off to the live model rather than clicking a guess.
    step = _record_click(100, 60, _scene(100, 60))
    no_coord = MacroStep(kind="left_click", anchor=step.anchor)  # drop the point
    assert resolve_replay_step(no_coord, FakePlainBackend(), (300, 200), frame_png=_blank()) is None


def test_macro_round_trips_a_visual_anchor(tmp_path):
    anchor = VisualAnchor(png=b"\x89PNG\r\n\x1a\n\x00\xff raw bytes", offset=(7, 9))
    m = Macro(task="t", steps=[MacroStep(kind="left_click", anchor=anchor, point=(0.2, 0.3))])
    path = tmp_path / "m.json"
    m.save(path)
    loaded = Macro.load(path).steps[0]
    assert loaded.anchor is not None
    assert loaded.anchor.png == anchor.png            # base64 codec is lossless
    assert loaded.anchor.offset == (7, 9)
    assert loaded.point == (0.2, 0.3)


# -- desktop accessibility tier: selector wins over the visual anchor ----------

def test_desktop_a11y_selector_is_recorded_and_replayed_in_preference_to_an_anchor():
    from secdogie_agent import axtree
    from secdogie_agent.backend import DesktopBackend

    class Prov:
        def __init__(self, els):
            self.els = els

        def snapshot(self):
            return self.els

    tree = [axtree.AxElement(role="Button", name="Save", automation_id="saveBtn", bounds=(100, 100, 200, 140))]
    backend = DesktopBackend(ax_provider=Prov(tree))

    rec = MacroRecorder("t")
    action = Action(kind="left_click", x=150, y=120, raw={"action": "left_click", "x": 150, "y": 120})
    rec.record(action, "clicked", backend, (800, 600), frame_png=b"ignored-when-a-selector-is-found")
    step = rec.steps[0]
    # The strongest tier won: a semantic selector, and NO visual anchor / coordinate.
    assert step.selector is not None and step.selector.kind == "desktop-ax"
    assert step.anchor is None and step.point is None

    # Replay after the button moved: the selector re-finds it by identity.
    backend.ax_provider = Prov(
        [axtree.AxElement(role="Button", name="Save", automation_id="saveBtn", bounds=(500, 300, 600, 340))]
    )
    resolved = resolve_replay_step(step, backend, (800, 600))
    assert resolved is not None and (resolved.x, resolved.y) == (550, 320)
