"""The fast local reflex layer -- the other half of the two-tier design.

A cloud vision model runs at roughly 1 Hz (1-3 s per call). That is fine for
deciding *what* to do, but hopeless for anything that has to keep up with a
60 Hz screen: by the time a screenshot has round-tripped to the model and back,
16+ frames have already gone by. So the model stays the slow *planner* and hands
a tight, goal-directed loop down to this module, which closes it **locally** --
grab a small region, match a target template, move/click -- at whatever frame
rate the machine allows, with *no* network call per frame. It returns control to
the model only when the goal is met, the target is lost, or a timeout hits.

This is the standard split (slow planner + fast controller) every real-time
control system uses; it is not an attempt to make the LLM itself fast, which is
impossible.

Template matching uses numpy, an optional dependency:
    pip install 'secdogie-agent[reflex]'
"""
from __future__ import annotations

import io
import math
import time
from collections.abc import Callable
from dataclasses import dataclass


def _require_numpy():
    try:
        import numpy as np
    except ImportError as e:
        raise RuntimeError(
            "the reflex layer needs numpy for template matching. Install it with "
            "`pip install 'secdogie-agent[reflex]'` (or `pip install numpy`)."
        ) from e
    return np


def png_to_gray(png: bytes):
    """Decode PNG bytes to a 2D float32 grayscale numpy array (0..255)."""
    np = _require_numpy()
    from PIL import Image

    with Image.open(io.BytesIO(png)) as img:
        gray = img.convert("L")
        return np.asarray(gray, dtype=np.float32)


@dataclass(frozen=True)
class Match:
    cx: int  # center of the matched template, in frame pixels
    cy: int
    score: float  # normalized cross-correlation peak in [-1, 1]; 1 == identical


def _xcorr_valid(np, frame, kernel):
    """Cross-correlation of `frame` with `kernel` over the 'valid' region (kernel
    fully inside the frame), via FFT -- O(N log N), not O(N * kernel). Returns
    an array of shape (fh-kh+1, fw-kw+1)."""
    fh, fw = frame.shape
    kh, kw = kernel.shape
    sh, sw = fh + kh - 1, fw + kw - 1  # full linear-correlation size, no FFT wraparound
    F = np.fft.rfft2(frame, s=(sh, sw))
    # Correlation == convolution with the flipped kernel.
    K = np.fft.rfft2(kernel[::-1, ::-1], s=(sh, sw))
    full = np.fft.irfft2(F * K, s=(sh, sw))
    return full[kh - 1:fh, kw - 1:fw]


