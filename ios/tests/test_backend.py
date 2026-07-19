import io

import pytest
from PIL import Image
from secdogie_agent import screen
from secdogie_agent.providers.base import Action
from secdogie_ios.backend import IosBackend
from secdogie_ios.wda import WdaError


class FakeWda:
    """Records WDA calls; capture returns a fixed pixel PNG + point window size."""

    def __init__(self, pixel=(1170, 2532), point=(390, 844), status_error=None, screenshot_error=None):
        self._pixel = pixel
        self._point = point
        self._status_error = status_error
        self._screenshot_error = screenshot_error
        self.calls = []

    def status(self):
        if self._status_error is not None:
            raise self._status_error
        return {"value": {"state": "success"}}

    def screenshot_png(self):
        if self._screenshot_error is not None:
            raise self._screenshot_error
        buf = io.BytesIO()
        Image.new("RGB", self._pixel, (0, 0, 0)).save(buf, format="PNG")
        return buf.getvalue()

    def window_size(self):
        return self._point

    def tap(self, x, y):
        self.calls.append(("tap", x, y))

    def double_tap(self, x, y):
        self.calls.append(("double_tap", x, y))

    def touch_and_hold(self, x, y, duration=1.0):
        self.calls.append(("touch_and_hold", x, y, duration))

    def drag(self, fx, fy, tx, ty, duration=0.5):
        self.calls.append(("drag", fx, fy, tx, ty))

    def type_text(self, text):
        self.calls.append(("type_text", text))

    def press_button(self, name):
        self.calls.append(("press_button", name))

    def homescreen(self):
        self.calls.append(("homescreen",))

    def open_url(self, url):
        self.calls.append(("open_url", url))


def _backend_after_capture(wda=None):
    """Return a backend that has captured once (so _px_per_pt is set)."""
    wda = wda or FakeWda()
    b = IosBackend(wda)
    b.capture(region=None)
    return b, wda


def _do(kind, **kw):
    b, wda = _backend_after_capture()
    b.execute(Action.from_dict({"action": kind, **kw}))
    return wda.calls


# -- capture ---------------------------------------------------------

def test_capture_returns_pixel_size_and_sets_retina_scale():
    b, wda = _backend_after_capture(FakeWda(pixel=(1170, 2532), point=(390, 844)))
    png, size = b.capture(region=None)
    assert size == (1170, 2532)  # honest pixel dimensions
    assert b._px_per_pt == pytest.approx(3.0)  # 1170 / 390


def test_capture_translates_wda_error_into_capture_error():
    b = IosBackend(FakeWda(screenshot_error=WdaError("device locked")))
    with pytest.raises(screen.CaptureError):
        b.capture(region=None)


# -- pixel -> point conversion (the crux) ---------------------------------------------------------

def test_tap_converts_pixels_to_points():
    # 3x device: a pixel-space tap at (300, 600) must become point (100, 200).
    calls = _do("left_click", x=300, y=600)
    assert calls == [("tap", 100, 200)]


def test_drag_converts_all_endpoints():
    calls = _do("drag", x=300, y=600, to_x=600, to_y=1200)
    assert calls == [("drag", 100, 200, 200, 400)]


# -- action mapping ---------------------------------------------------------

def test_double_click_double_taps():
    assert _do("double_click", x=300, y=300) == [("double_tap", 100, 100)]


# -- humanize_taps ---------------------------------------------------------

def test_humanize_off_by_default_uses_plain_tap():
    calls = _do("left_click", x=300, y=600)
    assert calls == [("tap", 100, 200)]


def test_humanize_on_uses_touch_and_hold_with_randomized_duration():
    import random

    wda = FakeWda()
    b = IosBackend(wda, humanize_taps=True, rng=random.Random(0))
    b.capture(region=None)
    b.execute(Action.from_dict({"action": "left_click", "x": 300, "y": 600}))
    assert len(wda.calls) == 1
    name, x, y, duration = wda.calls[0]
    assert name == "touch_and_hold" and (x, y) == (100, 200)
    from secdogie_ios.backend import _HUMANIZE_DURATION_S

    assert _HUMANIZE_DURATION_S[0] <= duration <= _HUMANIZE_DURATION_S[1]


