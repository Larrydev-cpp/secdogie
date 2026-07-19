import sys
import types

import pytest
from secdogie_open import windows


class _FakeWin:
    def __init__(self, title, left, top, width, height, visible=True, minimized=False,
                 active=False, activate_error=None):
        self.title = title
        self.left = left
        self.top = top
        self.width = width
        self.height = height
        self.isVisible = visible
        self.isMinimized = minimized
        self.isActive = active
        self._activate_error = activate_error
        self.activate_calls = []

    def activate(self, wait=False, user=True):
        if self._activate_error is not None:
            raise self._activate_error
        self.activate_calls.append((wait, user))
        self.isActive = True  # a real activate() call is what flips this


def _install_fake_pywinctl(monkeypatch, wins=None, *, raises=None):
    if raises is not None:
        fake = types.SimpleNamespace(getAllWindows=raises)
    else:
        fake = types.SimpleNamespace(getAllWindows=lambda: wins)
    monkeypatch.setitem(sys.modules, "pywinctl", fake)


def test_list_windows_filters_hidden_minimized_untitled_and_tiny(monkeypatch):
    wins = [
        _FakeWin("Editor", 0, 0, 800, 600),
        _FakeWin("Hidden", 0, 0, 800, 600, visible=False),
        _FakeWin("Minimized", 0, 0, 800, 600, minimized=True),
        _FakeWin("", 0, 0, 800, 600),
        _FakeWin("Tooltip", 0, 0, 20, 20),
    ]
    _install_fake_pywinctl(monkeypatch, wins)
    found = windows.list_windows()
    assert [w.title for w in found] == ["Editor"]


def test_list_windows_sorted_case_insensitively_by_title(monkeypatch):
    wins = [_FakeWin("zebra", 0, 0, 200, 200), _FakeWin("Alpha", 0, 0, 200, 200)]
    _install_fake_pywinctl(monkeypatch, wins)
    found = windows.list_windows()
    assert [w.title for w in found] == ["Alpha", "zebra"]


def test_list_windows_region_matches_geometry(monkeypatch):
    _install_fake_pywinctl(monkeypatch, [_FakeWin("App", 10, 20, 300, 400)])
    found = windows.list_windows()
    assert found[0].region == (10, 20, 300, 400)
    assert found[0].id == "App:10,20,300,400"


def test_list_windows_skips_window_that_raises_mid_enumeration(monkeypatch):
    class Bad:
        @property
        def isVisible(self):
            raise RuntimeError("window closed mid-enumeration")

    _install_fake_pywinctl(monkeypatch, [Bad(), _FakeWin("Ok", 0, 0, 200, 200)])
    found = windows.list_windows()
    assert [w.title for w in found] == ["Ok"]


def test_list_windows_missing_backend_raises_clear_error(monkeypatch):
    # sys.modules[name] = None makes `import pywinctl` raise ImportError,
    # the same as if the package were never installed.
    monkeypatch.setitem(sys.modules, "pywinctl", None)
    with pytest.raises(windows.NoWindowBackendError):
        windows.list_windows()


def test_list_windows_backend_failure_raises_clear_error(monkeypatch):
    def raise_enum():
        raise RuntimeError("no display server (e.g. Wayland)")

    _install_fake_pywinctl(monkeypatch, raises=raise_enum)
    with pytest.raises(windows.NoWindowBackendError):
        windows.list_windows()


def test_list_windows_import_time_display_failure_raises_clear_error(monkeypatch):
    # On Linux, pywinctl's own import chain (pymonctl -> ewmhlib -> Xlib)
    # eagerly opens an X11 connection, so a headless/Wayland-only session can
    # raise a non-ImportError exception (e.g. Xlib.error.DisplayNameError)
    # from the `import pywinctl` line itself, before getAllWindows() is ever
    # reached. `sys.modules["pywinctl"] = None` only simulates a plain
    # ImportError (a missing package), so this needs a real failing import.
    import builtins

    real_import = builtins.__import__
    monkeypatch.delitem(sys.modules, "pywinctl", raising=False)

    def fake_import(name, *args, **kwargs):
        if name == "pywinctl":
            raise RuntimeError('Xlib.error.DisplayNameError: Bad display name ""')
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(windows.NoWindowBackendError) as ei:
        windows.list_windows()
    assert "could not initialize window enumeration" in str(ei.value)


# -- focus_window ---------------------------------------------------------

def _win_info(title="App", left=0, top=0, width=300, height=400):
    return windows.WindowInfo(
        id=f"{title}:{left},{top},{width},{height}", title=title,
        left=left, top=top, width=width, height=height,
    )


def test_focus_window_activates_exact_geometry_match(monkeypatch):
    target = _FakeWin("App", 0, 0, 300, 400)
    other = _FakeWin("Other", 10, 10, 200, 200)
    _install_fake_pywinctl(monkeypatch, [other, target])

    assert windows.focus_window(_win_info(), timeout=0.05, poll_interval=0.01) is True
    assert target.activate_calls == [(False, True)]
    assert other.activate_calls == []


def test_focus_window_falls_back_to_title_match_when_geometry_moved(monkeypatch):
    # The window moved since it was listed (still same title).
    moved = _FakeWin("App", 999, 999, 300, 400)
    _install_fake_pywinctl(monkeypatch, [moved])

    assert windows.focus_window(_win_info(left=0, top=0), timeout=0.05, poll_interval=0.01) is True
    assert moved.activate_calls == [(False, True)]


def test_focus_window_returns_false_when_no_window_matches(monkeypatch):
    _install_fake_pywinctl(monkeypatch, [_FakeWin("SomethingElse", 0, 0, 100, 100)])
    assert windows.focus_window(_win_info(), timeout=0.05, poll_interval=0.01) is False


def test_focus_window_returns_false_when_activate_raises(monkeypatch):
    target = _FakeWin("App", 0, 0, 300, 400, activate_error=RuntimeError("no WM support"))
    _install_fake_pywinctl(monkeypatch, [target])
    assert windows.focus_window(_win_info(), timeout=0.05, poll_interval=0.01) is False


def test_focus_window_returns_false_if_never_confirmed_active(monkeypatch):
    # activate() succeeds but the window manager never actually grants focus
    # (isActive stays False) -- focus_window must give up, not hang.
    class NeverActivates(_FakeWin):
        def activate(self, wait=False, user=True):
            self.activate_calls.append((wait, user))
            # deliberately do NOT flip isActive

    target = NeverActivates("App", 0, 0, 300, 400)
    _install_fake_pywinctl(monkeypatch, [target])
    assert windows.focus_window(_win_info(), timeout=0.05, poll_interval=0.01) is False


def test_focus_window_missing_pywinctl_returns_false(monkeypatch):
    monkeypatch.setitem(sys.modules, "pywinctl", None)
    assert windows.focus_window(_win_info()) is False


def test_focus_window_getallwindows_failure_returns_false(monkeypatch):
    _install_fake_pywinctl(monkeypatch, raises=lambda: (_ for _ in ()).throw(RuntimeError("no display")))
    assert windows.focus_window(_win_info()) is False
