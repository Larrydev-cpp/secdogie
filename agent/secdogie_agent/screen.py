"""Screenshot capture and preparation for the vision model.

The key accuracy trick lives here: vision models reason about a *downscaled*
copy of any large screenshot, so the pixel coordinates they emit only line up
with the real screen if we control the scaling ourselves. `prepare_for_model`
resizes the capture to a known size, and `scale` is the exact factor to map
the model's coordinates back to real screen pixels (see loop.py)."""
from __future__ import annotations

import io

# Long-edge cap for the image sent to the model. ~1568 matches the size large
# images get internally reduced to anyway, so we lose no detail the model would
# have kept, while gaining an exact, known coordinate mapping.
DEFAULT_MAX_EDGE = 1568


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


def logical_screen_size() -> tuple[int, int] | None:
    """The OS *logical* screen size pyautogui uses for its coordinates.

    On HiDPI / Retina displays this differs from the captured pixel size: the
    screenshot comes back in physical pixels (e.g. 2880x1800) while pyautogui
    moves/clicks in logical points (e.g. 1440x900). Mapping the model's
    coordinates to *this* size, rather than the physical capture size, is what
    keeps clicks landing on target on scaled displays. Returns None when
    pyautogui can't be imported or can't read the display (headless / dry-run),
    in which case the caller falls back to the physical-pixel scale."""
    try:
        import pyautogui

        w, h = pyautogui.size()
        if w > 0 and h > 0:
            return (int(w), int(h))
    except Exception:
        pass
    return None


def prepare_for_model(
    png_bytes: bytes,
    real_size: tuple[int, int],
    max_edge: int = DEFAULT_MAX_EDGE,
    grid: bool = False,
) -> tuple[bytes, tuple[int, int], float]:
    """Resize the screenshot so its longest edge is at most `max_edge`,
    optionally draw a labeled reference grid, and return
    (model_png, model_size, scale).

    `scale` maps model-space coordinates back to real screen pixels:
        real_x = round(model_x * scale)
    Aspect ratio is preserved, so a single scalar is exact for both axes.
    """
    from PIL import Image

    real_w, real_h = real_size
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")

    longest = max(real_w, real_h)
    if longest > max_edge:
        factor = max_edge / longest  # < 1, we are shrinking
        model_w = max(1, round(real_w * factor))
        model_h = max(1, round(real_h * factor))
        img = img.resize((model_w, model_h), Image.LANCZOS)
    else:
        model_w, model_h = real_w, real_h

    if grid:
        _draw_grid(img)

    # Use the actual resized dimensions to compute scale, so rounding in the
    # resize can't desync the mapping.
    scale = real_w / img.width
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue(), (img.width, img.height), scale


def _draw_grid(img, step: int = 100) -> None:
    """Overlays faint gridlines with coordinate labels every `step` model-pixels
    to give the model concrete anchor points for estimating coordinates."""
    from PIL import ImageDraw

    draw = ImageDraw.Draw(img, "RGBA")
    line = (255, 0, 0, 70)
    label = (255, 0, 0, 200)
    w, h = img.width, img.height
    for x in range(step, w, step):
        draw.line([(x, 0), (x, h)], fill=line, width=1)
        draw.text((x + 2, 2), str(x), fill=label)
    for y in range(step, h, step):
        draw.line([(0, y), (w, y)], fill=line, width=1)
        draw.text((2, y + 2), str(y), fill=label)
