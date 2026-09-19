"""Unit tests for the macOS window-capture selection + TCC handling.

The pure selection logic and the capture flow run everywhere via an injected
fake Quartz; the real Quartz calls are validated on a Mac.
"""
from __future__ import annotations

import sys

import pytest
from secdogie_agent import mac_capture
from secdogie_agent.screen import CaptureError


def _win(wid, *, pid=1, layer=0, title="", w=800, h=600, alpha=1.0):
    return {
        "id": wid, "pid": pid, "layer": layer, "title": title, "owner": "App",
        "alpha": alpha, "bounds": {"X": 0, "Y": 0, "Width": w, "Height": h},
    }


def test_choose_frontmost_normal_window():
    wins = [_win(10, title="front"), _win(11, title="behind")]
    assert mac_capture.choose_window(wins)["id"] == 10  # list is front-to-back


def test_choose_skips_non_normal_and_degenerate():
    wins = [
        _win(1, layer=25, title="menubar"),   # non-zero layer (chrome)
        _win(2, alpha=0.0, title="hidden"),    # invisible
        _win(3, w=0, h=0, title="zero"),       # degenerate size
        _win(4, title="real"),
    ]
    assert mac_capture.choose_window(wins)["id"] == 4


def test_choose_filters_by_pid():
    wins = [_win(1, pid=100), _win(2, pid=200)]
    assert mac_capture.choose_window(wins, pid=200)["id"] == 2
    assert mac_capture.choose_window(wins, pid=999) is None


def test_choose_prefers_title_match_then_frontmost():
    wins = [_win(1, title="Untitled"), _win(2, title="Drawing.dwg"), _win(3, title="Drawing backup")]
    assert mac_capture.choose_window(wins, title="drawing")["id"] == 2


def test_choose_none_when_empty():
    assert mac_capture.choose_window([]) is None


def test_titles_readable_hint():
    assert mac_capture.titles_readable([_win(1, title="X")])
    assert not mac_capture.titles_readable([_win(1, title=""), _win(2, layer=3, title="chrome")])


_FAKE_IMAGE = object()  # a non-null CGImage stand-in


class _FakeQuartz:
    """Minimal Quartz stand-in for the capture flow."""

    CGRectNull = object()
    kCGWindowListOptionOnScreenOnly = 1
    kCGWindowListExcludeDesktopElements = 16
    kCGWindowListOptionIncludingWindow = 8
    kCGWindowImageBoundsIgnoreFraming = 1
    kCGNullWindowID = 0

    def __init__(self, windows, *, image=_FAKE_IMAGE, width=800, height=600):
        self._windows = windows
        self._image = image
        self._w, self._h = width, height

    def CGWindowListCopyWindowInfo(self, opts, rel):
        return self._windows

    def CGWindowListCreateImage(self, rect, opt, wid, flags):
        return self._image

    def CGImageGetWidth(self, img):
        return self._w

    def CGImageGetHeight(self, img):
        return self._h


def _raw(wid, pid=1, layer=0, title="", w=800, h=600):
    return {
        "kCGWindowNumber": wid, "kCGWindowOwnerPID": pid, "kCGWindowLayer": layer,
        "kCGWindowName": title, "kCGWindowOwnerName": "App", "kCGWindowAlpha": 1.0,
        "kCGWindowBounds": {"X": 0, "Y": 0, "Width": w, "Height": h},
    }


def test_list_windows_normalizes_cg_keys():
    q = _FakeQuartz([_raw(42, pid=7, title="Doc")])
    wins = mac_capture.list_windows(quartz=q)
    assert wins[0]["id"] == 42 and wins[0]["pid"] == 7 and wins[0]["title"] == "Doc"
    assert wins[0]["bounds"]["Width"] == 800


def test_capture_target_window_encodes_and_sizes():
    q = _FakeQuartz([_raw(42, title="Doc")], width=640, height=480)
    calls = {}

    def fake_encode(img, w, h, quartz):
        calls["size"] = (w, h)
        return b"PNGBYTES"

    png, size = mac_capture.capture_target_window_png(quartz=q, encode_png=fake_encode)
    assert png == b"PNGBYTES"
    assert size == (640, 480) == calls["size"]


def test_capture_returns_none_when_no_window():
    q = _FakeQuartz([_raw(1, layer=25)])  # only chrome; nothing selectable
    assert mac_capture.capture_target_window_png(quartz=q, encode_png=lambda *a: b"x") is None


def test_screen_recording_denied_on_null_image():
    q = _FakeQuartz([_raw(1, title="Doc")], image=None)  # TCC denial -> null image
    with pytest.raises(mac_capture.ScreenRecordingDenied) as exc:
        mac_capture.capture_target_window_png(quartz=q)
    assert isinstance(exc.value, CaptureError)  # loop treats it as a clean capture failure
    assert "Screen Recording" in str(exc.value)


def test_screen_recording_denied_on_zero_size():
    q = _FakeQuartz([_raw(1, title="Doc")], width=0, height=0)
    with pytest.raises(mac_capture.ScreenRecordingDenied):
        mac_capture.capture_window_png(1, quartz=q)


@pytest.mark.skipif(sys.platform == "darwin", reason="off-macOS behavior")
def test_load_quartz_refuses_off_macos():
    with pytest.raises(RuntimeError):
        mac_capture._load_quartz()
