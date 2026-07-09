"""Pure geometry for high-resolution tiled perception.

A vision model downscales a large screenshot before it reasons about it (the
agent caps the long edge at ~1568px), so on a 2160x1440 screen small controls --
a timeline clip, a toolbar icon in a video editor -- shrink below what the model
can read, and it mis-locates them. The fix is to cut the frame into a grid of
smaller tiles, each already under the model's cap so it goes in at native
resolution, analyze each tile separately (concurrently, one per pooled key),
then stitch the per-tile findings back into one map in full-image coordinates.

This module is just the math: plan the grid, translate a tile-local box back to
global pixels, and merge boxes that two overlapping tiles both reported. It has
no image, model, or network dependency, so it unit-tests without either.
"""
from __future__ import annotations

from dataclasses import dataclass

Box = tuple[int, int, int, int]  # (x1, y1, x2, y2), top-left origin, x2/y2 exclusive-ish


@dataclass(frozen=True)
class Tile:
    col: int
    row: int
    x: int  # left offset of this tile within the full image
    y: int  # top offset
    w: int  # tile width in pixels
    h: int  # tile height

    @property
    def box(self) -> Box:
        return (self.x, self.y, self.x + self.w, self.y + self.h)


def plan_tiles(width: int, height: int, cols: int = 3, rows: int = 3, overlap: float = 0.12) -> list[Tile]:
    """Split a width x height image into a cols x rows grid, each cell grown by
    `overlap` (fraction of the cell) into its neighbours and clamped to the
    image, so an element sitting on a seam still appears whole inside at least
    one tile. Tiles are returned row-major (row 0 left-to-right first).

    A 3x3 grid of 2160x1440 gives ~720x480 cells; with 12% overlap each tile is
    ~830x550 -- comfortably under a ~1568px model cap, so every tile is sent at
    native resolution."""
    if width <= 0 or height <= 0:
        raise ValueError("image dimensions must be positive")
    if cols < 1 or rows < 1:
        raise ValueError("cols and rows must be >= 1")
    if not 0.0 <= overlap < 0.5:
        raise ValueError("overlap must be in [0, 0.5)")

    cell_w = width / cols
    cell_h = height / rows
    pad_x = cell_w * overlap
    pad_y = cell_h * overlap

    tiles: list[Tile] = []
    for row in range(rows):
        for col in range(cols):
            left = max(0.0, col * cell_w - pad_x)
            right = min(float(width), (col + 1) * cell_w + pad_x)
            top = max(0.0, row * cell_h - pad_y)
            bottom = min(float(height), (row + 1) * cell_h + pad_y)
            x, y = round(left), round(top)
            tiles.append(Tile(col=col, row=row, x=x, y=y, w=round(right) - x, h=round(bottom) - y))
    return tiles


def local_box_to_global(tile: Tile, box: Box) -> Box:
    """Translate a box the model reported in this tile's own pixels (0,0 at the
    tile's top-left) into full-image pixels."""
    x1, y1, x2, y2 = box
    return (x1 + tile.x, y1 + tile.y, x2 + tile.x, y2 + tile.y)


def local_point_to_global(tile: Tile, x: int, y: int) -> tuple[int, int]:
    """Translate a tile-local point into full-image pixels (e.g. a click target)."""
    return (x + tile.x, y + tile.y)


def clamp_box(box: Box, width: int, height: int) -> Box:
    """Clip a box to the image bounds -- a tile's overlap padding can push a
    reported edge slightly past the real frame."""
    x1, y1, x2, y2 = box
    return (max(0, min(x1, width)), max(0, min(y1, height)),
            max(0, min(x2, width)), max(0, min(y2, height)))


def box_center(box: Box) -> tuple[int, int]:
    x1, y1, x2, y2 = box
    return ((x1 + x2) // 2, (y1 + y2) // 2)


def iou(a: Box, b: Box) -> float:
    """Intersection-over-union of two boxes; 0 if they don't overlap."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union else 0.0


@dataclass
class Detection:
    """One UI element located in full-image coordinates, plus which tiles saw
    it -- the unit `merge_detections` deduplicates and `perceive` returns."""
    box: Box
    label: str
    kind: str = ""
    confidence: float = 0.0
    tiles: tuple[tuple[int, int], ...] = ()  # (col, row) of every tile that reported it

    @property
    def center(self) -> tuple[int, int]:
        return box_center(self.box)


def merge_detections(dets: list[Detection], iou_threshold: float = 0.5) -> list[Detection]:
    """Collapse detections that overlapping tiles reported for the same element.
    Two detections merge when their global boxes overlap by at least
    `iou_threshold`; the higher-confidence one wins the box/label and the merged
    result records every tile that saw it. Greedy, confidence-first, so a strong
    detection anchors each cluster."""
    kept: list[Detection] = []
    for det in sorted(dets, key=lambda d: d.confidence, reverse=True):
        for i, k in enumerate(kept):
            if iou(det.box, k.box) >= iou_threshold:
                merged_tiles = tuple(dict.fromkeys(k.tiles + det.tiles))  # union, order-stable
                kept[i] = Detection(box=k.box, label=k.label, kind=k.kind,
                                    confidence=k.confidence, tiles=merged_tiles)
                break
        else:
            kept.append(det)
    return kept
