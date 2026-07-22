"""Process DPI awareness -- the fix for clicks landing in the wrong place on a
scaled (125% / 150%) or mixed-DPI multi-monitor Windows display.

The whole agent is one coordinate pipeline: mss captures the screen, the model
picks an (x, y) on that image, screen.prepare_for_model maps it back to real
pixels, and pyautogui clicks there. That chain is self-consistent only if the
process sees ONE coordinate space. A DPI-*unaware* Windows process does not: the
OS virtualizes it -- mss can hand back physical pixels while pyautogui's
SetCursorPos speaks logical pixels -- so on a 150% display the cursor lands at
2/3 of where the model aimed, and on a second monitor at a different scale it is
wrong in yet another way. Nothing downstream can correct for this after the
fact; it has to be declared once, up front, before any window or capture exists.

So `ensure_dpi_awareness()` is called at the very top of the CLI, before the
tkinter menu, the first mss capture, or pyautogui. It asks Windows for
Per-Monitor-Aware v2 -- every monitor reported in its own physical pixels, which
is what keeps the mss->model->pyautogui mapping exact across monitors and scale
changes -- and falls back through the older awareness APIs on Windows versions
that lack the newest call. It is:

  * a no-op off Windows (X11/Quartz already give mss + pyautogui physical pixels),
  * idempotent (safe to call from every entry point; only the first call acts --
    and the FIRST declaration in a process wins on Windows anyway), and
  * exception-proof (a failure to set awareness must never stop the app).
"""
from __future__ import annotations

import sys

# The outcome of the one real attempt, cached so repeat calls are no-ops and
# tests can introspect/reset it. None = not attempted yet.
_STATUS: str | None = None


def _apply_windows() -> str:
    """Try the DPI-awareness APIs newest-first; return a label for the first that
    sticks, or "unavailable" if none do. Each call is guarded because the newer
    entry points don't exist on older Windows (AttributeError) and a second
    declaration in the same process is rejected (OSError) -- both mean "move on".

    Order and constants:
      SetProcessDpiAwarenessContext(-4)  PER_MONITOR_AWARE_V2   Win10 1703+
      shcore.SetProcessDpiAwareness(2)   PER_MONITOR_AWARE       Win8.1+
      user32.SetProcessDPIAware()        system-aware (legacy)   Vista+
    """
    import ctypes

    # PER_MONITOR_AWARE_V2 -- the ideal: per-monitor, and correct across DPI
    # changes and non-client areas. The context handle is passed by value as a
    # pseudo-handle (-4); ctypes wants it as a void* sized int.
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return "per-monitor-v2"
    except (AttributeError, OSError):
        pass

    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return "per-monitor"
    except (AttributeError, OSError):
        pass

    try:
        # Legacy, system-DPI-aware: right on a single monitor, imperfect on a
        # mixed-DPI multi-monitor setup, but still far better than unaware.
        if ctypes.windll.user32.SetProcessDPIAware():
            return "system"
    except (AttributeError, OSError):
        pass

    return "unavailable"


def ensure_dpi_awareness(_apply=None) -> str:
    """Declare this process DPI-aware (see module docstring). Returns a short
    status label for logging/tests: "per-monitor-v2" | "per-monitor" | "system"
    | "unavailable" | "not-windows" | "already-set". Idempotent and never raises.

    `_apply` injects the platform layer for tests; production uses the real
    Windows call path.
    """
    global _STATUS
    if _STATUS is not None:
        return "already-set"
    if not sys.platform.startswith("win"):
        _STATUS = "not-windows"
        return _STATUS
    apply = _apply or _apply_windows
    try:
        _STATUS = apply()
    except Exception:
        # The safety net's safety net: awareness is an optimization, never a
        # reason the program fails to start.
        _STATUS = "unavailable"
    return _STATUS


def current_status() -> str | None:
    """The cached outcome, or None if ensure_dpi_awareness() hasn't run yet."""
    return _STATUS
