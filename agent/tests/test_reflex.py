import io
import sys
import types

import pytest

np = pytest.importorskip("numpy")  # the reflex layer's optional dependency

from PIL import Image  # noqa: E402
from secdogie_agent import reflex  # noqa: E402

# A 24x24 patch with strong structure on both axes, so it has real variance
# (a flat patch would have zero variance and no meaningful NCC peak).
_YY, _XX = np.mgrid[0:24, 0:24]
PATCH = ((_XX * 10 + _YY * 7) % 256).astype(np.uint8)
PATCH_GRAY = PATCH.astype(np.float32)


def _scene_png(px, py, w=120, h=100):
    """PNG of a `w`x`h` frame (flat gray) with PATCH placed at (px, py)."""
    arr = np.full((h, w), 30, np.uint8)
    arr[py:py + 24, px:px + 24] = PATCH
    buf = io.BytesIO()
    Image.fromarray(arr, "L").save(buf, format="PNG")
    return buf.getvalue()


# -- match_template ---------------------------------------------------------

def test_match_template_finds_the_patch_center_with_high_score():
    frame = reflex.png_to_gray(_scene_png(40, 30))
    m = reflex.match_template(frame, PATCH_GRAY)
    assert m is not None
    assert (m.cx, m.cy) == (40 + 12, 30 + 12)  # top-left + half the template
    assert m.score > 0.99  # an exact copy correlates ~1.0


def test_match_template_returns_none_when_template_bigger_than_frame():
    tiny = np.full((10, 10), 30, np.float32)  # smaller than the 24x24 template
    assert reflex.match_template(tiny, PATCH_GRAY) is None


def test_match_template_returns_none_below_min_score():
    # A frame with no patch at all (flat) -> no structured match to find.
    flat = np.full((100, 120), 30, np.float32)
    assert reflex.match_template(flat, PATCH_GRAY, min_score=0.5) is None


def test_match_template_is_brightness_invariant():
    # NCC is normalized, so the same patch dimmed/brightened still matches.
    frame = reflex.png_to_gray(_scene_png(20, 20))
    m = reflex.match_template(frame, PATCH_GRAY * 0.5 + 40)
    assert m is not None and (m.cx, m.cy) == (32, 32) and m.score > 0.99


# -- refine_point (coarse fuzzy detection -> precise native-res localization) --

def test_refine_point_pins_a_fuzzy_detection_to_the_true_center():
    # Target at a known place; a "model" gives a coarse point off by ~30px.
    tx, ty = 60, 45
    frame = reflex.png_to_gray(_scene_png(tx, ty, w=200, h=150))
    true_center = (tx + 12, ty + 12)  # PATCH is 24x24
    coarse = (true_center[0] + 25, true_center[1] - 18)  # fuzzy, ~31px off

    r = reflex.refine_point(frame, coarse, PATCH_GRAY, window=96)
    assert r.refined and r.score > 0.99
    assert (r.x, r.y) == true_center  # snapped exactly onto the target
    # and it is much closer to the truth than the coarse guess was
    coarse_err = ((coarse[0] - true_center[0]) ** 2 + (coarse[1] - true_center[1]) ** 2) ** 0.5
    refined_err = ((r.x - true_center[0]) ** 2 + (r.y - true_center[1]) ** 2) ** 0.5
    assert refined_err < coarse_err / 5


def test_refine_point_falls_back_when_target_not_in_window():
    # The coarse point is nowhere near the target -> the window holds no match,
    # so refine returns the coarse point untouched rather than jumping.
    frame = reflex.png_to_gray(_scene_png(20, 20, w=200, h=150))
    coarse = (180, 130)  # far from the patch at (20,20)
    r = reflex.refine_point(frame, coarse, PATCH_GRAY, window=64, min_score=0.5)
    assert not r.refined and (r.x, r.y) == coarse


def test_refine_point_falls_back_when_template_bigger_than_window():
    frame = reflex.png_to_gray(_scene_png(60, 45, w=200, h=150))
    r = reflex.refine_point(frame, (72, 57), PATCH_GRAY, window=16)  # 16 < 24 template
    assert not r.refined and (r.x, r.y) == (72, 57)


def test_refine_point_clamps_window_at_the_frame_edge():
    # A coarse point near the corner still works: the window is clamped inside.
    tx, ty = 4, 4
    frame = reflex.png_to_gray(_scene_png(tx, ty, w=200, h=150))
    r = reflex.refine_point(frame, (tx + 12 + 10, ty + 12 + 8), PATCH_GRAY, window=96)
    assert r.refined and (r.x, r.y) == (tx + 12, ty + 12)


# -- pursue ---------------------------------------------------------

def _capturer(positions):
    """A capture() that walks through `positions`, holding on the last one
    (so a settled target keeps being found)."""
    frames = [_scene_png(px, py) for px, py in positions]
    i = {"n": 0}

    def capture():
        idx = min(i["n"], len(frames) - 1)
        i["n"] += 1
        return frames[idx]

    return capture


