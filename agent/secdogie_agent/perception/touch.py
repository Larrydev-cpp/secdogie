"""Touch-probing provider: a tree walk, completed by a finger.

Wraps an AX provider that can ``probe`` (the macOS one) and returns snapshots
enriched with what touch exploration found where the walk came up short:

* **blind nodes** -- custom-drawn roles and opaque leaves (the same
  ``FilterPolicy.is_blind`` rule the adapter uses): touch inside their box;
* **shallow windows** -- a window whose walked subtree is nearly empty (an
  Electron/Chromium window before its tree is built, a SwiftUI hosting view
  hiding its children): touch the whole window.

Found elements are grafted under their deepest known ancestor
(:func:`touch_probe.merge_chains`) and tagged ``origin="hit-test"`` or
``"touch-text"``. A blind region where every touch lands on the blind node
itself is **opaque**: it is counted in ``last_report`` and left as an abstract
blind leaf. No pixels are read anywhere -- the only signal is what the hit test
says is under each point.

Touching costs cross-process calls, so a sweep runs only when the walked tree
changes (same tree => reuse the last result) or after :meth:`invalidate_probe`
(the loop calls it on ``look``), under a per-snapshot probe budget.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .. import touch_probe
from ..axtree import AxElement
from .adapter import DEFAULT_FILTER_POLICY, FilterPolicy, geometry_from_bounds, semantic_node_from

DEFAULT_SNAPSHOT_PROBES = 384  # total touches per sweep, split across regions
MIN_REGION_PROBES = 24
DEFAULT_MAX_REGIONS = 8
DEFAULT_SHALLOW_MIN_NODES = 4  # a window with fewer walked nodes than this is "shallow"


@dataclass(frozen=True)
class TouchReport:
    regions: int = 0
    probes: int = 0
    added: int = 0
    text_lines: int = 0
    opaque_regions: int = 0
    truncated_regions: int = 0
    reused: bool = False


def _subtree_sizes(elements: Sequence[AxElement]) -> list[int]:
    """Number of walked descendants of each element (pre-order + depth)."""
    sizes = [0] * len(elements)
    stack: list[int] = []
    for i, el in enumerate(elements):
        while stack and elements[stack[-1]].depth >= el.depth:
            stack.pop()
        for j in stack:
            sizes[j] += 1
        stack.append(i)
    return sizes


def probe_regions(
    elements: Sequence[AxElement],
    policy: FilterPolicy = DEFAULT_FILTER_POLICY,
    *,
    shallow_min_nodes: int = DEFAULT_SHALLOW_MIN_NODES,
    max_regions: int = DEFAULT_MAX_REGIONS,
) -> list[tuple[AxElement, tuple[int, int, int, int]]]:
    """Which boxes to touch, as (owner element, bounds): blind nodes first,
    then shallow windows. Needs walk depths; without them (-1) there is no way
    to tell a shallow window, so only blind nodes are returned."""
    has_depth = bool(elements) and all(el.depth >= 0 for el in elements)
    sizes = _subtree_sizes(elements) if has_depth else [1] * len(elements)
    out: list[tuple[AxElement, tuple[int, int, int, int]]] = []
    seen: set[tuple[int, int, int, int]] = set()

    def add(el: AxElement) -> None:
        if el.bounds not in seen and geometry_from_bounds(el.bounds).valid:
            seen.add(el.bounds)
            out.append((el, el.bounds))

    for i, el in enumerate(elements):
        if policy.is_blind(semantic_node_from(el), has_children=sizes[i] > 0):
            add(el)
    if has_depth:
        for i, el in enumerate(elements):
            if el.role == "Window" and sizes[i] < shallow_min_nodes:
                add(el)
    return out[:max_regions]


class TouchProbingProvider:
    """A ``DesktopAxProvider`` whose ``snapshot()`` is the base walk plus touch
    exploration of blind nodes and shallow windows. Every other attribute
    (press, set_value, press_at, restore_accessibility, occluder_of...) is the
    base provider's."""

    def __init__(
        self,
        base,
        *,
        policy: FilterPolicy = DEFAULT_FILTER_POLICY,
        max_probes: int = DEFAULT_SNAPSHOT_PROBES,
        max_regions: int = DEFAULT_MAX_REGIONS,
        shallow_min_nodes: int = DEFAULT_SHALLOW_MIN_NODES,
        read_text: bool = True,
        min_cell: int = touch_probe.DEFAULT_MIN_CELL,
    ) -> None:
        self._base = base
        self.policy = policy
        self.max_probes = max_probes
        self.max_regions = max_regions
        self.shallow_min_nodes = shallow_min_nodes
        self.read_text = read_text
        self.min_cell = min_cell
        self.last_report = TouchReport()
        self._cache_key: tuple | None = None
        self._cache_value: list[AxElement] | None = None

    def __getattr__(self, name):
        return getattr(self._base, name)

    def invalidate_probe(self) -> None:
        """Forget the cached sweep; the next snapshot touches again."""
        self._cache_key = None
        self._cache_value = None

    def snapshot(self) -> list[AxElement] | None:
        walked = self._base.snapshot()
        probe = getattr(self._base, "probe", None)
        if not walked or not callable(probe):
            self.last_report = TouchReport()
            return walked
        key = tuple((el, el.depth) for el in walked)
        if key == self._cache_key and self._cache_value is not None:
            self.last_report = TouchReport(reused=True)
            return list(self._cache_value)

        regions = probe_regions(
            walked, self.policy, shallow_min_nodes=self.shallow_min_nodes, max_regions=self.max_regions
        )
        merged = list(walked)
        stats = {"probes": 0, "added": 0, "text_lines": 0, "opaque": 0, "truncated": 0}
        if regions:
            share = max(MIN_REGION_PROBES, self.max_probes // len(regions))
            for owner, box in regions:
                remaining = self.max_probes - stats["probes"]
                if remaining <= 0:
                    stats["truncated"] += 1
                    continue
                try:
                    result = probe(
                        box, max_probes=min(share, remaining), min_cell=self.min_cell, read_text=self.read_text
                    )
                except Exception:
                    continue  # touching is best-effort; the walk still stands
                stats["probes"] += result.probes
                stats["text_lines"] += result.text_lines
                stats["truncated"] += int(result.truncated)
                if result.distinct and result.distinct <= {owner}:
                    stats["opaque"] += 1  # every touch landed on the blind node itself
                merged, added = touch_probe.merge_chains(merged, result.chains)
                stats["added"] += added
        self.last_report = TouchReport(
            regions=len(regions),
            probes=stats["probes"],
            added=stats["added"],
            text_lines=stats["text_lines"],
            opaque_regions=stats["opaque"],
            truncated_regions=stats["truncated"],
        )
        self._cache_key = key
        self._cache_value = merged
        return list(merged)


__all__ = [
    "DEFAULT_SNAPSHOT_PROBES",
    "TouchProbingProvider",
    "TouchReport",
    "probe_regions",
]
