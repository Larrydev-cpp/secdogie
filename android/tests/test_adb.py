import subprocess

import pytest

from secdogie_android import adb
from secdogie_android.adb import Adb, AdbError


class _FakeProc:
    def __init__(self, stdout=b"", stderr=b"", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _capture_calls(monkeypatch, proc=None, *, error=None):
    """Patch subprocess.run to record argv, and make adb look installed."""
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        if error is not None:
            raise error
        return proc if proc is not None else _FakeProc()

    monkeypatch.setattr(adb.subprocess, "run", fake_run)
    monkeypatch.setattr(adb.shutil, "which", lambda name: "/usr/bin/adb")
    return calls


def test_serial_is_threaded_into_every_call(monkeypatch):
    calls = _capture_calls(monkeypatch)
    Adb(serial="ABC123").tap(5, 6)
    argv = calls[0][0]
    assert argv[:4] == ["adb", "-s", "ABC123", "shell"]
    assert argv[-4:] == ["input", "tap", "5", "6"]


def test_no_serial_omits_the_flag(monkeypatch):
    calls = _capture_calls(monkeypatch)
    Adb().tap(1, 2)
    assert calls[0][0][0:2] == ["adb", "shell"]


def test_screencap_uses_exec_out_and_returns_binary(monkeypatch):
    calls = _capture_calls(monkeypatch, _FakeProc(stdout=b"\x89PNG..."))
    png = Adb().screencap_png()
    assert png == b"\x89PNG..."
    assert calls[0][0][1:] == ["exec-out", "screencap", "-p"]
    assert calls[0][1]["capture_output"] is True


def test_screencap_empty_output_raises(monkeypatch):
    _capture_calls(monkeypatch, _FakeProc(stdout=b""))
    with pytest.raises(AdbError):
        Adb().screencap_png()


def test_swipe_and_long_press(monkeypatch):
    calls = _capture_calls(monkeypatch)
    a = Adb()
    a.swipe(1, 2, 3, 4, 250)
    a.long_press(10, 20)
    assert calls[0][0][-7:] == ["input", "swipe", "1", "2", "3", "4", "250"]
    # long_press is a zero-distance swipe held for a long duration
    assert calls[1][0][-7:] == ["input", "swipe", "10", "20", "10", "20", "600"]


def test_text_encodes_spaces_and_metachars(monkeypatch):
    calls = _capture_calls(monkeypatch)
    Adb().text("a b&c")
    typed = calls[0][0][-1]
    assert typed == "a%sb\\&c"


def test_keyevent_alias_and_letter_and_longpress(monkeypatch):
    calls = _capture_calls(monkeypatch)
    a = Adb()
    a.keyevent("enter")
    a.keyevent("k")
    a.keyevent("back", longpress=True)
    assert calls[0][0][-2:] == ["keyevent", "KEYCODE_ENTER"]
    assert calls[1][0][-2:] == ["keyevent", "KEYCODE_K"]
    assert calls[2][0][-3:] == ["keyevent", "--longpress", "KEYCODE_BACK"]


def test_open_uri_uses_view_intent(monkeypatch):
    calls = _capture_calls(monkeypatch)
    Adb().open_uri("https://example.com")
    assert calls[0][0][-6:] == [
        "am", "start", "-a", "android.intent.action.VIEW", "-d", "https://example.com"
    ]


def test_list_devices_returns_only_ready_devices(monkeypatch):
    out = b"List of devices attached\nABC123\tdevice\nOFF1\toffline\nUNAUTH\tunauthorized\nXYZ\tdevice\n"
    _capture_calls(monkeypatch, _FakeProc(stdout=out))
    assert Adb().list_devices() == ["ABC123", "XYZ"]


def test_nonzero_exit_raises_with_stderr(monkeypatch):
    _capture_calls(monkeypatch, _FakeProc(stderr=b"device offline", returncode=1))
    with pytest.raises(AdbError) as ei:
        Adb().tap(1, 1)
    assert "device offline" in str(ei.value)


def test_missing_adb_binary_raises_actionable_error(monkeypatch):
    monkeypatch.setattr(adb.shutil, "which", lambda name: None)
    with pytest.raises(AdbError) as ei:
        Adb().tap(1, 1)
    assert "platform-tools" in str(ei.value)


def test_timeout_is_wrapped(monkeypatch):
    _capture_calls(monkeypatch, error=subprocess.TimeoutExpired(cmd="adb", timeout=20))
    with pytest.raises(AdbError) as ei:
        Adb().tap(1, 1)
    assert "timed out" in str(ei.value)


def test_resolve_keycode_passthrough_for_existing_keycode():
    assert adb._resolve_keycode("KEYCODE_CAMERA") == "KEYCODE_CAMERA"
    assert adb._resolve_keycode("7") == "KEYCODE_7"


def test_ui_dump_slices_xml_out_of_trailing_status_line(monkeypatch):
    # `uiautomator dump /dev/tty` prints the XML then a status line; ui_dump()
    # must return only the <hierarchy>...</hierarchy> span.
    payload = b"<?xml version='1.0'?><hierarchy rotation=\"0\"><node bounds=\"[0,0][1,1]\"/></hierarchy>UI hierchary dumped to: /dev/tty"
    calls = _capture_calls(monkeypatch, _FakeProc(stdout=payload))
    xml = Adb().ui_dump()
    assert xml == "<hierarchy rotation=\"0\"><node bounds=\"[0,0][1,1]\"/></hierarchy>"
    assert calls[0][0][1:] == ["exec-out", "uiautomator", "dump", "/dev/tty"]


def test_ui_dump_raises_when_no_hierarchy(monkeypatch):
    _capture_calls(monkeypatch, _FakeProc(stdout=b"ERROR: could not get idle state"))
    with pytest.raises(AdbError):
        Adb().ui_dump()
