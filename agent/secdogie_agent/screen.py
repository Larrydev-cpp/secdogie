"""Screenshot capture and preparation for the vision model.

The key accuracy trick lives here: vision models reason about a *downscaled*
copy of any large screenshot, so the pixel coordinates they emit only line up
with the real screen if we control the scaling ourselves. `prepare_for_model`
resizes the capture to a known size, and `scale` is the exact factor to map
the model's coordinates back to real screen pixels (see loop.py).

When a click misses or the model asks for a closer look, `prepare_fovea`
sends a native-resolution crop around the miss instead of upscaling the whole
frame -- same local precision, far fewer tokens.

Latency note: the image sent to the model is JPEG by default (~5–15× smaller
than PNG at the same resolution). Capture for verification / macros stays PNG.
Default long-edge is 1536 (was 1280) so dense UI / CAD detail remains usable;
raise further with --max-image-edge when a region truly needs magnification.
"""
from __future__ import annotations

import io
from dataclasses import dataclass

# Long-edge cap for the image sent to the model.
# 1536 balances detail (CAD labels, dense UI, fine click targets) against
# token/upload cost. JPEG keeps payloads small even at this size; for extreme
# magnification / dense engineering drawings raise further with
# --max-image-edge 1920 (or higher). Previous defaults were 1568 then 1280.
DEFAULT_MAX_EDGE = 1536

# JPEG quality for model-bound frames. 85 keeps small text/icons and CAD
# annotations readable while remaining compact; raise via
# prepare_for_model(quality=...) or a higher --max-image-edge when a region
# truly needs more magnification.
DEFAULT_JPEG_QUALITY = 85

# Side length of a foveated (native-res) crop sent instead of boosting the
# whole frame. 768 px is enough for CAD dimension text / dense icons; drop
# to 512 via --fovea-edge when token budget is the tighter constraint.
DEFAULT_FOVEA_EDGE = 768


class CaptureError(RuntimeError):
    """A screenshot could not be taken. Backends raise this (or a subclass)
    so the agent loop can end cleanly on any capture failure -- a headless
    desktop, or a phone that isn't reachable over adb -- instead of crashing."""


class NoDisplayError(CaptureError):
    """Raised when there is no graphical session to screenshot -- e.g. running
    over SSH to a headless box, or inside a container with no X display."""


def capture_screenshot(
    region: tuple[int, int, int, int] | None = None,
) -> tuple[bytes, tuple[int, int]]:
    """Returns (png_bytes, (width, height)).

    With no `region`, captures the primary monitor. With `region` given as
    (left, top, width, height) in absolute screen pixels, captures only that
    box -- e.g. one window out of several, when several are being driven in
    parallel. The returned size is the box's own size; loop.py adds the
    region's (left, top) back onto any resulting action coordinates before
    execution, since pyautogui always acts in absolute screen coordinates.
    """
    import mss
    import mss.tools

    try:
        with mss.mss() as sct:
            if region is not None:
                left, top, width, height = region
                monitor = {"left": left, "top": top, "width": width, "height": height}
            else:
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


def primary_size() -> tuple[int, int]:
    """(width, height) of the primary monitor, in absolute pixels.

    Used to clamp a small capture region against the screen edge -- the reflex
    layer captures a window *around* a moving target, and mss.grab errors (or
    returns garbage) if that window pokes past the monitor bounds.
    """
    import mss

    with mss.mss() as sct:
        m = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        return m["width"], m["height"]


def media_type_for(image_bytes: bytes) -> str:
    """Sniff JPEG vs PNG so providers set the correct content-type / media_type."""
    if image_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return "image/png"


def changed_ratio(before_png: bytes, after_png: bytes, max_edge: int = 256, tol: int = 16) -> float:
    """Fraction (0..1) of pixels that visibly changed between two screenshots.

    A finer signal than an exact hash: the loop uses it to answer "did that
    action actually do anything?" -- a click that changed nothing likely missed
    (wrong target, unfocused window, blocked UI). Both frames are converted to
    grayscale and shrunk to `max_edge` on the long side (so the compare is a few
    milliseconds, not a full-resolution pass), then counted as changed where the
    absolute per-pixel difference exceeds `tol` (which absorbs JPEG/anti-alias
    noise so a static screen reads as 0, not a nonzero shimmer).

    Mismatched sizes (e.g. a resolution change) count as fully changed -- that
    is itself a large visible change, which is the right answer here.
    """
    from PIL import Image, ImageChops

    def _small_gray(png: bytes) -> Image.Image:
        with Image.open(io.BytesIO(png)) as img:
            g = img.convert("L")
            longest = max(g.width, g.height)
            if longest > max_edge:
                factor = max_edge / longest
                g = g.resize((max(1, round(g.width * factor)), max(1, round(g.height * factor))))
            return g.copy()

    a = _small_gray(before_png)
    b = _small_gray(after_png)
    if a.size != b.size:
        return 1.0

    # Per-pixel |a-b|, thresholded at tol, then counted via the histogram -- all
    # in PIL's C core, so no per-pixel Python loop and no numpy dependency.
    mask = ImageChops.difference(a, b).point(lambda p: 255 if p > tol else 0)
    changed = mask.histogram()[255]
    total = a.width * a.height
    return changed / total if total else 0.0


