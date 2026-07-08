import base64

import pytest

from secdogie_ios.wda import Wda, WdaError


class RecordingWda(Wda):
    """A Wda whose transport is stubbed: records every (method, path, body)
    and returns canned responses keyed by path suffix."""

    def __init__(self, responses=None, **kw):
        super().__init__(**kw)
        self.calls = []
        self._responses = responses or {}

    def _request(self, method, path, body=None):
        self.calls.append((method, path, body))
        for suffix, resp in self._responses.items():
            if path.endswith(suffix):
                return resp
        return {}


def _session_resp(sid="S1"):
    return {"/session": {"sessionId": sid}}


def test_ensure_session_created_once_and_cached():
    wda = RecordingWda(responses=_session_resp("SID"))
    assert wda.ensure_session() == "SID"
    assert wda.ensure_session() == "SID"
    assert [c for c in wda.calls if c[1] == "/session"] == [("POST", "/session", {"capabilities": {"alwaysMatch": {}}})]


def test_session_id_parsed_from_value_shape():
    wda = RecordingWda(responses={"/session": {"value": {"sessionId": "V2"}}})
    assert wda.ensure_session() == "V2"


def test_missing_session_id_raises():
    wda = RecordingWda(responses={"/session": {"value": {}}})
    with pytest.raises(WdaError):
        wda.ensure_session()


def test_tap_uses_session_prefixed_path_and_body():
    wda = RecordingWda(responses=_session_resp("SID"))
    wda.tap(120, 340)
    assert wda.calls[-1] == ("POST", "/session/SID/wda/tap", {"x": 120, "y": 340})


def test_double_tap_touch_and_hold_drag_paths():
    wda = RecordingWda(responses=_session_resp())
    wda.double_tap(1, 2)
    wda.touch_and_hold(3, 4, 0.6)
    wda.drag(5, 6, 7, 8, 0.4)
    paths = [c[1] for c in wda.calls if "/wda/" in c[1] or "dragfrom" in c[1]]
    assert "/session/S1/wda/doubleTap" in paths
    assert "/session/S1/wda/touchAndHold" in paths
    assert wda.calls[-1] == (
        "POST",
        "/session/S1/wda/dragfromtoforduration",
        {"fromX": 5, "fromY": 6, "toX": 7, "toY": 8, "duration": 0.4},
    )
    # touchAndHold carries its duration
    hold = next(c for c in wda.calls if c[1].endswith("touchAndHold"))
    assert hold[2] == {"x": 3, "y": 4, "duration": 0.6}


def test_type_text_wraps_in_value_array():
    wda = RecordingWda(responses=_session_resp())
    wda.type_text("héllo")
    assert wda.calls[-1] == ("POST", "/session/S1/wda/keys", {"value": ["héllo"]})


def test_press_button_and_homescreen():
    wda = RecordingWda(responses=_session_resp())
    wda.press_button("home")
    wda.homescreen()
    assert ("POST", "/session/S1/wda/pressButton", {"name": "home"}) in wda.calls
    # homescreen is session-less -> no /session prefix
    assert ("POST", "/wda/homescreen", None) in wda.calls


def test_open_url_is_session_scoped():
    wda = RecordingWda(responses=_session_resp())
    wda.open_url("https://example.com")
    assert wda.calls[-1] == ("POST", "/session/S1/url", {"url": "https://example.com"})


def test_screenshot_decodes_base64():
    payload = b"\x89PNG\r\n\x1a\nfake"
    b64 = base64.b64encode(payload).decode()
    wda = RecordingWda(responses={"/screenshot": {"value": b64}})
    assert wda.screenshot_png() == payload
    # screenshot is session-less: no session was created
    assert all(c[1] != "/session" for c in wda.calls)


def test_screenshot_missing_value_raises():
    wda = RecordingWda(responses={"/screenshot": {"value": ""}})
    with pytest.raises(WdaError):
        wda.screenshot_png()


def test_window_size_parsed_as_points():
    wda = RecordingWda(responses={"/window/size": {"value": {"width": 390, "height": 844}}})
    assert wda.window_size() == (390, 844)


def test_window_size_bad_shape_raises():
    wda = RecordingWda(responses={"/window/size": {"value": {}}})
    with pytest.raises(WdaError):
        wda.window_size()
