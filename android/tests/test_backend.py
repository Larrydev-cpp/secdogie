import io

import pytest
from PIL import Image

from secdogie_agent import screen
from secdogie_agent.backend import ElementSelector, Locatable
from secdogie_agent.providers.base import Action

from secdogie_android.adb import AdbError
from secdogie_android.backend import AdbBackend


class FakeAdb:
    """Records calls instead of shelling out to adb."""

    def __init__(self, serial=None, png=None, screencap_error=None, devices=None, ui_xml=None, ui_error=None):
        self.serial = serial
        self._png = png
        self._screencap_error = screencap_error
        self._devices = devices if devices is not None else ["DEV1"]
        self._ui_xml = ui_xml
        self._ui_error = ui_error
        self.calls = []

    def list_devices(self):
        return self._devices

    def ui_dump(self):
        if self._ui_error is not None:
            raise self._ui_error
        return self._ui_xml

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


# -- RPA-style element snapping ---------------------------------------------------------

# A full-screen clickable backdrop plus a small Submit button inside it.
_SNAP_XML = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy rotation="0">
  <node class="root" clickable="true" text="" resource-id="app:id/root" content-desc=""
        bounds="[0,0][1080,2400]"/>
  <node class="a" clickable="true" text="Submit" resource-id="app:id/submit" content-desc=""
        bounds="[400,300][680,420]"/>
</hierarchy>"""


def _captured_backend(**kw):
    """A snapping backend that has captured once, so its screen-size area guard
    is active (1080x2400)."""
    adb = FakeAdb(png=_png(1080, 2400), ui_xml=_SNAP_XML, **kw)
    b = AdbBackend(adb, snap_to_elements=True)
    b.capture(region=None)
    return b, adb


def test_snap_off_by_default_uses_raw_coordinate():
    adb = FakeAdb(png=_png(1080, 2400), ui_xml=_SNAP_XML)
    b = AdbBackend(adb)  # snapping not enabled
    b.capture(region=None)
    b.execute(Action.from_dict({"action": "left_click", "x": 410, "y": 305}))
    assert adb.calls == [("tap", 410, 305)]


def test_snap_moves_tap_to_widget_center():
    # A corner click inside the Submit button snaps to its center; the button is
    # control-sized so the area guard allows it.
    b, adb = _captured_backend()
    result = b.execute(Action.from_dict({"action": "left_click", "x": 410, "y": 305}))
    assert adb.calls == [("tap", 540, 360)]  # Submit button center
    assert "snapped to 'Submit'" in result


def test_snap_declines_for_large_container():
    # A point that only the full-screen backdrop contains -> don't snap onto it.
    b, adb = _captured_backend()
    b.execute(Action.from_dict({"action": "left_click", "x": 50, "y": 1500}))
    assert adb.calls == [("tap", 50, 1500)]


def test_snap_applies_to_double_and_long_press():
    b, adb = _captured_backend()
    b.execute(Action.from_dict({"action": "double_click", "x": 410, "y": 305}))
    b.execute(Action.from_dict({"action": "right_click", "x": 410, "y": 305}))
    assert adb.calls == [("tap", 540, 360), ("tap", 540, 360), ("long_press", 540, 360)]


def test_snap_falls_back_to_raw_on_dump_error():
    adb = FakeAdb(png=_png(1080, 2400), ui_error=AdbError("secure view blocks dump"))
    b = AdbBackend(adb, snap_to_elements=True)
    b.capture(region=None)
    b.execute(Action.from_dict({"action": "left_click", "x": 410, "y": 305}))
    assert adb.calls == [("tap", 410, 305)]


def test_find_element_returns_match():
    adb = FakeAdb(ui_xml=_SNAP_XML)
    b = AdbBackend(adb, snap_to_elements=True)
    el = b.find_element(text="Submit")
    assert el is not None and el.resource_id == "app:id/submit"
    assert b.find_element(text="nope") is None


# -- Locatable: macro.py record/replay by element identity ---------------------------------------------------------

def test_adb_backend_satisfies_locatable_protocol():
    assert isinstance(AdbBackend(FakeAdb()), Locatable)


def test_describe_target_returns_selector_for_clickable_element():
    adb = FakeAdb(ui_xml=_SNAP_XML)
    b = AdbBackend(adb)
    sel = b.describe_target(410, 305)  # inside the Submit button
    assert sel == ElementSelector(
        kind="android-uiautomator",
        attrs={"resource_id": "app:id/submit", "text": "Submit", "cls": "a"},
    )


def test_describe_target_returns_none_when_nothing_clickable_at_point():
    adb = FakeAdb(ui_xml=_SNAP_XML)
    b = AdbBackend(adb)
    assert b.describe_target(2000, 5000) is None  # outside even the full-screen backdrop's bounds


def test_describe_target_returns_none_on_dump_error():
    adb = FakeAdb(ui_error=AdbError("secure view blocks dump"))
    b = AdbBackend(adb)
    assert b.describe_target(410, 305) is None


def test_locate_finds_element_center_by_selector():
    adb = FakeAdb(ui_xml=_SNAP_XML)
    b = AdbBackend(adb)
    sel = ElementSelector(kind="android-uiautomator", attrs={"resource_id": "app:id/submit"})
    assert b.locate(sel) == (540, 360)  # Submit button center


def test_locate_returns_none_when_selector_no_longer_matches():
    adb = FakeAdb(ui_xml=_SNAP_XML)
    b = AdbBackend(adb)
    sel = ElementSelector(kind="android-uiautomator", attrs={"resource_id": "app:id/gone"})
    assert b.locate(sel) is None


def test_locate_returns_none_for_selector_kind_from_another_backend():
    adb = FakeAdb(ui_xml=_SNAP_XML)
    b = AdbBackend(adb)
    sel = ElementSelector(kind="ios-wda", attrs={"resource_id": "app:id/submit"})
    assert b.locate(sel) is None


def test_locate_returns_none_on_dump_error():
    adb = FakeAdb(ui_error=AdbError("secure view blocks dump"))
    b = AdbBackend(adb)
    sel = ElementSelector(kind="android-uiautomator", attrs={"resource_id": "app:id/submit"})
    assert b.locate(sel) is None


def test_describe_then_locate_round_trip_finds_same_element():
    # The core record -> replay contract: whatever describe_target names,
    # locate must be able to find again from the same hierarchy.
    adb = FakeAdb(ui_xml=_SNAP_XML)
    b = AdbBackend(adb)
    sel = b.describe_target(410, 305)
    assert b.locate(sel) == (540, 360)


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
