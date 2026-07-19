import pytest
from secdogie_scene3d.tiles import (
    Detection,
    Tile,
    clamp_box,
    iou,
    local_box_to_global,
    local_point_to_global,
    merge_detections,
    plan_tiles,
)

# -- plan_tiles ---------------------------------------------------------

def test_plan_tiles_3x3_no_overlap_exact_cells():
    tiles = plan_tiles(2160, 1440, cols=3, rows=3, overlap=0.0)
    assert len(tiles) == 9
    # row-major order
    assert [(t.col, t.row) for t in tiles] == [(c, r) for r in range(3) for c in range(3)]
    # each cell is exactly 720x480 with no padding
    assert all(t.w == 720 and t.h == 480 for t in tiles)
    top_left = tiles[0]
    assert (top_left.x, top_left.y) == (0, 0)
    bottom_right = tiles[-1]
    assert bottom_right.box == (1440, 960, 2160, 1440)


def test_plan_tiles_overlap_grows_interior_and_clamps_edges():
    tiles = plan_tiles(2160, 1440, cols=3, rows=3, overlap=0.12)
    by_pos = {(t.col, t.row): t for t in tiles}
    # edge tiles stay pinned to the image bounds (no padding off-canvas)
    assert by_pos[(0, 0)].x == 0 and by_pos[(0, 0)].y == 0
    assert by_pos[(2, 2)].box[2] == 2160 and by_pos[(2, 2)].box[3] == 1440
    # the center tile is padded on all sides, so it's larger than a bare cell
    center = by_pos[(1, 1)]
    assert center.w > 720 and center.h > 480
    # ...but every tile stays comfortably under a typical model cap
    assert all(max(t.w, t.h) < 1568 for t in tiles)


def test_plan_tiles_overlap_covers_every_pixel():
    tiles = plan_tiles(1000, 800, cols=3, rows=3, overlap=0.1)
    # union of tile boxes must cover the whole image (no gaps between cells)
    covered_right = max(t.box[2] for t in tiles)
    covered_bottom = max(t.box[3] for t in tiles)
    assert covered_right == 1000 and covered_bottom == 800
    # adjacent columns must actually overlap or at least touch (no seam gap)
    row0 = sorted((t for t in tiles if t.row == 0), key=lambda t: t.col)
    for left, right in zip(row0, row0[1:], strict=False):  # adjacent pairs; last has no right neighbour
        assert left.box[2] >= right.x  # left tile's right edge reaches the next tile's start


def test_plan_tiles_single_tile():
    tiles = plan_tiles(800, 600, cols=1, rows=1)
    assert len(tiles) == 1
    assert tiles[0].box == (0, 0, 800, 600)


@pytest.mark.parametrize("kw", [
    {"width": 0, "height": 100},
    {"width": 100, "height": -1},
    {"width": 100, "height": 100, "cols": 0},
    {"width": 100, "height": 100, "overlap": 0.5},
    {"width": 100, "height": 100, "overlap": -0.1},
])
def test_plan_tiles_rejects_bad_args(kw):
    with pytest.raises(ValueError):
        plan_tiles(**kw)


# -- coordinate translation ---------------------------------------------------------

def test_local_box_to_global_adds_tile_offset():
    tile = Tile(col=1, row=2, x=700, y=900, w=760, h=540)
    assert local_box_to_global(tile, (10, 20, 60, 40)) == (710, 920, 760, 940)


def test_local_point_to_global_adds_tile_offset():
    tile = Tile(col=1, row=1, x=700, y=480, w=760, h=540)
    assert local_point_to_global(tile, 30, 40) == (730, 520)


def test_clamp_box_clips_to_image_bounds():
    assert clamp_box((-5, -5, 2200, 1500), 2160, 1440) == (0, 0, 2160, 1440)
    assert clamp_box((10, 10, 50, 50), 2160, 1440) == (10, 10, 50, 50)


# -- iou ---------------------------------------------------------

def test_iou_identical_is_one():
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0


def test_iou_disjoint_is_zero():
    assert iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0


def test_iou_half_overlap():
    # two 10x10 boxes sharing a 5x10 strip -> inter 50, union 150
    assert iou((0, 0, 10, 10), (5, 0, 15, 10)) == pytest.approx(50 / 150)


# -- merge_detections ---------------------------------------------------------

def _det(box, label="x", conf=0.5, tiles=()):
    return Detection(box=box, label=label, confidence=conf, tiles=tiles)


def test_merge_collapses_overlapping_same_element_and_unions_tiles():
    a = _det((100, 100, 140, 120), label="Export", conf=0.9, tiles=((0, 0),))
    b = _det((102, 101, 141, 121), label="Export", conf=0.6, tiles=((1, 0),))
    merged = merge_detections([a, b], iou_threshold=0.5)
    assert len(merged) == 1
    # the higher-confidence detection anchors box + label; both tiles recorded
    assert merged[0].label == "Export" and merged[0].confidence == 0.9
    assert set(merged[0].tiles) == {(0, 0), (1, 0)}


def test_merge_keeps_distinct_elements():
    a = _det((0, 0, 20, 20), label="A")
    b = _det((400, 400, 420, 420), label="B")
    merged = merge_detections([a, b])
    assert {d.label for d in merged} == {"A", "B"}


def test_merge_is_confidence_first():
    low = _det((0, 0, 40, 20), label="low", conf=0.3, tiles=((0, 0),))
    high = _det((1, 1, 41, 21), label="high", conf=0.95, tiles=((1, 0),))
    merged = merge_detections([low, high], iou_threshold=0.5)
    assert len(merged) == 1
    assert merged[0].label == "high"  # strongest detection anchors the cluster


def test_merge_empty_returns_empty():
    assert merge_detections([]) == []
