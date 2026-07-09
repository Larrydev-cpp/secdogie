"""Action executor tests. pyautogui/pyperclip are replaced with fakes so these
run headless with no display and no real input."""
import sys
import threading
import time
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


# -- track_click routes to the reflex layer, holding the input lock -----------

def test_track_click_routes_to_reflex_under_the_input_lock(monkeypatch):
    # track_click is an input action: it must hold _INPUT_LOCK for the whole
    # (multi-second) local pursuit, and hand its coords + timeout to the reflex
    # layer. Stub the reflex layer so no numpy/display is needed here.
    from secdogie_agent import reflex

    _fake_pyautogui(monkeypatch)  # _dispatch imports pyautogui at its top, before the branch
    seen = {}

    def fake_track(x, y, *, timeout_s=None):
        seen["args"] = (x, y, timeout_s)
        seen["locked"] = actions._INPUT_LOCK.locked()  # proves we ran inside the guard
        return "reflex track: clicked"

    monkeypatch.setattr(reflex, "track_click_target", fake_track)
    res = _run({"action": "track_click", "x": 12, "y": 34, "seconds": 8})
    assert seen["args"] == (12, 34, 8)
    assert seen["locked"] is True
    assert res == "reflex track: clicked"
    assert "track_click" not in actions._NON_INPUT_KINDS  # not benign -> takes the lock


# -- concurrent input serialization (the multi-actor / distributed case) ------

def test_concurrent_clicks_do_not_interleave_move_and_press(monkeypatch):
    # Two actors click at once (as open/ does with several windows). Each
    # move->press must complete as a unit: no thread's press may land between
    # another thread's move and its own press, or clicks hit the wrong place.
    events: list[tuple[str, int]] = []
    lock = threading.Lock()
    fake = types.SimpleNamespace()

    def moveTo(*a, **k):  # noqa: N802 -- mirrors pyautogui's name
        with lock:
            events.append(("move", threading.get_ident()))
        time.sleep(0.02)  # widen the race window a mid-move steal would exploit

    def click(*a, **k):
        with lock:
            events.append(("click", threading.get_ident()))

    fake.moveTo, fake.click = moveTo, click
    monkeypatch.setitem(sys.modules, "pyautogui", fake)

    def worker():
        actions.execute(Action.from_dict({"action": "left_click", "x": 1, "y": 1}), move_duration=0, settle=0)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2)

    # each "move" must be immediately followed by a "press" from the SAME thread
    assert len(events) == 4
    for i in range(0, len(events), 2):
        assert events[i][0] == "move" and events[i + 1][0] == "click"
        assert events[i][1] == events[i + 1][1]  # same actor's move + press, not interleaved


def test_click_waits_for_the_input_lock(monkeypatch):
    _fake_pyautogui(monkeypatch)
    done = threading.Event()

    def worker():
        actions.execute(Action.from_dict({"action": "left_click", "x": 1, "y": 1}), move_duration=0, settle=0)
        done.set()

    with actions._INPUT_LOCK:  # hold input; a click must block until we release
        threading.Thread(target=worker, daemon=True).start()
        assert not done.wait(timeout=0.2)
    assert done.wait(timeout=2)  # proceeds once the lock is free


def test_wait_does_not_take_the_input_lock(monkeypatch):
    # A non-input action must not hold input hostage: a long wait in one actor
    # can't stall another actor's click.
    _fake_pyautogui(monkeypatch)
    done = threading.Event()

    def worker():
        # small positive wait (0 would fall back to 1.0s via `seconds or 1.0`)
        actions.execute(Action.from_dict({"action": "wait", "seconds": 0.01}), move_duration=0, settle=0)
        done.set()

    with actions._INPUT_LOCK:  # input is held, but wait doesn't need it -> finishes anyway
        threading.Thread(target=worker, daemon=True).start()
        assert done.wait(timeout=1)


# -- activate hook (window-focus-aware multi-actor serialization) -------------

def test_activate_is_called_before_dispatch_for_real_actions(monkeypatch):
    calls = _fake_pyautogui(monkeypatch)
    order = []
    _run({"action": "left_click", "x": 1, "y": 1}, activate=lambda: order.append("activate") or True)
    assert order == ["activate"]
    assert [c[0] for c in calls] == ["moveTo", "click"]  # activate happened, then the real dispatch


def test_activate_not_called_for_benign_actions(monkeypatch):
    _fake_pyautogui(monkeypatch)
    called = []
    _run({"action": "wait", "seconds": 0.01}, activate=lambda: called.append(1) or True)
    assert called == []


def test_activate_failure_does_not_block_the_action(monkeypatch):
    # Activation is best-effort: an exception must not stop the real action
    # (nor escape execute()) -- silently doing nothing would be worse than
    # acting against whatever currently has focus.
    calls = _fake_pyautogui(monkeypatch)

    def boom():
        raise RuntimeError("no window manager support")

    result = _run({"action": "left_click", "x": 1, "y": 1}, activate=boom)
    assert "clicked" in result
    assert [c[0] for c in calls] == ["moveTo", "click"]


def test_activate_runs_inside_the_input_lock(monkeypatch):
    # This is the whole point: activate() for actor B must not be able to run
    # until actor A's activate()+dispatch has released the lock, so A's window
    # is guaranteed to have already lost focus by the time B's activate() (and
    # therefore B's action) begins.
    _fake_pyautogui(monkeypatch)
    order = []

    def make_activate(name):
        def _activate():
            assert actions._INPUT_LOCK.locked()  # only true if called from inside execute()'s guard
            order.append(name)
            return True
        return _activate

    def worker(name):
        actions.execute(
            Action.from_dict({"action": "left_click", "x": 1, "y": 1}),
            move_duration=0, settle=0, activate=make_activate(name),
        )

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("A", "B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2)

    assert set(order) == {"A", "B"}  # both ran, and (by the lock) never concurrently
