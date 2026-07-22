"""Actually put a window in the foreground -- and *know* whether it worked.

Naive activation (SetForegroundWindow, or pywinctl's `.activate()`) silently
fails under Windows' ForegroundLockTimeout: an app that isn't already foreground
and hasn't had recent user input is refused and gets only a taskbar flash. A
caller that then clicks or types lands on the WRONG window while believing it
focused the right one -- the "focus hallucination". This module does the real
thing and then verifies it:

  1. Force (Windows): briefly zero SPI_SETFOREGROUNDLOCKTIMEOUT and attach our
     input thread to the current foreground thread (AttachThreadInput) so the OS
     lets SetForegroundWindow through -- the standard lock-timeout bypass -- then
     detach and restore the timeout.
  2. Settle: on every platform, don't assume -- poll until the target is really
     the active/foreground window (or a timeout elapses), so the caller acts only
     once focus has actually landed.
  3. Be honest about the impossible: on Wayland a client cannot raise itself or
     another window at all (the compositor forbids it by design), so we return
     CANNOT_FORCE instead of pretending, and the caller can ask the user to click
     the target once rather than firing blind.

The Win32 pieces are on-machine (guarded ctypes). The parts that carry the logic
-- the display-server detection and the settle/confirm loop -- are pure and
unit-tested against fakes.
"""
from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable

# Outcome labels (also what callers log / branch on).
FOCUSED = "focused"          # it is now the foreground/active window
ALREADY = "already"          # it already was
CANNOT_FORCE = "cannot-force"  # the platform forbids it (Wayland) -- ask the user
TIMEOUT = "timeout"          # tried, but focus never landed within the deadline
UNSUPPORTED = "unsupported"  # no way to even attempt on this platform/build

_DEFAULT_SETTLE_S = 1.0
_DEFAULT_POLL_S = 0.03


def display_server() -> str:
    """Which windowing system we're on: "windows" | "macos" | "wayland" | "x11"
    | "unknown". Wayland is called out specifically because it changes what's
    *possible* (no programmatic focus stealing), not just how it's done."""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return "wayland"
    if os.environ.get("DISPLAY") or os.environ.get("XDG_SESSION_TYPE", "").lower() == "x11":
        return "x11"
    return "unknown"


