"""Parse an Android `uiautomator dump` XML hierarchy into a flat list of UI
elements, so the agent can target real widgets (by text / resource-id /
content-desc) or snap a click onto the element under a point -- the RPA way of
hitting things by identity instead of guessing pixel coordinates.

Pure and OS-free (stdlib XML only) so it unit-tests without a device.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from xml.etree import ElementTree

# uiautomator encodes bounds as "[x1,y1][x2,y2]".
_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


@dataclass(frozen=True)
class UiElement:
    text: str
    resource_id: str
    content_desc: str
    cls: str  # the `class` attribute (e.g. android.widget.Button)
    clickable: bool
    bounds: tuple[int, int, int, int]  # (left, top, right, bottom) in real device pixels

    @property
    def center(self) -> tuple[int, int]:
        left, top, right, bottom = self.bounds
        return ((left + right) // 2, (top + bottom) // 2)

    @property
    def area(self) -> int:
        left, top, right, bottom = self.bounds
        return max(0, right - left) * max(0, bottom - top)

    def contains(self, x: int, y: int) -> bool:
        left, top, right, bottom = self.bounds
        return left <= x <= right and top <= y <= bottom


def _parse_bounds(raw: str) -> tuple[int, int, int, int] | None:
    m = _BOUNDS_RE.match(raw or "")
    if not m:
        return None
    x1, y1, x2, y2 = (int(g) for g in m.groups())
    return (x1, y1, x2, y2)


def parse(xml: str) -> list[UiElement]:
    """Flatten the hierarchy into every node that has a valid bounds box."""
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as e:
        raise ValueError(f"could not parse uiautomator XML: {e}") from e

    elements: list[UiElement] = []
    for node in root.iter("node"):
        bounds = _parse_bounds(node.get("bounds", ""))
        if bounds is None:
            continue
        elements.append(
            UiElement(
                text=node.get("text", ""),
                resource_id=node.get("resource-id", ""),
                content_desc=node.get("content-desc", ""),
                cls=node.get("class", ""),
                clickable=node.get("clickable", "false") == "true",
                bounds=bounds,
            )
        )
    return elements


def find_elements(
    elements: list[UiElement],
    *,
    text: str | None = None,
    resource_id: str | None = None,
    content_desc: str | None = None,
    cls: str | None = None,
    clickable_only: bool = False,
) -> list[UiElement]:
    """Filter by any combination of selectors. `text`/`content_desc` match on a
    case-insensitive substring; `resource_id` matches exactly or on the id part
    after `.../id/`; `cls` matches the element's class name exactly (it's a
    canonical Java class string, e.g. "android.widget.Button", not free text --
    the weakest/last-resort selector when nothing more specific is available)."""
    out = []
    for el in elements:
        if clickable_only and not el.clickable:
            continue
        if text is not None and text.lower() not in el.text.lower():
            continue
        if content_desc is not None and content_desc.lower() not in el.content_desc.lower():
            continue
        if resource_id is not None and not _resource_matches(el.resource_id, resource_id):
            continue
        if cls is not None and el.cls != cls:
            continue
        out.append(el)
    return out


def _resource_matches(full: str, wanted: str) -> bool:
    if not full:
        return False
    if full == wanted:
        return True
    # Allow matching just the id name, e.g. "login_button" against
    # "com.app:id/login_button".
    return full.rsplit("/", 1)[-1] == wanted


def smallest_clickable_at(elements: list[UiElement], x: int, y: int) -> UiElement | None:
    """The tightest clickable element whose box contains (x, y), or None. The
    smallest match is the most specific target -- a large container also
    contains the point but isn't what a click at that spot meant."""
    best: UiElement | None = None
    for el in elements:
        if not el.clickable or not el.contains(x, y):
            continue
        if best is None or el.area < best.area:
            best = el
    return best
