"""Screenshot capture. Uses mss, which works on X11, Windows and macOS
(Wayland support depends on the compositor / portal support in mss)."""
from __future__ import annotations


class NoDisplayError(RuntimeError):
    """Raised when there is no graphical session to screenshot -- e.g. running
    over SSH to a headless box, or inside a container with no X display."""


def capture_screenshot() -> tuple[bytes, tuple[int, int]]:
    """Returns (png_bytes, (width, height)) for the primary monitor."""
    import mss
    import mss.tools

    try:
        with mss.mss() as sct:
            monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            shot = sct.grab(monitor)
            png_bytes = mss.tools.to_png(shot.rgb, shot.size)
            return png_bytes, (shot.size[0], shot.size[1])
    except Exception as e:
        # mss raises assorted backend-specific errors (X connection failures,
        # etc.) when there's no usable display; normalize them into one clear
        # message rather than dumping a backend traceback on the user.
        raise NoDisplayError(
            "could not capture a screenshot: no graphical display is available. "
            "secdogie-agent controls a desktop, so it must run in a graphical "
            "session (not over plain SSH to a headless machine). "
            f"underlying error: {e}"
        ) from e
