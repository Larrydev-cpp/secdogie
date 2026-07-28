"""Tests for Windows virtual-desktop support (vdesktop.py).

The COM queries and the Win+Ctrl keystrokes are on-machine. What's proved here
is the part that carries the correctness: the search loop never *assumes* a
keystroke worked -- it re-checks with the COM query after each switch, so a
shortcut that silently did nothing fails honestly instead of leaving the caller
convinced the right desktop is on screen."""
import sys

from secdogie_agent import vdesktop


class Desktops:
    """A fake Win+Tab setup: `current` is the desktop on screen, `home` is the
    one holding the window we're looking for. `switch()` steps right and wraps,
    exactly like Win+Ctrl+Right."""

    def __init__(self, count, current, home, *, broken=False):
        self.count, self.current, self.home = count, current, home
        self.broken = broken   # the shortcut can't be sent at all
        self.switches = 0

    def is_on_current(self):
        return self.current == self.home

    def switch(self):
        if self.broken:
            return False
        self.switches += 1
        self.current = (self.current + 1) % self.count
        return True


def _find(d, **kw):
    return vdesktop.find_desktop_with(
        d.is_on_current, d.switch, settle=0, sleep=lambda s: None, **kw
    )


def test_already_on_the_right_desktop_switches_nothing():
    d = Desktops(count=4, current=2, home=2)
    assert _find(d) is True
    assert d.switches == 0


def test_switches_until_the_window_is_on_screen():
    d = Desktops(count=4, current=0, home=2)
    assert _find(d) is True
    assert d.switches == 2          # 0 -> 1 -> 2, confirmed after each step


def test_wraps_around_past_the_last_desktop():
    d = Desktops(count=3, current=2, home=0)
    assert _find(d) is True
    assert d.switches == 1          # 2 -> wraps to 0


def test_gives_up_after_max_switches_rather_than_spinning():
    # The window isn't on any desktop we can reach (it closed, or the shortcut
    # is a no-op under policy). Report failure instead of looping forever.
    d = Desktops(count=100, current=0, home=50)
    assert _find(d, max_switches=4) is False
    assert d.switches == 4


def test_a_keystroke_that_cannot_be_sent_fails_immediately():
    d = Desktops(count=4, current=0, home=2, broken=True)
    assert _find(d) is False
    assert d.switches == 0          # never pretends a failed press moved us


def test_it_confirms_rather_than_trusting_the_keystroke():
    """The heart of the design: the COM query is the ground truth. A switch that
    reports success but doesn't actually move must not be believed."""

    class LyingSwitch(Desktops):
        def switch(self):
            self.switches += 1
            return True             # claims success, changes nothing

    d = LyingSwitch(count=4, current=0, home=2)
    assert _find(d, max_switches=3) is False
    assert d.switches == 3


# -- graceful degradation off Windows -----------------------------------------

def test_everything_degrades_cleanly_off_windows(monkeypatch):
    monkeypatch.setattr(vdesktop.sys, "platform", "linux")
    assert vdesktop.available() is False
    assert vdesktop.is_on_current_desktop(1234) is None
    assert vdesktop.desktop_id(1234) is None
    assert vdesktop.same_desktop(1, 2) is None
    # ensure_visible must say False (not crash, not claim success) so the caller
    # falls back to plain raise-then-capture on a single desktop.
    assert vdesktop.ensure_visible(1234) is False
    assert vdesktop.move_to_current_desktop(1234, 5678) is False


def test_manager_is_none_off_windows(monkeypatch):
    if sys.platform.startswith("win"):
        return  # the real COM path only runs on Windows
    assert vdesktop._manager() is None
