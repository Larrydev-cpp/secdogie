"""Reduced motion: the person's own setting decides whether the light breathes."""
from __future__ import annotations

from types import SimpleNamespace

from secdogie_studio.motion import ENV, os_prefers_less_motion, qt_prefers_less_motion, reduce_motion


def test_each_desktop_is_read_the_way_it_stores_the_setting():
    asked = []

    def run(cmd):
        asked.append(cmd[0])
        return {"defaults": "1", "gsettings": "false"}[cmd[0]]

    assert os_prefers_less_motion("darwin", run) is True
    assert os_prefers_less_motion("linux", run) is True
    assert os_prefers_less_motion("linux", lambda cmd: "true") is False
    assert os_prefers_less_motion("darwin", lambda cmd: "") is False       # unreadable: no preference
    assert os_prefers_less_motion("win32", run, windows=lambda: True) is True
    assert asked == ["defaults", "gsettings"]


def test_an_unreadable_windows_setting_counts_as_no_preference():
    def broken():
        raise OSError("no user32")

    assert os_prefers_less_motion("win32", windows=broken) is False


def _app(name):
    preference = SimpleNamespace(name=name)
    hints = SimpleNamespace(accessibility=lambda: SimpleNamespace(motionPreference=lambda: preference))
    return SimpleNamespace(styleHints=lambda: hints)


def test_qt_answers_when_it_can_and_older_qt_defers_to_the_os():
    assert qt_prefers_less_motion(_app("ReducedMotion")) is True
    assert qt_prefers_less_motion(_app("NoPreference")) is False
    old_qt = SimpleNamespace(styleHints=lambda: SimpleNamespace())           # no accessibility() before 6.10
    assert qt_prefers_less_motion(old_qt) is None

    assert reduce_motion(_app("ReducedMotion"), env={}, os_check=lambda: False) is True
    assert reduce_motion(old_qt, env={}, os_check=lambda: True) is True
    assert reduce_motion(_app("NoPreference"), env={}, os_check=lambda: False) is False


def test_the_environment_overrides_both():
    assert reduce_motion(_app("ReducedMotion"), env={ENV: "0"}, os_check=lambda: True) is False
    assert reduce_motion(None, env={ENV: "1"}, os_check=lambda: False) is True
    assert reduce_motion(None, env={ENV: "maybe"}, os_check=lambda: True) is True