def confirm_foreground(
    is_active: Callable[[], bool],
    raise_once: Callable[[], None],
    *,
    settle_timeout: float = _DEFAULT_SETTLE_S,
    poll: float = _DEFAULT_POLL_S,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """The state-settle loop, factored out so it's provable without a desktop:
    ask the target to raise itself, then poll `is_active` until it reports True
    or `settle_timeout` elapses. Returns whether focus actually landed. This is
    what turns "I called activate()" into "focus is confirmed here", so the next
    click can't fire before the window is really frontmost. If it was already
    active, `raise_once` still runs (harmless) and the first poll returns True."""
    if is_active():
        return True
    raise_once()
    deadline = clock() + settle_timeout
    while clock() < deadline:
        if is_active():
            return True
        sleep(poll)
    return is_active()


# -- Windows force-foreground (on-machine) ------------------------------------


def _win_force_foreground(hwnd) -> None:
    """The ForegroundLockTimeout bypass: zero the lock timeout, attach our input
    thread to the foreground thread so SetForegroundWindow is honoured, raise the
    window, then detach and restore the timeout. Best-effort -- every step is
    guarded; the confirm loop is what actually decides success afterward."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    SPI_GETFOREGROUNDLOCKTIMEOUT = 0x2000
    SPI_SETFOREGROUNDLOCKTIMEOUT = 0x2001
    SPIF_SENDCHANGE = 0x2
    SW_RESTORE = 9

    saved = wintypes.DWORD(0)
    try:
        user32.SystemParametersInfoW(SPI_GETFOREGROUNDLOCKTIMEOUT, 0, ctypes.byref(saved), 0)
        user32.SystemParametersInfoW(SPI_SETFOREGROUNDLOCKTIMEOUT, 0, ctypes.c_void_p(0), SPIF_SENDCHANGE)
    except Exception:
        pass

    fg_thread = target_thread = 0
    attached = False
    try:
        fg = user32.GetForegroundWindow()
        fg_thread = user32.GetWindowThreadProcessId(fg, None)
        target_thread = user32.GetWindowThreadProcessId(hwnd, None)
        if fg_thread and target_thread and fg_thread != target_thread:
            attached = bool(user32.AttachThreadInput(target_thread, fg_thread, True))
        # A minimized window must be restored before it can take the foreground.
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass
    finally:
        if attached:
            try:
                user32.AttachThreadInput(target_thread, fg_thread, False)
            except Exception:
                pass
        try:
            # Restore the user's original lock-timeout setting.
            user32.SystemParametersInfoW(
                SPI_SETFOREGROUNDLOCKTIMEOUT, 0, ctypes.c_void_p(saved.value), SPIF_SENDCHANGE
            )
        except Exception:
            pass
        del kernel32  # (imported for symmetry/future use; keep the reference tidy)


def force_foreground_hwnd(
    hwnd,
    *,
    settle_timeout: float = _DEFAULT_SETTLE_S,
    poll: float = _DEFAULT_POLL_S,
) -> str:
    """Windows: force `hwnd` frontmost past ForegroundLockTimeout and confirm it.
    Returns one of the outcome labels. On Wayland (never reached with an HWND, but
    for symmetry) or an unsupported build, returns the honest label rather than a
    false FOCUSED."""
    server = display_server()
    if server == "wayland":
        return CANNOT_FORCE
    if server != "windows":
        return UNSUPPORTED
    try:
        import ctypes

        user32 = ctypes.windll.user32

        def is_active() -> bool:
            return user32.GetForegroundWindow() == hwnd

        if is_active():
            return ALREADY
        landed = confirm_foreground(
            is_active, lambda: _win_force_foreground(hwnd),
            settle_timeout=settle_timeout, poll=poll,
        )
        return FOCUSED if landed else TIMEOUT
    except Exception:
        return UNSUPPORTED


# -- record / restore the pre-launch foreground window ------------------------
#
# For the single desktop agent: before our own menu/dialogs steal focus, remember
# what was in front; once the agent is about to act, put it back, so the model's
# first frame -- and the click that follows -- land there and not on a ghost of
# our GUI. An opaque token (a Windows HWND, or a pywinctl window on X11) hides the
# per-platform handle from callers.


def current_foreground():
    """An opaque token for whatever window is foreground right now, or None if it
    can't be determined (Wayland, no backend). Pass it back to
    restore_foreground() later. Call this BEFORE showing any GUI of our own."""
    server = display_server()
    if server == "windows":
        try:
            import ctypes

            hwnd = ctypes.windll.user32.GetForegroundWindow()
            return hwnd or None
        except Exception:
            return None
    if server == "x11":
        try:
            import pywinctl

            return pywinctl.getActiveWindow()
        except Exception:
            return None
    return None  # wayland/unknown: nothing we could restore anyway


def restore_foreground(token, *, settle_timeout: float = _DEFAULT_SETTLE_S, poll: float = _DEFAULT_POLL_S) -> bool:
    """Bring a token from current_foreground() back to the front, confirming it
    landed (state settle). False if there's no token or it can't be raised."""
    if token is None:
        return False
    server = display_server()
    if server == "windows":
        return force_foreground_hwnd(token, settle_timeout=settle_timeout, poll=poll) in (FOCUSED, ALREADY)
    if server == "x11":
        try:
            return confirm_foreground(
                lambda: bool(getattr(token, "isActive", False)),
                lambda: token.activate(wait=False),
                settle_timeout=settle_timeout, poll=poll,
            )
        except Exception:
            return False
    return False


def activate_title(title: str, *, settle_timeout: float = _DEFAULT_SETTLE_S, poll: float = _DEFAULT_POLL_S) -> bool:
    """Find a window by exact title and force it frontmost (confirmed). For the
    single agent's --window targeting. False on Wayland (can't), if pywinctl is
    missing, or if no window matches / it won't come forward."""
    if display_server() == "wayland":
        return False
    try:
        import pywinctl
    except Exception:
        return False
    try:
        matches = [w for w in pywinctl.getAllWindows() if getattr(w, "title", None) == title]
    except Exception:
        return False
    if not matches:
        return False
    target = matches[0]

    hwnd = None
    if display_server() == "windows":
        try:
            hwnd = target.getHandle()
        except Exception:
            hwnd = None

    def is_active() -> bool:
        try:
            return bool(target.isActive)
        except Exception:
            return False

    def raise_once() -> None:
        if hwnd is not None:
            _win_force_foreground(hwnd)
        else:
            try:
                target.activate(wait=False)
            except Exception:
                pass

    return confirm_foreground(is_active, raise_once, settle_timeout=settle_timeout, poll=poll)
