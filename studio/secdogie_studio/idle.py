"""Seconds since the last input anywhere: the OS's own idle counter, nothing more.

This is the one attention signal the companion uses (``presence.Attention``).
It is a single number the OS already keeps for screen savers and power
management. Studio never sees which keys were pressed, which window has
focus, or anything on the screen. Hooking the keyboard or recording input
would be a keylogger, and that is on the forbidden list
(``tests/test_purity.py`` checks for it).

  macOS    CGEventSourceSecondsSinceLastEventType (CoreGraphics)
  Windows  GetLastInputInfo
  Linux    the X11 screen-saver extension (XScreenSaverQueryInfo); Wayland
           offers no such counter, so it returns None there

``None`` means "unknown": the companion then surfaces gently instead of
guessing whether you are busy.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import sys
import threading

_lock = threading.Lock()
_x11 = None  # (xlib, xss, display, root) once opened; False when unavailable


def _macos() -> float | None:
    path = ctypes.util.find_library("ApplicationServices") or ctypes.util.find_library("CoreGraphics")
    if not path:
        return None
    cg = ctypes.CDLL(path)
    fn = cg.CGEventSourceSecondsSinceLastEventType
    fn.restype = ctypes.c_double
    fn.argtypes = [ctypes.c_int32, ctypes.c_uint32]
    combined_session_state, any_input_event = 0, 0xFFFFFFFF
    return max(0.0, float(fn(combined_session_state, any_input_event)))


def _windows() -> float | None:
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):  # type: ignore[attr-defined]
        return None
    now = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF  # type: ignore[attr-defined]
    return ((now - info.dwTime) & 0xFFFFFFFF) / 1000.0


class _XScreenSaverInfo(ctypes.Structure):
    _fields_ = [("window", ctypes.c_ulong), ("state", ctypes.c_int), ("kind", ctypes.c_int),
                ("til_or_since", ctypes.c_ulong), ("idle", ctypes.c_ulong), ("eventMask", ctypes.c_ulong)]


def _linux() -> float | None:
    global _x11
    if _x11 is None:
        _x11 = False
        xlib_path, xss_path = ctypes.util.find_library("X11"), ctypes.util.find_library("Xss")
        if xlib_path and xss_path:
            xlib, xss = ctypes.CDLL(xlib_path), ctypes.CDLL(xss_path)
            xlib.XOpenDisplay.restype = ctypes.c_void_p
            xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
            xlib.XDefaultRootWindow.restype = ctypes.c_ulong
            xlib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
            xss.XScreenSaverAllocInfo.restype = ctypes.POINTER(_XScreenSaverInfo)
            xss.XScreenSaverQueryInfo.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_XScreenSaverInfo)]
            display = xlib.XOpenDisplay(None)
            if display:
                _x11 = (xss, display, xlib.XDefaultRootWindow(display), xss.XScreenSaverAllocInfo())
    if not _x11:
        return None
    xss, display, root, info = _x11
    if not xss.XScreenSaverQueryInfo(display, root, info):
        return None
    return info.contents.idle / 1000.0


def idle_seconds() -> float | None:
    """Seconds since the last keyboard or pointer input, or None if unknown."""
    with _lock:
        try:
            if sys.platform == "darwin":
                return _macos()
            if sys.platform == "win32":
                return _windows()
            return _linux()
        except (OSError, AttributeError, ValueError):
            return None


__all__ = ["idle_seconds"]
