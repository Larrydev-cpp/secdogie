import io

from PIL import Image
from secdogie_agent import screen
from secdogie_agent.providers.base import Action


def _png(w, h):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


def _solid(shade, w=200, h=150):
    buf = io.BytesIO()
    Image.new("L", (w, h), shade).save(buf, format="PNG")
    return buf.getvalue()


def _solid_with_patch(shade, patch_shade, box, w=200, h=150):
    img = Image.new("L", (w, h), shade)
    x0, y0, x1, y1 = box
    for y in range(y0, y1):
        for x in range(x0, x1):
            img.putpixel((x, y), patch_shade)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_large_image_is_downscaled_and_scale_is_exact():
    png = _png(2560, 1440)
    out, (mw, mh), scale = screen.prepare_for_model(png, (2560, 1440), max_edge=1568)
    assert max(mw, mh) == 1568          # longest edge capped
    assert mw == 1568 and mh == 882     # aspect ratio preserved
    # scale maps model coords back to real pixels
    assert abs(scale - 2560 / 1568) < 1e-9
    # a model coord at the far right maps back near the real right edge
    assert round(mw * scale) == 2560


def test_small_image_is_not_upscaled():
    png = _png(1280, 720)
    out, (mw, mh), scale = screen.prepare_for_model(png, (1280, 720), max_edge=1568)
    assert (mw, mh) == (1280, 720)
    assert scale == 1.0


def test_grid_overlay_still_valid_png_and_same_size():
    png = _png(1000, 800)
    out, (mw, mh), scale = screen.prepare_for_model(png, (1000, 800), max_edge=1568, grid=True)
    assert (mw, mh) == (1000, 800)
    # output remains a decodable PNG of the expected dimensions
    img = Image.open(io.BytesIO(out))
    assert img.size == (1000, 800)


def test_action_scaled_maps_coordinates():
    a = Action.from_dict({"action": "left_click", "x": 100, "y": 50})
    b = a.scaled(2.0)
    assert (b.x, b.y) == (200, 100)
    assert b.raw["x"] == 200 and b.raw["y"] == 100
    # original is unchanged
    assert (a.x, a.y) == (100, 50)


def test_action_scaled_leaves_scroll_amounts_alone():
    a = Action.from_dict({"action": "scroll", "x": 10, "y": 20, "dx": 3, "dy": -3})
    b = a.scaled(2.0)
    assert (b.x, b.y) == (20, 40)     # position scaled
    assert (b.dx, b.dy) == (3, -3)    # scroll amount not scaled


def test_action_scaled_identity_returns_self():
    a = Action.from_dict({"action": "left_click", "x": 5, "y": 5})
    assert a.scaled(1.0) is a


def test_action_translated_shifts_positions_only():
    a = Action.from_dict({"action": "drag", "x": 10, "y": 20, "to_x": 30, "to_y": 40})
    b = a.translated(100, 200)
    assert (b.x, b.y) == (110, 220)
    assert (b.to_x, b.to_y) == (130, 240)
    assert b.raw["x"] == 110 and b.raw["to_x"] == 130
    assert (a.x, a.y) == (10, 20)  # original unchanged


def test_action_translated_leaves_scroll_amounts_alone():
    a = Action.from_dict({"action": "scroll", "x": 10, "y": 20, "dx": 3, "dy": -3})
    b = a.translated(5, 5)
    assert (b.x, b.y) == (15, 25)
    assert (b.dx, b.dy) == (3, -3)


def test_action_translated_identity_returns_self():
    a = Action.from_dict({"action": "left_click", "x": 5, "y": 5})
    assert a.translated(0, 0) is a


# -- changed_ratio (post-action visual verification primitive) ----------------

def test_changed_ratio_identical_frames_is_zero():
    frame = _solid(120)
    assert screen.changed_ratio(frame, frame) == 0.0


def test_changed_ratio_black_to_white_is_near_one():
    assert screen.changed_ratio(_solid(0), _solid(255)) > 0.99


def test_changed_ratio_small_patch_is_small_and_below_default_threshold():
    # A ~10% area patch that flips well past the tolerance -> a small ratio,
    # below the loop's default verify_threshold (0.005) would be too strict, so
    # confirm it lands in a sensible small-but-nonzero range.
    before = _solid(30)
    after = _solid_with_patch(30, 220, box=(0, 0, 60, 45))  # 60*45 / (200*150) = 9%
    ratio = screen.changed_ratio(before, after)
    assert 0.05 < ratio < 0.15


def test_changed_ratio_ignores_sub_tolerance_noise():
    # A shift of a few gray levels (below `tol`) should read as no change, so a
    # static screen with mild compression shimmer isn't mistaken for activity.
    assert screen.changed_ratio(_solid(100), _solid(108), tol=16) == 0.0


def test_changed_ratio_mismatched_sizes_counts_as_fully_changed():
    assert screen.changed_ratio(_solid(100, w=200, h=150), _solid(100, w=100, h=100)) == 1.0


def test_capture_screenshot_region_grabs_only_that_box(monkeypatch):
    import mss

    grabbed = {}

    class FakeShot:
        rgb = b"\x00" * (50 * 40 * 3)
        size = (50, 40)

    class FakeSct:
        monitors = [{"left": 0, "top": 0, "width": 1920, "height": 1080}]

        def grab(self, monitor):
            grabbed["monitor"] = monitor
            return FakeShot()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mss, "mss", lambda: FakeSct())

    png, size = screen.capture_screenshot(region=(100, 200, 50, 40))
    assert grabbed["monitor"] == {"left": 100, "top": 200, "width": 50, "height": 40}
    assert size == (50, 40)
    assert png  # real mss.tools.to_png ran against the fake capture and produced bytes


def test_capture_screenshot_no_region_uses_primary_monitor(monkeypatch):
    import mss

    grabbed = {}

    class FakeShot:
        rgb = b"\x00" * (10 * 10 * 3)
        size = (10, 10)

    class FakeSct:
        monitors = [
            {"left": 0, "top": 0, "width": 3000, "height": 1000},  # index 0: "all monitors" combined
            {"left": 0, "top": 0, "width": 10, "height": 10},  # index 1: primary
        ]

        def grab(self, monitor):
            grabbed["monitor"] = monitor
            return FakeShot()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mss, "mss", lambda: FakeSct())

    screen.capture_screenshot()
    assert grabbed["monitor"] == FakeSct.monitors[1]  # picks the primary monitor, not the combined one
