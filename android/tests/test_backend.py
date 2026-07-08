import io

import pytest
from PIL import Image

from secdogie_agent import screen
from secdogie_agent.providers.base import Action

from secdogie_android.adb import AdbError
from secdogie_android.backend import AdbBackend


class FakeAdb:
    """Records calls instead of shelling out to adb."""

    def __init__(self, serial=None, png=None, screencap_error=None, devices=None):
        self.serial = serial
        self._png = png
        self._screencap_error = screencap_error
        self._devices = devices if devices is not None else ["DEV1"]
        self.calls = []

    def list_devices(self):
        return self._devices

    def screencap_png(self):
        if self._screencap_error is not None:
            raise self._screencap_error
        return self._png

    def tap(self, x, y):
        self.calls.append(("tap", x, y))

    def swipe(self, x1, y1, x2, y2, duration_ms=200):
        self.calls.append(("swipe", x1, y1, x2, y2, duration_ms))

    def long_press(self, x, y, duration_ms=600):
        self.calls.append(("long_press", x, y))

    def text(self, s):
        self.calls.append(("text", s))

    def keyevent(self, key, longpress=False):
        self.calls.append(("keyevent", key, longpress))

    def open_uri(self, uri):
        self.calls.append(("open_uri", uri))


def _png(w, h):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (0, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


def _do(kind, **kw):
    adb = FakeAdb()
    AdbBackend(adb).execute(Action.from_dict({"action": kind, **kw}))
    return adb.calls


# -- capture ---------------------------------------------------------

def test_capture_returns_png_and_real_device_size():
    adb = FakeAdb(png=_png(1080, 2400))
    png, size = AdbBackend(adb).capture(region=None)
    assert size == (1080, 2400)  # taken from the screencap PNG, i.e. true device pixels
    assert png[:4] == b"\x89PNG"


def test_capture_translates_adb_error_into_capture_error():
    adb = FakeAdb(screencap_error=AdbError("device offline"))
    with pytest.raises(screen.CaptureError):
        AdbBackend(adb).capture(region=None)


# -- action mapping ---------------------------------------------------------

def test_left_click_taps():
    assert _do("left_click", x=100, y=200) == [("tap", 100, 200)]


def test_double_click_taps_twice():
    assert _do("double_click", x=5, y=6) == [("tap", 5, 6), ("tap", 5, 6)]


def test_right_click_long_presses():
    assert _do("right_click", x=7, y=8) == [("long_press", 7, 8)]


def test_move_is_a_noop_with_message():
    adb = FakeAdb()
    result = AdbBackend(adb).execute(Action.from_dict({"action": "move", "x": 1, "y": 2}))
    assert adb.calls == []
    assert "no-op" in result


def test_drag_swipes():
    calls = _do("drag", x=1, y=2, to_x=3, to_y=4)
    assert calls[0][0] == "swipe" and calls[0][1:5] == (1, 2, 3, 4)


def test_type_ascii_sends_text():
    assert _do("type", text="hello") == [("text", "hello")]


def test_type_non_ascii_is_skipped_not_sent():
    adb = FakeAdb()
    result = AdbBackend(adb).execute(Action.from_dict({"action": "type", "text": "你好"}))
    assert adb.calls == []  # must not send garbage over `input text`
    assert "non-ASCII" in result


def test_key_sends_each_keyevent():
    assert _do("key", keys=["enter"]) == [("keyevent", "enter", False)]
    assert _do("key", keys=["a", "b"]) == [("keyevent", "a", False), ("keyevent", "b", False)]


def test_hold_key_longpresses():
    assert _do("hold_key", keys=["back"], seconds=2) == [("keyevent", "back", True)]


def test_open_uses_view_intent():
    assert _do("open", path="https://x.com") == [("open_uri", "https://x.com")]


def test_open_without_path_raises():
    with pytest.raises(ValueError):
        AdbBackend(FakeAdb()).execute(Action.from_dict({"action": "open"}))


def test_scroll_down_swipes_up_and_clamps_to_top():
    # dy > 0 (reveal content below) -> finger swipes upward; endpoint clamps at 0.
    calls = _do("scroll", x=500, y=400, dy=3)
    assert calls == [("swipe", 500, 400, 500, 0, 200)]


def test_scroll_up_swipes_down():
    calls = _do("scroll", x=500, y=400, dy=-3)
    assert calls == [("swipe", 500, 400, 500, 1000, 200)]


def test_scroll_horizontal_swipes_sideways():
    calls = _do("scroll", x=500, y=400, dx=2)
    assert calls == [("swipe", 500, 400, 0, 400, 200)]


def test_unexecutable_kind_raises():
    with pytest.raises(ValueError):
        AdbBackend(FakeAdb()).execute(Action.from_dict({"action": "wait", "seconds": 1}))


# -- setup ---------------------------------------------------------

class _RecordingLogger:
    def __init__(self):
        self.warnings = []

    def warning(self, msg, *args):
        self.warnings.append(msg % args if args else msg)


def test_setup_warns_when_no_device():
    log = _RecordingLogger()
    AdbBackend(FakeAdb(devices=[])).setup(log)
    assert any("no adb device" in w for w in log.warnings)


def test_setup_warns_on_multiple_devices_without_serial():
    log = _RecordingLogger()
    AdbBackend(FakeAdb(serial=None, devices=["A", "B"])).setup(log)
    assert any("--device" in w for w in log.warnings)


def test_setup_quiet_when_one_device():
    log = _RecordingLogger()
    AdbBackend(FakeAdb(devices=["ONLY"])).setup(log)
    assert log.warnings == []
