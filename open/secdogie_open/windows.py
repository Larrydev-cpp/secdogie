"""Enumerates open, controllable windows on the desktop.

This is the "split the screen" half of running several secdogie-agent
sessions in parallel, one per window: instead of one agent driving the
whole primary monitor, each detected window becomes its own region that a
separate agent loop can be scoped to (see runner.py).
"""
from __future__ import annotations

import time
from dataclasses import dataclass

# Windows smaller than this on an edge are almost never something a user
# wants to hand a task to (tooltips, tray icons, docks); filtering them keeps
# the picker list to real application windows.
MIN_EDGE = 60

# How long focus_window() polls for activation to actually take effect before
# giving up (some window managers ignore or delay programmatic activation).
_FOCUS_TIMEOUT_S = 0.3
_FOCUS_POLL_S = 0.02


class NoWindowBackendError(RuntimeError):
    """pywinctl isn't installed, or the display server won't enumerate
    windows (e.g. Wayland, which blocks window listing for isolation)."""


@dataclass(frozen=True)
class WindowInfo:
    id: str  # stable for one enumeration pass: title + geometry
    title: str
    left: int
    top: int
    width: int
    height: int

    @property
    def region(self) -> tuple[int, int, int, int]:
        """(left, top, width, height), the shape loop.AgentConfig.region wants."""
        return (self.left, self.top, self.width, self.height)


def list_windows() -> list[WindowInfo]:
    """Returns visible, non-minimized, titled windows, sorted by title.

    Raises NoWindowBackendError if windows can't be listed at all (missing
    dependency, or a Wayland session where this is blocked by design) --
    callers should show that message rather than an empty, silently-wrong list.
    """
    try:
        import pywinctl
    except ImportError as e:
        raise NoWindowBackendError(
            "window enumeration needs the pywinctl package. Install it with "
            "`pip install pywinctl` (it's a listed dependency of secdogie-open, "
            "so a plain `pip install -e .` should already have pulled it in)."
        ) from e
    except Exception as e:
        # On Linux, pywinctl's own import chain (pymonctl -> ewmhlib -> Xlib)
        # eagerly opens an X11 connection at import time, not just when you
        # call into it -- so a headless/Wayland-only session can raise here
        # (e.g. Xlib.error.DisplayNameError), before getAllWindows() is even
        # reached. Route it through the same NoWindowBackendError as a failed
        # getAllWindows() call below, since it's the same underlying cause.
        raise NoWindowBackendError(
            "could not initialize window enumeration on this display. On Linux this needs an "
            "X11 session (Wayland blocks window enumeration for isolation "
            f"reasons). underlying error: {e}"
        ) from e

    try:
        raw_windows = pywinctl.getAllWindows()
    except Exception as e:
        raise NoWindowBackendError(
            "could not list windows on this display. On Linux this needs an "
            "X11 session (Wayland blocks window enumeration for isolation "
            f"reasons). underlying error: {e}"
        ) from e

    out: list[WindowInfo] = []
    for w in raw_windows:
        try:
            if not w.isVisible or w.isMinimized:
                continue
            title = (w.title or "").strip()
            if not title or w.width < MIN_EDGE or w.height < MIN_EDGE:
                continue
            out.append(
                WindowInfo(
                    id=f"{title}:{w.left},{w.top},{w.width},{w.height}",
                    title=title,
                    left=w.left,
                    top=w.top,
                    width=w.width,
                    height=w.height,
                )
            )
        except Exception:
            continue  # a window can close mid-enumeration; skip it, don't fail the whole list

    return sorted(out, key=lambda win: win.title.lower())


def focus_window(win: WindowInfo, timeout: float = _FOCUS_TIMEOUT_S, poll_interval: float = _FOCUS_POLL_S) -> bool:
    """Bring `win` to the foreground and confirm it actually took focus before
    returning, so a caller acting on it next (a click, typed text) lands there
    and not on whatever window the OS still had focused.

    Best-effort, not a hard gate: some window managers ignore or delay
    programmatic activation, and a closed/moved window just means we can't
    find a match. Either way this returns False rather than raising -- the
    caller is expected to proceed with its action regardless (see
    runner.py's use as a Backend.activate hook), since refusing to act at all
    would be worse than acting against whatever currently has focus.
    """
    try:
        import pywinctl
    except Exception:
        return False

    try:
        all_windows = pywinctl.getAllWindows()
    except Exception:
        return False

    # Prefer an exact geometry match (the window hasn't moved since it was
    # listed); fall back to matching by title alone, since geometry can drift
    # a little under some window managers/decorations.
    exact = [
        w for w in all_windows
        if getattr(w, "title", None) == win.title
        and (w.left, w.top, w.width, w.height) == (win.left, win.top, win.width, win.height)
    ]
    by_title = [w for w in all_windows if getattr(w, "title", None) == win.title]
    candidates = exact or by_title
    if not candidates:
        return False
    target = candidates[0]

    try:
        target.activate(wait=False)
    except Exception:
        return False

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if target.isActive:
                return True
        except Exception:
            return False
        time.sleep(poll_interval)
    return False
