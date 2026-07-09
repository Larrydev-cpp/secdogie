"""Enumerates open, controllable windows on the desktop.

This is the "split the screen" half of running several secdogie-agent
sessions in parallel, one per window: instead of one agent driving the
whole primary monitor, each detected window becomes its own region that a
separate agent loop can be scoped to (see runner.py).
"""
from __future__ import annotations

from dataclasses import dataclass

# Windows smaller than this on an edge are almost never something a user
# wants to hand a task to (tooltips, tray icons, docks); filtering them keeps
# the picker list to real application windows.
MIN_EDGE = 60


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
