"""macOS window capture via Quartz `CGWindowListCreateImage`.

Why this exists: on macOS the accessibility tree is the primary perception
path, and a screenshot is only sent to the model when the tree cannot describe
the content (a canvas, a game, owner-drawn chrome) or the model asks to `look`.
The old fallback grabbed the whole display with `mss`, which on macOS

  * captures every window, not the one the model is working on, and
  * without the Screen Recording (TCC) permission silently returns a black /
    wallpaper-only frame -- the model then reasons about nothing.

This module captures a **single on-screen window** by its `CGWindowID` (the same
primitive `native/atlas` already uses for its pixel-diff verify) and raises an
*actionable* error when Screen Recording is denied, instead of feeding the model
a blank frame. macOS renders windows compositor-side, so the window image cannot
be reconstructed from process memory (see native/atlas memory_inspector.h) --
this API is the way to get it.

Everything is lazy: importing this module never requires pyobjc, so it is inert
off macOS. The window-selection logic (`choose_window`) is pure and unit-tested;
the Quartz calls are exercised on a real Mac. Callers pass `quartz=` / `encode_png=`
to test the capture flow without pyobjc.
"""
from __future__ import annotations

import io
import sys
from typing import Any

from .screen import CaptureError

_DENIED_MSG = (
    "macOS Screen Recording permission is required to capture a window image. "
    "Grant it in System Settings -> Privacy & Security -> Screen Recording for "
    "the app running secdogie (Terminal / your build), then restart it. "
    "(The accessibility tree still works without this; only pixel capture needs it.)"
)


class ScreenRecordingDenied(CaptureError):
    """Raised when a window image could not be captured because macOS Screen
    Recording (TCC) permission is not granted. A CaptureError subclass, so the
    agent loop surfaces it and exits cleanly rather than sending a blank frame."""


def _load_quartz() -> Any:
    if sys.platform != "darwin":
        raise RuntimeError("mac_capture is only available on macOS")
    try:
        import Quartz
    except ImportError as e:  # pragma: no cover - exercised on macOS
        raise RuntimeError(
            "Quartz (pyobjc) is not installed; pip install pyobjc-framework-Quartz"
        ) from e
    return Quartz


def list_windows(*, quartz: Any = None) -> list[dict]:
    """On-screen, non-desktop windows, front-to-back, as plain dicts:
    {id, pid, layer, title, owner, alpha, bounds:{X,Y,Width,Height}}."""
    q = quartz or _load_quartz()
    opts = q.kCGWindowListOptionOnScreenOnly | q.kCGWindowListExcludeDesktopElements
    raw = q.CGWindowListCopyWindowInfo(opts, q.kCGNullWindowID) or []
    out: list[dict] = []
    for w in raw:
        bounds = dict(w.get("kCGWindowBounds") or {})
        out.append(
            {
                "id": int(w.get("kCGWindowNumber", 0)),
                "pid": int(w.get("kCGWindowOwnerPID", 0)),
                "layer": int(w.get("kCGWindowLayer", 0)),
                "title": w.get("kCGWindowName") or "",
                "owner": w.get("kCGWindowOwnerName") or "",
                "alpha": float(w.get("kCGWindowAlpha", 1.0)),
                "bounds": {
                    "X": float(bounds.get("X", 0)),
                    "Y": float(bounds.get("Y", 0)),
                    "Width": float(bounds.get("Width", 0)),
                    "Height": float(bounds.get("Height", 0)),
                },
            }
        )
    return out


def choose_window(windows: list[dict], *, pid: int | None = None, title: str | None = None) -> dict | None:
    """Pick the target window from a `list_windows()` result. Pure.

    Keeps normal windows (layer 0, visible, non-degenerate size), optionally
    restricted to an owner `pid` and/or a `title` substring, then returns the
    frontmost survivor (the list is front-to-back). None if nothing qualifies,
    so the caller can fall back to a whole-screen grab."""
    cands = [
        w
        for w in windows
        if w.get("layer", 0) == 0
        and w.get("alpha", 1.0) > 0
        and w.get("bounds", {}).get("Width", 0) >= 1
        and w.get("bounds", {}).get("Height", 0) >= 1
    ]
    if pid is not None:
        cands = [w for w in cands if w.get("pid") == pid]
    if title:
        needle = title.lower()
        titled = [w for w in cands if needle in (w.get("title") or "").lower()]
        if titled:
            cands = titled
    return cands[0] if cands else None


def titles_readable(windows: list[dict]) -> bool:
    """True if any normal window exposes a title. Without Screen Recording,
    macOS blanks other apps' window names, so all-empty titles is a hint that
    permission is missing -- used only to enrich a log line, never for control."""
    return any((w.get("title") or "") for w in windows if w.get("layer", 0) == 0)


def _cgimage_to_png(img: Any, width: int, height: int, quartz: Any) -> bytes:  # pragma: no cover - macOS only
    from PIL import Image

    provider = quartz.CGImageGetDataProvider(img)
    data = bytes(quartz.CGDataProviderCopyData(provider))
    bpr = int(quartz.CGImageGetBytesPerRow(img))
    # CGWindowListCreateImage yields premultiplied BGRA, little-endian.
    pil = Image.frombuffer("RGBA", (width, height), data, "raw", "BGRA", bpr, 1)
    out = io.BytesIO()
    pil.convert("RGB").save(out, format="PNG", compress_level=1)
    return out.getvalue()


def capture_window_png(window_id: int, *, quartz: Any = None, encode_png=None) -> tuple[bytes, tuple[int, int]]:
    """Capture one window by CGWindowID as (png_bytes, (width, height)).

    Raises ScreenRecordingDenied if the image comes back null/empty, which is
    what a missing Screen Recording grant produces."""
    q = quartz or _load_quartz()
    img = q.CGWindowListCreateImage(
        q.CGRectNull,
        q.kCGWindowListOptionIncludingWindow,
        window_id,
        q.kCGWindowImageBoundsIgnoreFraming,
    )
    if img is None:
        raise ScreenRecordingDenied(_DENIED_MSG)
    width = int(q.CGImageGetWidth(img))
    height = int(q.CGImageGetHeight(img))
    if width == 0 or height == 0:
        raise ScreenRecordingDenied(_DENIED_MSG)
    enc = encode_png or _cgimage_to_png
    return enc(img, width, height, q), (width, height)


def capture_target_window_png(
    *, pid: int | None = None, title: str | None = None, quartz: Any = None, encode_png=None
) -> tuple[bytes, tuple[int, int]] | None:
    """Capture the frontmost matching on-screen window as (png, (w, h)).

    Returns None when no window matches (caller falls back to whole-screen);
    raises ScreenRecordingDenied when capture is blocked by TCC."""
    q = quartz or _load_quartz()
    win = choose_window(list_windows(quartz=q), pid=pid, title=title)
    if win is None:
        return None
    return capture_window_png(win["id"], quartz=q, encode_png=encode_png)
