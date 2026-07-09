import sys
import types

import pytest
from secdogie_open import windows


class _FakeWin:
    def __init__(self, title, left, top, width, height, visible=True, minimized=False):
        self.title = title
        self.left = left
        self.top = top
        self.width = width
        self.height = height
        self.isVisible = visible
        self.isMinimized = minimized


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