def crop_anchor(frame_png: bytes, cx: int, cy: int, box: int = 64) -> tuple[bytes, int, int]:
    """Cut a small grayscale patch out of `frame_png` around (cx, cy) -- a visual
    fingerprint of the element being clicked, so a macro can re-find it later by
    matching the patch instead of trusting a fixed coordinate (see macro.py).

    Returns (grayscale PNG bytes, offset_x, offset_y) where the offset is where
    (cx, cy) sits *inside* the returned patch. The window is clamped to the frame,
    so near an edge the click is no longer the patch center -- the offset records
    that, and lets replay map a re-found patch back to the true click point. PIL
    only (no numpy), so recording an anchor never needs the reflex extra.
    """
    from PIL import Image

    with Image.open(io.BytesIO(frame_png)) as img:
        gray = img.convert("L")
        w, h = gray.width, gray.height
        # A box larger than the frame just takes the whole frame.
        bw, bh = min(box, w), min(box, h)
        left = max(0, min(cx - bw // 2, w - bw))
        top = max(0, min(cy - bh // 2, h - bh))
        patch = gray.crop((left, top, left + bw, top + bh))
        out = io.BytesIO()
        patch.save(out, format="PNG")
        return out.getvalue(), cx - left, cy - top


@dataclass(frozen=True)
class Fovea:
    """A native-resolution crop sent to the model instead of a downscaled frame.

    `image` / `model_size` / `scale` are what `prepare_for_model` would return
    for the crop alone. `origin` is the crop's top-left in the *capture*
    (so `Action.translated(*origin)` maps model coords back to capture space).
    `size` is the crop in capture pixels; `anchor` is the requested center.
    """

    image: bytes
    model_size: tuple[int, int]
    scale: float
    origin: tuple[int, int]
    size: tuple[int, int]
    anchor: tuple[int, int]


def fovea_box(
    cx: int,
    cy: int,
    frame_w: int,
    frame_h: int,
    edge: int = DEFAULT_FOVEA_EDGE,
) -> tuple[int, int, int, int]:
    """Clamp a box of side `edge` around `(cx, cy)` inside the frame.

    Returns `(left, top, width, height)`. A frame smaller than `edge` yields
    the whole frame -- the same clamp `crop_anchor` uses.
    """
    if frame_w < 1 or frame_h < 1:
        return 0, 0, max(1, frame_w), max(1, frame_h)
    bw = min(max(1, int(edge)), frame_w)
    bh = min(max(1, int(edge)), frame_h)
    left = max(0, min(int(cx) - bw // 2, frame_w - bw))
    top = max(0, min(int(cy) - bh // 2, frame_h - bh))
    return left, top, bw, bh


def prepare_fovea(
    png_bytes: bytes,
    real_size: tuple[int, int],
    cx: int,
    cy: int,
    edge: int = DEFAULT_FOVEA_EDGE,
    *,
    grid: bool = False,
    format: str = "jpeg",
    quality: int = DEFAULT_JPEG_QUALITY,
) -> Fovea:
    """Crop a native-resolution patch around `(cx, cy)` and prepare it for the model.

    This is the token-cheap alternative to raising the whole-frame `max_edge`:
    a 768×768 native crop of a 2560×1440 CAD view is far fewer pixels than a
    1920-long-edge full frame, and the model sees the *actual* pixels around
    the miss instead of a still-downscaled whole screen.

    The crop itself is never downscaled (native pixels are the point).
    Coordinates the model emits are in the crop's own space; add `origin`
    (`Action.translated`) to map them back to the capture.
    """
    from PIL import Image

    real_w, real_h = real_size
    left, top, bw, bh = fovea_box(cx, cy, real_w, real_h, edge)
    with Image.open(io.BytesIO(png_bytes)) as img:
        # If the PNG size disagrees with real_size (shouldn't), prefer the
        # actual image so the crop stays in-bounds.
        iw, ih = img.size
        if (iw, ih) != (real_w, real_h):
            left, top, bw, bh = fovea_box(cx, cy, iw, ih, edge)
        crop = img.convert("RGB").crop((left, top, left + bw, top + bh))
        buf = io.BytesIO()
        crop.save(buf, format="PNG")
        crop_png = buf.getvalue()
        crop_size = crop.size

    # Never downscale the fovea -- native pixels are the whole point.
    model_bytes, model_size, scale = prepare_for_model(
        crop_png,
        crop_size,
        max_edge=max(crop_size),
        grid=grid,
        format=format,
        quality=quality,
    )
    return Fovea(
        image=model_bytes,
        model_size=model_size,
        scale=scale,
        origin=(left, top),
        size=crop_size,
        anchor=(int(cx), int(cy)),
    )


def prepare_for_model(
    png_bytes: bytes,
    real_size: tuple[int, int],
    max_edge: int = DEFAULT_MAX_EDGE,
    grid: bool = False,
    format: str = "jpeg",
    quality: int = DEFAULT_JPEG_QUALITY,
) -> tuple[bytes, tuple[int, int], float]:
    """Resize the screenshot so its longest edge is at most `max_edge`,
    optionally draw a labeled reference grid, and return
    (model_image_bytes, model_size, scale).

    `scale` maps model-space coordinates back to real screen pixels:
        real_x = round(model_x * scale)
    Aspect ratio is preserved, so a single scalar is exact for both axes.

    Default `format` is JPEG (quality 85): much smaller than PNG for the same
    resolution, which dominates end-to-end latency on vision API calls. Use
    format="png" when lossless is required (tests, grid debugging). For parts
    that need extra magnification, pass a larger max_edge (e.g. 1920).
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
    fmt = (format or "jpeg").lower()
    if fmt in ("jpg", "jpeg"):
        # Grid overlays stay readable at quality 85; avoid progressive JPEG so
        # providers that only accept baseline images keep working.
        img.save(out, format="JPEG", quality=max(40, min(95, int(quality))), optimize=True)
    else:
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
