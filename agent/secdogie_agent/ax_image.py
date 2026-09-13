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

# Dark pad. Role tints stay readable after JPEG.
_BG = (18, 20, 26)
_GRID = (36, 40, 50)
_INK = (230, 232, 236)
_MUTED = (150, 156, 168)
_ROLE_FILL = {
    "button": (40, 92, 168),
    "splitbutton": (40, 92, 168),
    "push button": (40, 92, 168),
    "popupbutton": (40, 92, 168),
    "menubutton": (40, 92, 168),
    "edit": (36, 110, 78),
    "entry": (36, 110, 78),
    "text": (36, 110, 78),
    "textfield": (36, 110, 78),
    "textarea": (36, 110, 78),
    "securetextfield": (36, 110, 78),
    "password text": (36, 110, 78),
    "checkbox": (128, 88, 36),
    "check box": (128, 88, 36),
    "radiobutton": (128, 88, 36),
    "radio button": (128, 88, 36),
    "tab": (88, 64, 148),
    "tabitem": (88, 64, 148),
    "page tab": (88, 64, 148),
    "menuitem": (88, 64, 148),
    "menu item": (88, 64, 148),
    "window": (48, 52, 62),
}
_STRUCT = (58, 64, 76)
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
        draw.rectangle((x0, y0, x1, y1), outline=fill, width=2)
        ref = ref_of.get(id(el))
        name = (el.name or el.automation_id or el.role or "").strip()
        caption = f"{ref} {name}".strip() if ref else name
        if caption and (x1 - x0) >= 24 and (y1 - y0) >= 12:
            ink = _INK if ref else _MUTED
            draw.text((x0 + 3, y0 + 2), caption[:48], fill=ink)

    if not boxes:
        draw.text((16, 16), "AX pad — no nodes (grant Accessibility)", fill=_MUTED)

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
