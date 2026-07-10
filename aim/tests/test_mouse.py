"""RelativeMouse plumbing tests -- the parts that don't need a real device."""
import pytest
from secdogie_aim.mouse import RecordingMouse, RelativeMouse, open_mouse


def test_recording_mouse_accumulates_moves_and_events():
    m = RecordingMouse()
    m.move(10, -5)
    m.move(-3, 2)
    m.press()
    m.release()
    m.click()
    assert m.moves == [(10, -5), (-3, 2)]
    assert m.net == (7, -3)
    assert m.events == ["press", "release", "click"]


def test_recording_mouse_satisfies_the_protocol():
    assert isinstance(RecordingMouse(), RelativeMouse)


def test_open_mouse_rejects_unsupported_platforms(monkeypatch):
    import secdogie_aim.mouse as mouse_mod

    monkeypatch.setattr(mouse_mod.sys, "platform", "sunos5")
    with pytest.raises(RuntimeError, match="Windows and Linux"):
        open_mouse()


def test_windows_mouse_refuses_to_build_off_windows(monkeypatch):
    import secdogie_aim.mouse as mouse_mod

    monkeypatch.setattr(mouse_mod.sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="Windows-only"):
        mouse_mod.WindowsMouse()


def test_linux_mouse_reports_missing_uinput_clearly(monkeypatch):
    # Without python-uinput installed the error must say how to fix it, not
    # dump an ImportError traceback.
    import builtins

    import secdogie_aim.mouse as mouse_mod

    real_import = builtins.__import__

    def no_uinput(name, *a, **k):
        if name == "uinput":
            raise ImportError("No module named 'uinput'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_uinput)
    with pytest.raises(RuntimeError, match="secdogie-aim\\[linux\\]"):
        mouse_mod.LinuxUinputMouse()