def match_template(frame_gray, template_gray, min_score: float = 0.0) -> Match | None:
    """Find `template_gray` inside `frame_gray` by normalized cross-correlation.

    Returns the best match's center + peak score, or None if the template is
    bigger than the frame or the peak is below `min_score`. NCC is contrast/
    brightness-invariant, so a target still matches under moderate lighting or
    alpha changes. Uses FFT cross-correlation plus integral-image window
    statistics (the standard fast normxcorr2), so a match over a bounded search
    region is a few milliseconds -- fast enough to run every frame."""
    np = _require_numpy()

    frame = np.asarray(frame_gray, dtype=np.float64)
    template = np.asarray(template_gray, dtype=np.float64)
    fh, fw = frame.shape
    th, tw = template.shape
    if th > fh or tw > fw:
        return None

    n = th * tw
    t0 = template - template.mean()
    t_norm = math.sqrt(float((t0 * t0).sum()))
    if t_norm < 1e-9:
        return None  # a flat template has no structure to localize

    # Numerator: sum over each window of frame*t0. Since t0 is zero-mean this is
    # exactly the cross-correlation of the frame with t0.
    numerator = _xcorr_valid(np, frame, t0)

    # Per-window frame variance via summed-area tables (integral images): one
    # O(N) pass gives every window's sum and sum-of-squares.
    s = np.pad(frame.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    sq = np.pad((frame * frame).cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    win_sum = s[th:, tw:] - s[:-th, tw:] - s[th:, :-tw] + s[:-th, :-tw]
    win_sq = sq[th:, tw:] - sq[:-th, tw:] - sq[th:, :-tw] + sq[:-th, :-tw]
    win_var_sumsq = np.maximum(win_sq - (win_sum * win_sum) / n, 0.0)
    denom = np.sqrt(win_var_sumsq) * t_norm

    ncc = np.where(denom > 1e-9, numerator / np.where(denom > 1e-9, denom, 1.0), 0.0)

    best = int(np.argmax(ncc))
    score = float(ncc.flat[best])
    if score < min_score:
        return None
    r, c = np.unravel_index(best, ncc.shape)
    return Match(cx=int(c) + tw // 2, cy=int(r) + th // 2, score=score)


@dataclass(frozen=True)
class PursueResult:
    outcome: str  # "clicked" | "lost" | "timeout" | "stopped"
    frames: int
    elapsed_s: float
    fps: float
    center: tuple[int, int] | None  # last known target center, in frame pixels


def _fps(frames: int, elapsed: float) -> float:
    return frames / elapsed if elapsed > 0 else 0.0


def pursue(
    capture: Callable[[], bytes],
    move: Callable[[int, int], None],
    click: Callable[[int, int], None],
    template_gray,
    *,
    min_score: float = 0.6,
    stable_radius: float = 4.0,
    stable_frames: int = 5,
    lost_frames: int = 10,
    timeout_s: float = 5.0,
    max_fps: float = 60.0,
    should_stop: Callable[[], bool] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> PursueResult:
    """Track a target locally and click it once it settles -- no model calls.

    Each frame: `capture()` a region (PNG), match `template_gray`, and if found,
    `move(cx, cy)` the cursor onto it. When the target has stayed within
    `stable_radius` pixels for `stable_frames` frames (it stopped moving),
    `click(cx, cy)` and return. Give up as "lost" after `lost_frames` frames
    with no match, or "timeout" after `timeout_s`. `should_stop` (checked each
    frame) lets a caller cancel. `clock`/`sleep` are injectable so tests run
    without real time passing.

    Coordinates are in the captured region's own pixels; a desktop caller adds
    the region's origin before moving/clicking (see track_and_click_desktop)."""
    start = clock()
    min_dt = 1.0 / max_fps if max_fps and max_fps > 0 else 0.0
    frames = 0
    lost = 0
    stable = 0
    last_center: tuple[int, int] | None = None

    while True:
        if should_stop is not None and should_stop():
            return PursueResult("stopped", frames, clock() - start, _fps(frames, clock() - start), last_center)
        now = clock()
        if now - start >= timeout_s:
            return PursueResult("timeout", frames, now - start, _fps(frames, now - start), last_center)

        m = match_template(png_to_gray(capture()), template_gray, min_score=min_score)
        frames += 1

        if m is None:
            lost += 1
            stable = 0
            if lost >= lost_frames:
                return PursueResult("lost", frames, clock() - start, _fps(frames, clock() - start), last_center)
        else:
            lost = 0
            move(m.cx, m.cy)
            if last_center is not None and math.hypot(m.cx - last_center[0], m.cy - last_center[1]) <= stable_radius:
                stable += 1
            else:
                stable = 0
            last_center = (m.cx, m.cy)
            if stable >= stable_frames:
                click(m.cx, m.cy)
                el = clock() - start
                return PursueResult("clicked", frames, el, _fps(frames, el), last_center)

        if min_dt:
            spent = clock() - now
            if spent < min_dt:
                sleep(min_dt - spent)


def track_and_click_desktop(region, target, box=(48, 48), **pursue_kwargs) -> PursueResult:
    """Desktop wiring for pursue(): crop a template around `target` (an absolute
    screen (x, y)) from an initial capture of `region` (left, top, width,
    height), then chase and click it locally. Captures via mss, moves/clicks via
    pyautogui with zero glide so tracking can keep up. Returns pursue()'s result.

    Kept thin on purpose -- the reusable logic is match_template/pursue above;
    this only maps region-local matches back to absolute screen coordinates."""
    import pyautogui

    from . import screen

    left, top, _w, _h = region
    tx, ty = target
    bw, bh = box

    first_png, _size = screen.capture_screenshot(region=region)
    first = png_to_gray(first_png)
    # Crop the template around the target in region-local coords, clamped in.
    fh, fw = first.shape
    lx, lyy = tx - left, ty - top
    x0 = max(0, min(lx - bw // 2, fw - bw))
    y0 = max(0, min(lyy - bh // 2, fh - bh))
    template = first[y0:y0 + bh, x0:x0 + bw]

    def capture() -> bytes:
        png, _s = screen.capture_screenshot(region=region)
        return png

    def move(cx: int, cy: int) -> None:
        pyautogui.moveTo(left + cx, top + cy, duration=0)

    def click(cx: int, cy: int) -> None:
        pyautogui.moveTo(left + cx, top + cy, duration=0)
        pyautogui.click()

    return pursue(capture, move, click, template, **pursue_kwargs)


# Cap on how long a single track_click may chase (and hold the shared input
# lock) before giving up -- a model asking for a huge timeout must not be able
# to freeze every other desktop actor.
MAX_TRACK_SECONDS = 30.0


def track_click_target(x: int, y: int, *, search: int = 200, timeout_s: float | None = None) -> str:
    """Model-facing reflex entry: the target is *currently* near absolute screen
    point (x, y) and moving. Capture a `search`x`search` window around it and
    locally track+click it (pursue) at frame rate -- no model call per frame.
    Returns a one-line result for the agent history. Desktop only.

    The search window is deliberately small (fast matching) and clamped to the
    monitor; if the target escapes it, pursue reports "lost" and control returns
    to the model, which re-locates it. That graceful give-up is why the window
    doesn't need to be the whole screen."""
    from . import screen

    sw, sh = screen.primary_size()
    width = min(search, sw)
    height = min(search, sh)
    left = max(0, min(x - width // 2, sw - width))
    top = max(0, min(y - height // 2, sh - height))
    region = (left, top, width, height)

    kwargs: dict = {}
    if timeout_s is not None:
        kwargs["timeout_s"] = min(timeout_s, MAX_TRACK_SECONDS)
    result = track_and_click_desktop(region, (x, y), **kwargs)

    # pursue reports the center in the captured region's own pixels; map it back
    # to absolute screen coordinates so the history the model reads matches the
    # (x, y) space it asked in.
    if result.center is not None:
        abs_center = (left + result.center[0], top + result.center[1])
        where = f" at {abs_center}"
    else:
        where = ""
    return (
        f"reflex track near ({x}, {y}): {result.outcome}{where} "
        f"after {result.frames} frame(s) [{result.fps:.0f} fps]"
    )
