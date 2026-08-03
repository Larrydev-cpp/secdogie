"""Windows virtual desktops (the Win+Tab kind), used to stop windows occluding
each other.

The problem this solves: when several agents drive several windows on ONE
desktop, the windows overlap. A screenshot of "window A's rectangle" captures
whatever is on top of that rectangle, which is often window B -- so the model
reasons about the wrong pixels and then clicks coordinates that mean nothing in
the window it actually acts on. Raising each window before acting only makes it
worse, because every agent is raising its own.

Putting each task's window on its **own virtual desktop** removes the overlap
entirely: a desktop shows only its own window, so a capture of it is always that
window. Note what this does NOT buy: Win+Tab desktops share one input queue and
one foreground window, so this is *occlusion isolation, not parallelism* --
tasks still take turns to act. (For genuine parallelism the desktops have to be
separate sessions or VMs; see fleet/.)

## Why this module looks the way it does

Windows exposes only *queries* publicly. `IVirtualDesktopManager` (a documented,
stable COM interface) can tell you which desktop a window is on and move a window
to a desktop you already have a GUID for. Creating and switching desktops has
**no public API**: the internal COM interface exists but its IID and vtable change
between Windows builds, so binding to it breaks on the next update.

So this module deliberately mixes the two halves:

  * **decide and verify** with the public COM interface -- always reliable,
  * **act** with the standard keyboard shortcuts (Win+Ctrl+D / Win+Ctrl+Arrow),
    which are stable across builds,

and never trusts a keystroke: after each one it re-checks with COM, so a blind
shortcut becomes a confirmed state change. Same "act then confirm" shape as
osfocus.py.

Windows-only by design. Everything degrades to a clean False/None elsewhere.
"""
from __future__ import annotations

import sys
import time
from collections.abc import Callable

# Documented COM identity for IVirtualDesktopManager (stable since Windows 10).
_CLSID_VirtualDesktopManager = "{AA509086-5CA9-4C25-8F95-589D3C07B48A}"
_IID_IVirtualDesktopManager = "{A5CD92FF-29BE-454C-8D04-D82879FB3F1B}"

# How long to let the desktop-switch animation settle before re-checking.
_SWITCH_SETTLE_S = 0.35


def available() -> bool:
    """True if virtual-desktop support can be used on this machine."""
    if not sys.platform.startswith("win"):
        return False
    return _manager() is not None


# -- the pure decision core (provable without Windows) -------------------------


