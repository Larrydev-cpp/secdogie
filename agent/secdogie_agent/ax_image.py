"""Build a model frame from an accessibility tree — no screenshot.

macOS control is a trackpad: names / roles / bounds. Screen Recording is
not the pad, and CGWindowListCreateImage is not the Mac image. This module
paints the live AX boxes onto a schematic so the vision model still has a
spatial figure to aim at.
"""
from __future__ import annotations

import io

from .axtree import AxElement
from .elements import interactable_targets

# Dark pad. Role tints stay readable after JPEG. No purple.
_BG = (12, 12, 14)
_GRID = (38, 38, 42)
_INK = (245, 245, 247)
_MUTED = (142, 142, 147)
_ROLE_FILL = {
    "button": (10, 132, 255),
    "splitbutton": (10, 132, 255),
    "push button": (10, 132, 255),
    "popupbutton": (10, 132, 255),
    "menubutton": (10, 132, 255),
    "edit": (48, 176, 132),
    "entry": (48, 176, 132),
    "text": (48, 176, 132),
    "textfield": (48, 176, 132),
    "textarea": (48, 176, 132),
    "securetextfield": (48, 176, 132),
    "password text": (48, 176, 132),
    "checkbox": (255, 159, 10),
    "check box": (255, 159, 10),
    "radiobutton": (255, 159, 10),
    "radio button": (255, 159, 10),
    "tab": (90, 110, 132),
    "tabitem": (90, 110, 132),
    "page tab": (90, 110, 132),
    "menuitem": (90, 110, 132),
    "menu item": (90, 110, 132),
    "window": (58, 58, 62),
}
_STRUCT = (72, 72, 78)
_PAD_MIN = (640, 400)


def render(
    elements: list[AxElement] | None,
    *,
    region: tuple[int, int, int, int] | None = None,
    targets: list[AxElement] | None = None,
) -> tuple[bytes, tuple[int, int]]:
    """Return (png_bytes, (width, height)) in the same coordinate space the
    loop already uses: region-relative if `region` is set, otherwise the
    union of element bounds. Click (x, y) on this figure is a trackpad tap.
    """
    from PIL import Image, ImageDraw

    boxes = [el for el in (elements or []) if _sane(el.bounds)]
    origin_x, origin_y, width, height = _canvas(boxes, region)
    img = Image.new("RGB", (width, height), _BG)
    draw = ImageDraw.Draw(img)
    _grid(draw, width, height)

    labeled = targets if targets is not None else interactable_targets(boxes)
    ref_of = {id(el): f"e{i}" for i, el in enumerate(labeled, 1)}

    # Large structural boxes first so interactable leaves paint on top.
    ordered = sorted(boxes, key=lambda el: el.area, reverse=True)
    for el in ordered:
        x0, y0, x1, y1 = _shift(el.bounds, origin_x, origin_y, width, height)
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        role = el.role.strip().lower()
        fill = _ROLE_FILL.get(role, _STRUCT)
        _round_box(draw, x0, y0, x1, y1, fill)
        ref = ref_of.get(id(el))
        name = (el.name or el.automation_id or el.role or "").strip()
        caption = f"{ref} {name}".strip() if ref else name
        if caption and (x1 - x0) >= 24 and (y1 - y0) >= 12:
            ink = _INK if ref else _MUTED
            draw.text((x0 + 6, y0 + 4), caption[:48], fill=ink)

    if not boxes:
        _round_box(draw, 16, 16, width - 17, height - 17, _STRUCT)
        draw.text((28, 28), "AX pad — no nodes (grant Accessibility)", fill=_MUTED)
        draw.text((28, 48), "AX 触控板 — 无节点（请授予辅助功能）", fill=_MUTED)

    out = io.BytesIO()
    img.save(out, format="PNG", compress_level=1)
    return out.getvalue(), (width, height)


def render_from_backend(
    backend,
    *,
    region: tuple[int, int, int, int] | None = None,
    targets: list[AxElement] | None = None,
) -> tuple[bytes, tuple[int, int]]:
    """Snapshot the live provider when present; empty pad if it cannot."""
    snapshot: list[AxElement] = []
    provider = getattr(backend, "ax_provider", None)
    snap = getattr(provider, "snapshot", None)
    if callable(snap):
        try:
            got = snap()
            if got:
                snapshot = list(got)
        except Exception:
            snapshot = []
    return render(snapshot, region=region, targets=targets)


def _sane(bounds: tuple[int, int, int, int]) -> bool:
    left, top, right, bottom = bounds
    return right > left and bottom > top


def _canvas(
    boxes: list[AxElement],
    region: tuple[int, int, int, int] | None,
) -> tuple[int, int, int, int]:
    if region is not None:
        left, top, width, height = region
        return int(left), int(top), max(1, int(width)), max(1, int(height))
    if not boxes:
        return 0, 0, _PAD_MIN[0], _PAD_MIN[1]
    left = min(el.bounds[0] for el in boxes)
    top = min(el.bounds[1] for el in boxes)
    right = max(el.bounds[2] for el in boxes)
    bottom = max(el.bounds[3] for el in boxes)
    width = max(_PAD_MIN[0], right - left)
    height = max(_PAD_MIN[1], bottom - top)
    return left, top, width, height


def _shift(
    bounds: tuple[int, int, int, int],
    origin_x: int,
    origin_y: int,
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    x0 = max(0, bounds[0] - origin_x)
    y0 = max(0, bounds[1] - origin_y)
    x1 = min(width - 1, bounds[2] - origin_x)
    y1 = min(height - 1, bounds[3] - origin_y)
    return x0, y0, x1, y1


def _grid(draw, width: int, height: int, step: int = 100) -> None:
    for x in range(0, width, step):
        draw.line([(x, 0), (x, height)], fill=_GRID, width=1)
    for y in range(0, height, step):
        draw.line([(0, y), (width, y)], fill=_GRID, width=1)


def _round_box(draw, x0: int, y0: int, x1: int, y1: int, fill) -> None:
    radius = max(4, min(16, (x1 - x0) // 8, (y1 - y0) // 8))
    box = (x0, y0, x1, y1)
    try:
        draw.rounded_rectangle(box, radius=radius, outline=fill, width=2)
    except Exception:
        draw.rectangle(box, outline=fill, width=2)
