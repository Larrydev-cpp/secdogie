"""Whether the person asked their computer for less motion.

The companion breathes, and its cards rise as they fade in. Someone who turned
on "reduce motion" (macOS Accessibility, Windows "Show animations", GNOME
"Animations") gets a still light and plain cross-fades instead.

Qt 6.10 and later report the preference itself; older Qt, and platforms where
Qt reports nothing, fall back to reading the same setting from the OS.
``SECDOGIE_REDUCE_MOTION=1`` (or ``0``) overrides both. Read once when the
window opens.
"""
from __future__ import annotations

import os
import subprocess
import sys

ENV = "SECDOGIE_REDUCE_MOTION"


def _run(cmd: list[str]) -> str:
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=2.0, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def _windows_animations_off() -> bool:
    import ctypes
    from ctypes import wintypes

    SPI_GETCLIENTAREAANIMATION = 0x1042
    on = wintypes.BOOL(True)
    ok = ctypes.windll.user32.SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(on), 0)
    return bool(ok) and not on.value


def os_prefers_less_motion(platform: str = sys.platform, run=_run, windows=_windows_animations_off) -> bool:
    """The OS setting, read the way each desktop stores it. Anything unreadable
    counts as "no preference"."""
    if platform == "darwin":
        return run(["defaults", "read", "com.apple.universalaccess", "reduceMotion"]) == "1"
    if platform == "win32":
        try:
            return windows()
        except (OSError, AttributeError, ValueError):
            return False
    return run(["gsettings", "get", "org.gnome.desktop.interface", "enable-animations"]) == "false"


def qt_prefers_less_motion(app) -> bool | None:
    """Qt's own reading (Qt 6.10+), or None where this Qt cannot tell."""
    try:
        hints = app.styleHints()
        accessibility = hints.accessibility()
        preference = accessibility.motionPreference()
    except AttributeError:
        return None
    return getattr(preference, "name", str(preference)) == "ReducedMotion"


def reduce_motion(app=None, *, env=None, os_check=os_prefers_less_motion) -> bool:
    env = os.environ if env is None else env
    override = env.get(ENV, "").strip()
    if override in ("0", "1"):
        return override == "1"
    if app is not None and qt_prefers_less_motion(app):
        return True
    return os_check()


__all__ = ["ENV", "os_prefers_less_motion", "qt_prefers_less_motion", "reduce_motion"]