def test_pursue_tracks_then_clicks_a_settling_target():
    # Moves for three frames, then stops at (60, 40); once it holds still for
    # stable_frames, pursue clicks it -- all locally, no model calls.
    positions = [(20, 20), (40, 30), (60, 40), (60, 40), (60, 40), (60, 40), (60, 40)]
    moves, clicks = [], []
    result = reflex.pursue(
        _capturer(positions), lambda x, y: moves.append((x, y)), lambda x, y: clicks.append((x, y)),
        PATCH_GRAY, stable_frames=3, stable_radius=4, timeout_s=100, max_fps=0,
    )
    assert result.outcome == "clicked"
    assert clicks == [(72, 52)]  # (60+12, 40+12) center of the settled patch
    assert moves[-1] == (72, 52)  # cursor was tracking it every frame
    assert result.center == (72, 52)


def test_pursue_gives_up_as_lost_when_target_disappears():
    flat = np.full((100, 120), 30, np.uint8)
    buf = io.BytesIO()
    Image.fromarray(flat, "L").save(buf, format="PNG")
    blank = buf.getvalue()

    result = reflex.pursue(
        lambda: blank, lambda x, y: None, lambda x, y: None,
        PATCH_GRAY, lost_frames=4, timeout_s=100, max_fps=0,
    )
    assert result.outcome == "lost"
    assert result.frames == 4


def test_pursue_times_out_on_a_target_that_never_settles():
    # Target moves every frame, so it never becomes stable; a fake clock makes
    # timeout_s elapse deterministically without real waiting.
    positions = [(10, 10), (30, 10), (50, 10), (70, 10), (90, 10)]  # in-frame, always moving
    ticks = iter(float(n) for n in range(1000))
    result = reflex.pursue(
        _capturer(positions), lambda x, y: None, lambda x, y: None,
        PATCH_GRAY, stable_frames=100, lost_frames=100, timeout_s=3.0, max_fps=0,
        clock=lambda: next(ticks),
    )
    assert result.outcome == "timeout"


def test_pursue_stops_when_should_stop_is_set():
    positions = [(10, 10), (30, 10), (50, 10), (70, 10)]  # in-frame, always moving
    calls = {"n": 0}

    def should_stop():
        calls["n"] += 1
        return calls["n"] > 3

    result = reflex.pursue(
        _capturer(positions), lambda x, y: None, lambda x, y: None,
        PATCH_GRAY, stable_frames=100, timeout_s=100, max_fps=0, should_stop=should_stop,
    )
    assert result.outcome == "stopped"


def test_pursue_reports_fps_and_frame_count():
    ticks = iter(float(n) for n in range(0, 1000))  # 1s per clock() call
    result = reflex.pursue(
        _capturer([(50, 50)] * 10), lambda x, y: None, lambda x, y: None,
        PATCH_GRAY, stable_frames=3, timeout_s=1e9, max_fps=0, clock=lambda: next(ticks),
    )
    assert result.outcome == "clicked"
    assert result.frames >= 4 and result.fps > 0


# -- track_click_target (the model-facing desktop wrapper) ------------------

def test_track_click_target_clamps_region_and_clicks_absolute(monkeypatch):
    # The target sits still at absolute (500, 400); the wrapper must build a
    # search window around it, track it locally, and click in ABSOLUTE screen
    # coordinates (region origin added back).
    import secdogie_agent.screen as screen_mod

    frame = np.full((200, 200), 30, np.uint8)
    frame[88:112, 88:112] = PATCH  # region-local, inside the cropped template box
    buf = io.BytesIO()
    Image.fromarray(frame, "L").save(buf, format="PNG")
    frame_png = buf.getvalue()

    regions = []

    def fake_capture(region=None):
        regions.append(region)
        return frame_png, (200, 200)

    monkeypatch.setattr(screen_mod, "primary_size", lambda: (1920, 1080))
    monkeypatch.setattr(screen_mod, "capture_screenshot", fake_capture)

    moves, clicks = [], []
    fake_pg = types.ModuleType("pyautogui")
    fake_pg.moveTo = lambda x, y, duration=0: moves.append((x, y))
    fake_pg.click = lambda *a, **k: clicks.append(moves[-1])
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pg)

    result = reflex.track_click_target(500, 400, timeout_s=5.0)

    assert regions[0] == (400, 300, 200, 200)  # centered on the target, clamped in-bounds
    assert clicks == [(500, 400)]              # clicked the absolute target, not region-local
    assert "clicked at (500, 400)" in result


def test_track_click_target_caps_the_timeout(monkeypatch):
    # A model asking for a huge chase must be capped so it can't hold the shared
    # input lock forever; the wrapper clamps timeout_s to MAX_TRACK_SECONDS.
    import secdogie_agent.screen as screen_mod

    monkeypatch.setattr(screen_mod, "primary_size", lambda: (800, 600))
    monkeypatch.setattr(screen_mod, "capture_screenshot", lambda region=None: (b"", (200, 200)))

    seen = {}

    def fake_pursue(capture, move, click, template, **kw):
        seen.update(kw)
        return reflex.PursueResult("lost", 1, 0.0, 0.0, None)

    monkeypatch.setattr(reflex, "pursue", fake_pursue)
    # png_to_gray/crop still run on the first capture; stub the decode too.
    monkeypatch.setattr(reflex, "png_to_gray", lambda png: np.zeros((200, 200), np.float32))
    monkeypatch.setitem(sys.modules, "pyautogui", types.ModuleType("pyautogui"))

    reflex.track_click_target(100, 100, timeout_s=9999.0)
    assert seen["timeout_s"] == reflex.MAX_TRACK_SECONDS
