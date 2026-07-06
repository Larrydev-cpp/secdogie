"""Screenshot capture. Uses mss, which works on X11, Windows and macOS
(Wayland support depends on the compositor / portal support in mss)."""
from __future__ import annotations


def capture_screenshot() -> tuple[bytes, tuple[int, int]]:
    """Returns (png_bytes, (width, height)) for the primary monitor."""
    import mss
    import mss.tools

    with mss.mss() as sct:
        monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        shot = sct.grab(monitor)
        png_bytes = mss.tools.to_png(shot.rgb, shot.size)
        return png_bytes, (shot.size[0], shot.size[1])
