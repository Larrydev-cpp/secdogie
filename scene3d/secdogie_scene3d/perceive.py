"""High-resolution tiled perception: read a big screenshot at full detail by
splitting it across many models.

One vision call over a 2160x1440 frame sees it downscaled and mis-locates small
controls (a timeline clip, a toolbar icon). Here the frame is cut into a grid
(see tiles.py), every tile is analyzed at native resolution by its own worker --
spread across the key pool so N tiles are N concurrent requests, not N serial
ones -- and the per-tile element lists are translated back to full-image
coordinates and merged into a single map. The "combine" is geometric: no extra
model call is needed to fuse the tiles, only to reason across them (out of scope
here).
"""
from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from secdogie_agent.providers.base import parse_action_json

from . import tiles as tiles_mod
from .model import SceneModel
from .tiles import Detection, Tile

TILE_SYSTEM = """You are looking at ONE rectangular tile cropped from a larger, high-resolution screen (for example a video editor). Report every distinct UI element you can see IN THIS TILE: buttons, icons, menu items, text fields, labels, timeline clips, sliders, thumbnails, playhead, and so on.

Use THIS tile's own pixel coordinates: (0,0) is the tile's top-left corner and the tile is the size given below. Give each element a tight bounding box [x1, y1, x2, y2] in those tile pixels.

Return ONLY a JSON object, no prose:
{
  "elements": [
    {"label": "short human text or icon name", "type": "button|icon|text|field|menu|clip|slider|thumbnail|other", "box": [x1, y1, x2, y2], "confidence": 0.0}
  ]
}
Only include elements you can actually localize in this tile. Do not guess about content cut off at a tile edge; a neighboring tile will cover it."""


@dataclass
class TileResult:
    tile: Tile
    detections: list[Detection] = field(default_factory=list)
    error: str | None = None


@dataclass
class ScreenPerception:
    width: int
    height: int
    elements: list[Detection] = field(default_factory=list)  # merged, full-image coordinates
    tiles: list[TileResult] = field(default_factory=list)  # per-tile detail (incl. errors)

    @property
    def errors(self) -> list[str]:
        return [f"tile ({t.tile.col},{t.tile.row}): {t.error}" for t in self.tiles if t.error]


def _tile_user(tile: Tile) -> str:
    return (
        f"This tile is {tile.w}x{tile.h} pixels. It is the tile at column {tile.col}, row {tile.row} "
        "of the full screen. List the UI elements visible in it as JSON."
    )


def _parse_detections(text: str, tile: Tile) -> list[Detection]:
    data = parse_action_json(text)  # tolerant: pulls the first JSON object out of any prose
    out: list[Detection] = []
    for raw in data.get("elements", []) or []:
        box = raw.get("box")
        if not (isinstance(box, list | tuple) and len(box) == 4):
            continue  # an element we can't place is not useful for targeting
        local = tuple(int(round(float(v))) for v in box)
        out.append(
            Detection(
                box=tiles_mod.local_box_to_global(tile, local),  # tile pixels -> full-image pixels
                label=str(raw.get("label", "")),
                kind=str(raw.get("type", "")),
                confidence=float(raw.get("confidence", 0.0) or 0.0),
                tiles=((tile.col, tile.row),),
            )
        )
    return out


def analyze_tile(model: SceneModel, tile: Tile, tile_png: bytes) -> TileResult:
    """Run one worker over one tile. Any model or parse failure is captured on
    the result, never raised, so one bad tile can't sink the whole frame."""
    try:
        text = model.describe(TILE_SYSTEM, _tile_user(tile), tile_png)
    except Exception as e:
        return TileResult(tile=tile, error=f"{type(e).__name__}: {e}")
    try:
        dets = _parse_detections(text, tile)
    except (ValueError, TypeError):
        return TileResult(tile=tile, error="could not parse the tile's element JSON")
    return TileResult(tile=tile, detections=dets)


def _crop_png(image_png: bytes, tile: Tile) -> bytes:
    from PIL import Image

    with Image.open(io.BytesIO(image_png)) as img:
        crop = img.crop((tile.x, tile.y, tile.x + tile.w, tile.y + tile.h))
        buf = io.BytesIO()
        crop.save(buf, format="PNG")
        return buf.getvalue()


def _image_size(image_png: bytes) -> tuple[int, int]:
    from PIL import Image

    with Image.open(io.BytesIO(image_png)) as img:
        return (img.width, img.height)


def perceive_screen(
    image_png: bytes,
    models: SceneModel | list[SceneModel],
    cols: int = 3,
    rows: int = 3,
    overlap: float = 0.12,
    iou_threshold: float = 0.5,
    max_workers: int | None = None,
) -> ScreenPerception:
    """Perceive one high-resolution screenshot by tiling it cols x rows, analyzing
    every tile at native resolution (concurrently, one worker per pooled model),
    and merging the per-tile elements into a single full-image element map.
    `models` may be one model or a pool; tile i is handled by pool[i % len]."""
    pool = models if isinstance(models, list) else [models]
    if not pool:
        raise ValueError("perceive_screen needs at least one model")

    width, height = _image_size(image_png)
    tiles = tiles_mod.plan_tiles(width, height, cols=cols, rows=rows, overlap=overlap)
    crops = [_crop_png(image_png, t) for t in tiles]

    workers = max_workers if max_workers is not None else min(len(pool), len(tiles))

    def run_one(i: int) -> TileResult:
        return analyze_tile(pool[i % len(pool)], tiles[i], crops[i])

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        results = list(ex.map(run_one, range(len(tiles))))  # map preserves tile order

    all_dets = [d for r in results for d in r.detections]
    merged = tiles_mod.merge_detections(all_dets, iou_threshold=iou_threshold)
    # Strongest first makes the map easy to scan and to pick a click target from.
    merged.sort(key=lambda d: d.confidence, reverse=True)
    return ScreenPerception(width=width, height=height, elements=merged, tiles=results)