def test_humanize_gives_different_durations_across_taps():
    import random

    wda = FakeWda()
    b = IosBackend(wda, humanize_taps=True, rng=random.Random(0))
    b.capture(region=None)
    for _ in range(5):
        b.execute(Action.from_dict({"action": "left_click", "x": 300, "y": 600}))
    durations = [c[3] for c in wda.calls]
    assert len(set(durations)) > 1  # not the same fixed duration every time


def test_humanize_does_not_affect_double_click():
    # doubleTap is WDA's own gesture; humanizing must not substitute two
    # touchAndHolds for it (that could fail to register as a double-tap).
    import random

    wda = FakeWda()
    b = IosBackend(wda, humanize_taps=True, rng=random.Random(0))
    b.capture(region=None)
    b.execute(Action.from_dict({"action": "double_click", "x": 300, "y": 300}))
    assert wda.calls == [("double_tap", 100, 100)]


def test_right_click_touch_and_holds():
    calls = _do("right_click", x=300, y=300)
    assert calls[0][0] == "touch_and_hold" and calls[0][1:3] == (100, 100)


def test_move_is_noop():
    b, wda = _backend_after_capture()
    result = b.execute(Action.from_dict({"action": "move", "x": 1, "y": 2}))
    assert wda.calls == []
    assert "no-op" in result


def test_type_sends_unicode_unchanged():
    # iOS/WDA can type Unicode, unlike adb -- it must not be skipped.
    assert _do("type", text="你好world") == [("type_text", "你好world")]


def test_key_enter_is_typed_as_newline():
    assert _do("key", keys=["enter"]) == [("type_text", "\n")]


def test_key_home_presses_hardware_button():
    assert _do("key", keys=["home"]) == [("press_button", "home")]


def test_key_volume_alias_maps_to_wda_name():
    assert _do("key", keys=["volup"]) == [("press_button", "volumeUp")]


def test_key_single_char_typed_verbatim():
    assert _do("key", keys=["a"]) == [("type_text", "a")]


def test_hold_key_presses_once_with_note():
    b, wda = _backend_after_capture()
    result = b.execute(Action.from_dict({"action": "hold_key", "keys": ["home"], "seconds": 3}))
    assert wda.calls == [("press_button", "home")]
    assert "not supported" in result


def test_open_uses_url():
    assert _do("open", path="https://x.com") == [("open_url", "https://x.com")]


def test_open_without_path_raises():
    b, _ = _backend_after_capture()
    with pytest.raises(ValueError):
        b.execute(Action.from_dict({"action": "open"}))


def test_scroll_down_swipes_up_in_points_and_clamps():
    # pixel (300, 900) -> point (100, 300); dy>0 swipes up by 300pt, clamped >=0.
    calls = _do("scroll", x=300, y=900, dy=3)
    assert calls == [("drag", 100, 300, 100, 0)]


def test_scroll_up_swipes_down():
    calls = _do("scroll", x=300, y=900, dy=-3)
    assert calls == [("drag", 100, 300, 100, 600)]


def test_unexecutable_kind_raises():
    b, _ = _backend_after_capture()
    with pytest.raises(ValueError):
        b.execute(Action.from_dict({"action": "wait", "seconds": 1}))


# -- setup ---------------------------------------------------------

class _RecordingLogger:
    def __init__(self):
        self.warnings = []

    def warning(self, msg, *args):
        self.warnings.append(msg % args if args else msg)


def test_setup_warns_when_wda_unreachable():
    log = _RecordingLogger()
    IosBackend(FakeWda(status_error=WdaError("connection refused"))).setup(log)
    assert any("not reachable" in w for w in log.warnings)


def test_setup_quiet_when_reachable():
    log = _RecordingLogger()
    IosBackend(FakeWda()).setup(log)
    assert log.warnings == []
