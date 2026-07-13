"""Pure model + queries for a desktop accessibility tree.

This is the desktop equivalent of android's uitree.py: given a flat list of
accessibility elements (role, name, automation-id, bounds), it finds the element
under a point and re-finds an element by identity. That's the RPA "hit things by
identity, not by frozen pixel coordinate" idea, applied to the desktop.

Deliberately OS-free and side-effect-free so it unit-tests without a real
desktop. The part that *does* need the OS -- walking the live UI Automation /
AT-SPI / AX tree into these AxElements -- lives behind a provider seam in
desktop_ax.py and is exercised on your machine, not here.
"""
from __future__ import annotations

from dataclasses import dataclass

# Selector kind tag (backend.ElementSelector.kind) so a desktop-ax selector is
# never handed to a different backend's locate(). Matches DesktopBackend.
SELECTOR_KIND = "desktop-ax"


@dataclass(frozen=True)
class AxElement:
    role: str          # control type, e.g. "Button", "Edit" (platform-normalized by the provider)
    name: str          # visible label / accessible name, e.g. "Save"
    automation_id: str  # stable developer id (Windows AutomationId; "" where a platform has none)
    bounds: tuple[int, int, int, int]  # (left, top, right, bottom) in real screen pixels

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


def element_at(elements: list[AxElement], x: int, y: int) -> AxElement | None:
    """The tightest element whose box contains (x, y), or None. The smallest
    match is the most specific target -- a window/pane also contains the point
    but isn't what a click there meant (same rule as uitree.smallest_at)."""
    best: AxElement | None = None
    for el in elements:
        if not el.contains(x, y):
            continue
        if best is None or el.area < best.area:
            best = el
    return best


def find_elements(
    elements: list[AxElement],
    *,
    automation_id: str | None = None,
    name: str | None = None,
    role: str | None = None,
) -> list[AxElement]:
    """Filter by any combination of identity attributes, all matched EXACTLY.
    Desktop trees are dense and repetitive, so exact matching (not substring)
    keeps a re-find from latching onto a lookalike; `selector_for` records the
    strongest available combination so this stays precise."""
    out = []
    for el in elements:
        if automation_id is not None and el.automation_id != automation_id:
            continue
        if name is not None and el.name != name:
            continue
        if role is not None and el.role != role:
            continue
        out.append(el)
    return out


def selector_for(el: AxElement) -> dict[str, str] | None:
    """The strongest identity attributes to re-find `el` by, or None if it has
    nothing identifying (role alone is too ambiguous to trust -- many buttons
    share a role). Prefers the stable automation-id, then the visible name,
    always keeping the role to disambiguate. Feed the result straight back to
    `find_elements(**attrs)`."""
    if el.automation_id:
        return {"automation_id": el.automation_id, "role": el.role} if el.role else {"automation_id": el.automation_id}
    if el.name:
        return {"name": el.name, "role": el.role} if el.role else {"name": el.name}
    return None
