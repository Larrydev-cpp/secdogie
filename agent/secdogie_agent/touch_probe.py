"""Touch exploration: find UI elements by *touching* the screen, not walking the tree.

A blind VoiceOver user on a Mac trackpad doesn't walk the accessibility tree;
they drag a finger and hear what is under it. The OS call behind that finger is
a hit test (``AXUIElementCopyElementAtPosition``), and it can reach elements a
tree walk never lists: children hidden behind framework layers (SwiftUI's
``NSHostingView``), nodes a parent forgets to report in ``AXChildren``. This
module is the OS-free half of doing the same systematically:

* :func:`adaptive_probe` samples a region like a finger sweep. It starts with a
  coarse grid and subdivides only where neighbouring touches land on different
  elements (an edge), so the number of touches follows the UI's complexity, not
  its area, and a uniform region costs a handful of touches. Cells are visited
  coarse-to-fine, so a probe budget that runs out still leaves even coverage.
* :func:`merge_chains` grafts what the finger found (each hit as its ancestor
  chain, root first) into a depth-annotated snapshot, under the deepest ancestor
  the snapshot already knows, keeping depth-first order.

Nothing here touches the OS; the hit function is injected, so every rule is
testable headless.
"""
from __future__ import annotations

import dataclasses
import time
from collections import deque
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass

from .axtree import AxElement

Rect = tuple[int, int, int, int]  # (left, top, right, bottom), right/bottom exclusive
Point = tuple[int, int]

DEFAULT_MAX_PROBES = 256
DEFAULT_MIN_CELL = 12  # px: don't subdivide below this; finer than any hit target
DEFAULT_MAX_SECONDS = 0.5


@dataclass(frozen=True)
class ProbeSweep:
    """What a sweep touched. ``hits`` maps each touched point to what was under
    it (None = nothing / hit test failed). ``truncated`` is True when the probe
    or time budget ran out before every non-uniform cell was resolved."""

    hits: dict[Point, Hashable | None]
    probes: int
    truncated: bool

    def distinct(self) -> set[Hashable]:
        return {h for h in self.hits.values() if h is not None}


def _cell_points(cell: Rect) -> list[Point]:
    """Four inset corners and the centre of a cell. Corners are inset by one
    pixel so every point is strictly inside the (exclusive) rect."""
    left, top, right, bottom = cell
    r, b = right - 1, bottom - 1
    cx, cy = (left + r) // 2, (top + b) // 2
    return [(left, top), (r, top), (left, b), (r, b), (cx, cy)]


def _split(cell: Rect) -> list[Rect]:
    left, top, right, bottom = cell
    mx, my = (left + right) // 2, (top + bottom) // 2
    quads = [(left, top, mx, my), (mx, top, right, my), (left, my, mx, bottom), (mx, my, right, bottom)]
    return [q for q in quads if q[2] > q[0] and q[3] > q[1]]


def adaptive_probe(
    region: Rect,
    hit: Callable[[int, int], Hashable | None],
    *,
    max_probes: int = DEFAULT_MAX_PROBES,
    min_cell: int = DEFAULT_MIN_CELL,
    max_seconds: float = DEFAULT_MAX_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> ProbeSweep:
    """Sweep ``region`` with hit tests, subdividing where touches disagree.

    A cell whose five touch points all land on the same thing is uniform and
    is not refined. Otherwise it splits in four, down to ``min_cell`` pixels.
    Points shared by neighbouring cells are touched once. Breadth-first, so an
    exhausted budget still leaves the region evenly covered at the coarsest
    level reached."""
    left, top, right, bottom = region
    hits: dict[Point, Hashable | None] = {}
    if right <= left or bottom <= top or max_probes <= 0:
        return ProbeSweep(hits, 0, truncated=right > left and bottom > top)
    deadline = clock() + max_seconds
    queue: deque[Rect] = deque([region])
    truncated = False
    while queue:
        cell = queue.popleft()
        keys = []
        for p in _cell_points(cell):
            if p not in hits:
                if len(hits) >= max_probes or clock() > deadline:
                    truncated = True
                    break
                try:
                    hits[p] = hit(*p)
                except Exception:
                    hits[p] = None  # a failed touch is "nothing here", not a crash
            keys.append(hits[p])
        if truncated:
            break
        if len(set(keys)) <= 1:
            continue  # uniform: one element (or nothing) covers this cell
        if cell[2] - cell[0] < 2 * min_cell and cell[3] - cell[1] < 2 * min_cell:
            continue  # small enough: an edge runs through here, don't chase pixels
        queue.extend(_split(cell))
    return ProbeSweep(hits, len(hits), truncated)


def merge_chains(
    snapshot: Sequence[AxElement], chains: Sequence[Sequence[AxElement]]
) -> tuple[list[AxElement], int]:
    """Graft hit-test ancestor chains (root first) into a depth-first snapshot.

    Each chain is matched against the elements already present (identity is
    ``AxElement`` equality: role, name, automation id, bounds). Elements below
    the deepest known ancestor are inserted at the end of that ancestor's
    subtree with consecutive depths; a chain with no known ancestor becomes a
    new root. Returns ``(merged, added_count)``. If the snapshot carries no
    walk depths (-1), new elements are appended with depth -1 and nesting is
    left to the adapter's containment fallback."""
    merged = list(snapshot)
    added = 0
    has_depth = all(el.depth >= 0 for el in merged)
    for chain in chains:
        if not chain:
            continue
        present = set(merged)
        anchor = -1
        start = 0
        for k in range(len(chain) - 1, -1, -1):
            if chain[k] in present:
                anchor = merged.index(chain[k])
                start = k + 1
                break
        new = [el for el in chain[start:] if el not in present]
        if not new:
            continue
        if not has_depth:
            merged.extend(dataclasses.replace(el, depth=-1) for el in new)
            added += len(new)
            continue
        if anchor < 0:
            base, insert_at = 0, len(merged)
        else:
            base = merged[anchor].depth + 1
            insert_at = anchor + 1
            while insert_at < len(merged) and merged[insert_at].depth > merged[anchor].depth:
                insert_at += 1
        for offset, el in enumerate(new):
            merged.insert(insert_at + offset, dataclasses.replace(el, depth=base + offset))
        added += len(new)
    return merged, added


__all__ = [
    "DEFAULT_MAX_PROBES",
    "DEFAULT_MIN_CELL",
    "DEFAULT_MAX_SECONDS",
    "ProbeSweep",
    "adaptive_probe",
    "merge_chains",
]
