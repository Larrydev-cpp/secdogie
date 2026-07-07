"""Action executor tests. pyautogui/pyperclip are replaced with fakes so these
run headless with no display and no real input."""
import sys
import types

import pytest

from secdogie_agent import actions
from secdogie_agent.providers.base import Action


def _fake_pyautogui(monkeypatch):
    calls = []
    fake = types.SimpleNamespace()

    def rec(name):
        def f(*a, **k):
            calls.append((name, a, k))

        return f

    for n in [
        "click", "doubleClick", "moveTo", "dragTo", "typewrite",
        "press", "hotkey", "keyDown", "keyUp", "hscroll", "vscroll",
    ]:
        setattr(fake, n, rec(n))
    monkeypatch.setitem(sys.modules, "pyautogui", fake)
    return calls


def _run(d, **kw):
    return actions.execute(Action.from_dict(d), move_duration=0, settle=0, **kw)


def test_hold_key_presses_then_releases(monkeypatch):
    calls = _fake_pyautogui(monkeypatch)
    res = _run({"action": "hold_key", "keys": ["right"], "seconds": 0})
    names = [c[0] for c in calls]
    assert names == ["keyDown", "keyUp"]
    assert calls[0][1] == ("right",) and calls[1][1] == ("right",)
    assert "held" in res


def test_hold_multiple_keys_releases_in_reverse(monkeypatch):
    calls = _fake_pyautogui(monkeypatch)
    _run({"action": "hold_key", "keys": ["shift", "right"], "seconds": 0})
    downs = [c[1][0] for c in calls if c[0] == "keyDown"]
    ups = [c[1][0] for c in calls if c[0] == "keyUp"]
    assert downs == ["shift", "right"]
    assert ups == ["right", "shift"]  # released in reverse order


def test_key_names_are_lowercased(monkeypatch):
    # The model sometimes capitalizes special keys ('Return'), which pyautogui
    # silently ignores; they must be normalized to lowercase before pressing.
    calls = _fake_pyautogui(monkeypatch)
    _run({"action": "key", "keys": ["Return"]})
    assert ("press", ("return",), {}) in calls


def test_hotkey_combo_is_lowercased(monkeypatch):
    calls = _fake_pyautogui(monkeypatch)
    _run({"action": "key", "keys": ["Ctrl", "C"]})
    assert ("hotkey", ("ctrl", "c"), {}) in calls


def test_type_ascii_uses_typewrite(monkeypatch):
    calls = _fake_pyautogui(monkeypatch)
    _run({"action": "type", "text": "hello"})
    assert [c[0] for c in calls] == ["typewrite"]


def test_type_unicode_uses_clipboard(monkeypatch):
    calls = _fake_pyautogui(monkeypatch)
    copied = {}
    fake_clip = types.SimpleNamespace(copy=lambda t: copied.setdefault("text", t))
    monkeypatch.setitem(sys.modules, "pyperclip", fake_clip)
    monkeypatch.setattr(actions.sys, "platform", "linux")
    res = _run({"action": "type", "text": "你好"})
    assert copied["text"] == "你好"           # went to clipboard
    assert ("hotkey", ("ctrl", "v"), {}) in calls  # pasted
    assert "clipboard" in res


def test_type_unicode_uses_cmd_v_on_mac(monkeypatch):
    calls = _fake_pyautogui(monkeypatch)
    monkeypatch.setitem(sys.modules, "pyperclip", types.SimpleNamespace(copy=lambda t: None))
    monkeypatch.setattr(actions.sys, "platform", "darwin")
    _run({"action": "type", "text": "café"})
    assert ("hotkey", ("command", "v"), {}) in calls


def test_open_uses_xdg_open_on_linux(monkeypatch):
    _fake_pyautogui(monkeypatch)
    popen_calls = []
    monkeypatch.setattr(actions.sys, "platform", "linux")
    monkeypatch.setattr(actions.subprocess, "Popen", lambda args, *a, **k: popen_calls.append(args))
    res = _run({"action": "open", "path": "/tmp/file.txt"})
    assert popen_calls == [["xdg-open", "/tmp/file.txt"]]
    assert "opened" in res


def test_open_requires_path(monkeypatch):
    _fake_pyautogui(monkeypatch)
    with pytest.raises(ValueError):
        _run({"action": "open"})


def test_new_actions_are_valid_schema():
    # Action.from_dict must accept the new kinds and their fields.
    a = Action.from_dict({"action": "hold_key", "keys": ["right"], "seconds": 2})
    assert a.kind == "hold_key" and a.seconds == 2
    b = Action.from_dict({"action": "open", "path": "/x"})
    assert b.kind == "open" and b.path == "/x"
