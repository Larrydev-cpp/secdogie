"""Tests for the force-foreground helper (osfocus.py). The Win32 raise
(AttachThreadInput / SetForegroundWindow / SPI lock-timeout) is on-machine; what
is proved here is the logic that decides correctness: display-server detection
(especially the Wayland "cannot force" call-out) and the state-settle confirm
loop that turns "I called activate" into "focus is verified"."""
from secdogie_agent import osfocus

# -- display_server ------------------------------------------------------------

def test_display_server_windows_and_macos(monkeypatch):
    monkeypatch.setattr(osfocus.sys, "platform", "win32")
    assert osfocus.display_server() == "windows"
    monkeypatch.setattr(osfocus.sys, "platform", "darwin")
    assert osfocus.display_server() == "macos"


def test_display_server_detects_wayland(monkeypatch):
    monkeypatch.setattr(osfocus.sys, "platform", "linux")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    assert osfocus.display_server() == "wayland"

    # Also via XDG_SESSION_TYPE, with no WAYLAND_DISPLAY set.
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    assert osfocus.display_server() == "wayland"


def test_display_server_x11(monkeypatch):
    monkeypatch.setattr(osfocus.sys, "platform", "linux")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setenv("DISPLAY", ":0")
    assert osfocus.display_server() == "x11"


# -- confirm_foreground (the state-settle loop) --------------------------------

class _Clock:
    """A controllable monotonic clock; every sleep() advances it, so the loop's
    timeout is exercised deterministically without real waiting."""

    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, dt):
        self.t += dt


def test_confirm_returns_immediately_when_already_active():
    raised = {"n": 0}
    ok = osfocus.confirm_foreground(
        is_active=lambda: True,
        raise_once=lambda: raised.__setitem__("n", raised["n"] + 1),
    )
    assert ok is True
    assert raised["n"] == 0  # no need to raise -- it was already frontmost


def test_confirm_succeeds_once_focus_lands_after_a_couple_polls():
    clock = _Clock()
    state = {"active": False}

    def raise_once():
        # Focus lands a couple polls later (models the compositor taking a moment).
        state["landing_at"] = clock.now() + 0.06

    def is_active():
        if not state["active"] and "landing_at" in state and clock.now() >= state["landing_at"]:
            state["active"] = True
        return state["active"]

    ok = osfocus.confirm_foreground(
        is_active, raise_once, settle_timeout=1.0, poll=0.03,
        clock=clock.now, sleep=clock.sleep,
    )
    assert ok is True


def test_confirm_times_out_when_focus_never_lands():
    clock = _Clock()
    raised = {"n": 0}
    ok = osfocus.confirm_foreground(
        is_active=lambda: False,  # never becomes active (e.g. WM refused)
        raise_once=lambda: raised.__setitem__("n", raised["n"] + 1),
        settle_timeout=0.2, poll=0.03,
        clock=clock.now, sleep=clock.sleep,
    )
    assert ok is False
    assert raised["n"] == 1  # tried exactly once, then polled to the deadline


# -- outcome labels on non-windows --------------------------------------------

def test_force_foreground_hwnd_is_honest_off_windows(monkeypatch):
    monkeypatch.setattr(osfocus.sys, "platform", "linux")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert osfocus.force_foreground_hwnd(1234) == osfocus.CANNOT_FORCE

    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setenv("DISPLAY", ":0")
    assert osfocus.force_foreground_hwnd(1234) == osfocus.UNSUPPORTED