def find_desktop_with(
    is_on_current: Callable[[], bool],
    switch: Callable[[], bool],
    *,
    max_switches: int = 8,
    settle: float = _SWITCH_SETTLE_S,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Switch desktops until the window we care about is on the current one.

    `is_on_current()` is the COM query (the ground truth); `switch()` presses the
    shortcut. We step at most `max_switches` times, confirming after each -- a
    keystroke that silently did nothing (focus stolen, shortcut disabled by
    policy) therefore fails honestly instead of leaving us convinced we moved.

    Pure: both callbacks and the sleep are injected, so the search logic is
    unit-tested with no desktop at all. Returns whether the window ended up
    visible on the current desktop.
    """
    if is_on_current():
        return True
    for _ in range(max_switches):
        if not switch():
            return False  # couldn't even send the keystroke
        if settle:
            sleep(settle)
        if is_on_current():
            return True
    return False


# -- COM: the public, stable queries (on-machine) ------------------------------


def _manager():
    """The IVirtualDesktopManager instance, or None if COM/comtypes isn't usable.
    Cached per call site by the callers -- creating it is cheap but not free."""
    if not sys.platform.startswith("win"):
        return None
    try:
        import comtypes
        import comtypes.client
    except Exception:
        return None
    try:
        from ctypes import HRESULT, POINTER, c_int
        from ctypes.wintypes import BOOL, HWND

        from comtypes import COMMETHOD, GUID, IUnknown

        class IVirtualDesktopManager(IUnknown):
            _iid_ = GUID(_IID_IVirtualDesktopManager)
            _methods_ = [
                COMMETHOD([], HRESULT, "IsWindowOnCurrentVirtualDesktop",
                          (["in"], HWND, "topLevelWindow"),
                          (["out"], POINTER(BOOL), "onCurrentDesktop")),
                COMMETHOD([], HRESULT, "GetWindowDesktopId",
                          (["in"], HWND, "topLevelWindow"),
                          (["out"], POINTER(GUID), "desktopId")),
                COMMETHOD([], HRESULT, "MoveWindowToDesktop",
                          (["in"], HWND, "topLevelWindow"),
                          (["in"], POINTER(GUID), "desktopId")),
            ]

        _ = c_int  # (imported for the ctypes typing above; keep the reference tidy)
        return comtypes.client.CreateObject(
            _CLSID_VirtualDesktopManager, interface=IVirtualDesktopManager
        )
    except Exception:
        return None


def is_on_current_desktop(hwnd) -> bool | None:
    """Is `hwnd` on the virtual desktop the user is looking at right now?
    None if it can't be determined (not Windows, COM unavailable, dead window)."""
    mgr = _manager()
    if mgr is None:
        return None
    try:
        return bool(mgr.IsWindowOnCurrentVirtualDesktop(hwnd))
    except Exception:
        return None


def desktop_id(hwnd) -> str | None:
    """The GUID (as a string) of the desktop `hwnd` lives on, or None."""
    mgr = _manager()
    if mgr is None:
        return None
    try:
        return str(mgr.GetWindowDesktopId(hwnd))
    except Exception:
        return None


def same_desktop(hwnd_a, hwnd_b) -> bool | None:
    """Whether two windows share a desktop -- the check that tells you two tasks
    can occlude each other. None if undeterminable."""
    a, b = desktop_id(hwnd_a), desktop_id(hwnd_b)
    if a is None or b is None:
        return None
    return a == b


# -- keystroke actions (stable across builds, unlike the internal COM API) -----


def _send_hotkey(*keys: str) -> bool:
    """Press a chord via pyautogui. Best-effort: returns False if input can't be
    sent at all, so callers report an honest failure rather than assuming."""
    try:
        import pyautogui

        pyautogui.hotkey(*keys)
        return True
    except Exception:
        return False


def new_desktop() -> bool:
    """Create a new virtual desktop AND switch to it (Win+Ctrl+D)."""
    return _send_hotkey("win", "ctrl", "d")


def switch_right() -> bool:
    """Move to the next virtual desktop (Win+Ctrl+Right)."""
    return _send_hotkey("win", "ctrl", "right")


def switch_left() -> bool:
    """Move to the previous virtual desktop (Win+Ctrl+Left)."""
    return _send_hotkey("win", "ctrl", "left")


def close_desktop() -> bool:
    """Close the current virtual desktop (Win+Ctrl+F4). Its windows move to the
    neighbouring desktop rather than closing."""
    return _send_hotkey("win", "ctrl", "f4")


# -- the composed operations open/ actually calls ------------------------------


def ensure_visible(hwnd, *, max_switches: int = 8, settle: float = _SWITCH_SETTLE_S) -> bool:
    """Make sure `hwnd`'s desktop is the one on screen, so a capture of that
    window is really that window. Confirmed via COM after each switch.

    Returns False (honestly) when virtual desktops aren't usable here, so the
    caller can fall back to plain raise-then-capture on a single desktop.
    """
    mgr = _manager()
    if mgr is None:
        return False

    def on_current() -> bool:
        try:
            return bool(mgr.IsWindowOnCurrentVirtualDesktop(hwnd))
        except Exception:
            return False

    return find_desktop_with(on_current, switch_right, max_switches=max_switches, settle=settle)


def move_to_current_desktop(hwnd, anchor_hwnd) -> bool:
    """Move `hwnd` onto the desktop that's on screen now.

    MoveWindowToDesktop needs a desktop GUID, and there is no public call for
    "the current desktop's id" -- so we read it off `anchor_hwnd`, any window
    already known to be on the current desktop (the caller's own window is the
    reliable choice, since a window it just created is always on the current
    desktop). Fully within the documented interface, no build-specific IIDs.
    """
    mgr = _manager()
    if mgr is None:
        return False
    try:
        target_desktop = mgr.GetWindowDesktopId(anchor_hwnd)
        mgr.MoveWindowToDesktop(hwnd, target_desktop)
        return True
    except Exception:
        return False
