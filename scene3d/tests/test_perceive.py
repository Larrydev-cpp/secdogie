import io
import json
import re

import pytest
from conftest import FakeModel
from PIL import Image
from secdogie_scene3d.perceive import perceive_screen
from secdogie_scene3d.tiles import plan_tiles


def _png(w, h):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (30, 30, 40)).save(buf, format="PNG")
    return buf.getvalue()


def _tile_pos(user):
    m = re.search(r"column (\d+), row (\d+)", user)
    return (int(m.group(1)), int(m.group(2)))


def _elements(*els):
    return json.dumps({"elements": list(els)})


# -- tiling + translation ---------------------------------------------------------

def test_perceive_tiles_image_and_translates_to_global_coords():
    # Every tile reports one element at its own local (10,10)-(20,20); with no
    # overlap the 9 tiles are disjoint, so we get 9 distinct global elements.
    model = FakeModel(lambda s, u, img: _elements(
        {"label": "e", "type": "button", "box": [10, 10, 20, 20], "confidence": 0.9}
    ))
    img = _png(300, 300)
    perc = perceive_screen(img, model, cols=3, rows=3, overlap=0.0)

    assert perc.width == 300 and perc.height == 300
    assert len(perc.elements) == 9
    # each tile offset is (col*100, row*100); its element lands at offset+(10..20)
    got = {tuple(e.box) for e in perc.elements}
    want = {(c * 100 + 10, r * 100 + 10, c * 100 + 20, r * 100 + 20) for r in range(3) for c in range(3)}
    assert got == want
    assert all(len(e.tiles) == 1 for e in perc.elements)
    assert model.calls and len(model.calls) == 9  # one describe() per tile


def test_perceive_captures_a_failing_tile_without_sinking_the_rest():
    def reply(s, u, img):
        if _tile_pos(u) == (1, 1):
            raise RuntimeError("rate limited")
        return _elements({"label": "ok", "box": [5, 5, 15, 15], "confidence": 0.8})

    perc = perceive_screen(_png(300, 300), FakeModel(reply), cols=3, rows=3, overlap=0.0)
    assert len(perc.errors) == 1
    assert "rate limited" in perc.errors[0]
    assert len(perc.elements) == 8  # the other eight tiles still produced elements


def test_perceive_merges_the_same_element_reported_by_two_overlapping_tiles():
    # Put one element at a fixed global point that falls inside the overlap of
    # tiles (0,0) and (1,0); each of those tiles reports it (in its own local
    # coords), everyone else reports nothing. After translation + merge it must
    # collapse to a single element carrying both tiles.
    tiles = {(t.col, t.row): t for t in plan_tiles(600, 600, cols=3, rows=3, overlap=0.2)}
    gx1, gy1, gx2, gy2 = 190, 40, 210, 60  # straddles the col-0/col-1 seam near x=200
    shared = {(0, 0), (1, 0)}

    def reply(s, u, img):
        pos = _tile_pos(u)
        if pos not in shared:
            return _elements()
        t = tiles[pos]
        local = [gx1 - t.x, gy1 - t.y, gx2 - t.x, gy2 - t.y]
        return _elements({"label": "Export", "box": local, "confidence": 0.7})

    perc = perceive_screen(_png(600, 600), FakeModel(reply), cols=3, rows=3, overlap=0.2)
    assert len(perc.elements) == 1
    el = perc.elements[0]
    assert el.label == "Export"
    assert set(el.tiles) == shared  # both tiles are recorded as having seen it
    assert el.box == (gx1, gy1, gx2, gy2)


def test_perceive_spreads_tiles_over_the_model_pool():
    # Each model tags its element with its own name; tile results stay in order,
    # so tile i must have been handled by pool[i % len(pool)].
    pool = [FakeModel(lambda s, u, img, n=n: _elements({"label": n, "box": [1, 1, 2, 2]}), name=n)
            for n in ("k0", "k1")]
    perc = perceive_screen(_png(300, 300), pool, cols=3, rows=3, overlap=0.0)
    handled = [t.detections[0].label for t in perc.tiles]
    assert handled == ["k0", "k1", "k0", "k1", "k0", "k1", "k0", "k1", "k0"]


def test_perceive_single_tile_is_the_whole_image():
    model = FakeModel(_elements({"label": "whole", "box": [0, 0, 50, 50]}))
    perc = perceive_screen(_png(400, 300), model, cols=1, rows=1)
    assert len(perc.tiles) == 1
    assert perc.tiles[0].tile.box == (0, 0, 400, 300)
    assert perc.elements[0].box == (0, 0, 50, 50)


def test_perceive_requires_a_model():
    with pytest.raises(ValueError):
        perceive_screen(_png(100, 100), [], cols=2, rows=2)


def test_perceive_tolerates_unparseable_tile_output():
    perc = perceive_screen(_png(200, 200), FakeModel("not json at all"), cols=2, rows=2, overlap=0.0)
    assert len(perc.elements) == 0
    assert len(perc.errors) == 4  # every tile failed to parse, all captured
