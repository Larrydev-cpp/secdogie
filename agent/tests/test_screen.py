import io

from PIL import Image

from secdogie_agent import screen
from secdogie_agent.providers.base import Action


def _png(w, h):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 20, 30)).save(buf, format="PNG")
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
