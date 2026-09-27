"""Structural Adapter: package an AX snapshot as a typed ``Observation``.

The OS-facing half -- walking the live UIA / AT-SPI / AX tree into
``AxElement``s -- already lives behind the ``DesktopAxProvider`` seam in
desktop_ax.py. This module is the other half: a pure data conversion from that
flat element list into the ``Observation`` that fusion (observation.py) consumes.
No image libraries, no OS calls, no capture -- the provider is injected, so the
whole adapter runs headless on CI against a fake.

Mapping (lossless for everything ``AxElement`` carries):

  * ``role`` / ``name`` / ``automation_id`` -> the same ``SemanticNode`` fields;
  * ``bounds`` (left, top, right, bottom) -> ``Geometry(x, y, w, h)`` with
    ``x=left, y=top, w=right-left, h=bottom-top``. Degenerate boxes keep their
    raw numbers (``Geometry.valid`` is then False) rather than being clamped, so
    the original bounds are always recoverable;
  * the provider's element order (its tree-walk order, which is how hierarchy
    reaches us -- ``AxElement`` has no parent link) is preserved as-is.

The window ``geometry`` is the union of every element's valid box: the tree's
root window normally contains all its descendants, so this is the window's
extent as AX sees it, and it is what fusion compares against a DIB.

An absent (``None``) or empty snapshot yields a standard empty observation:
no nodes, no geometry, confidence 0.0 -- "perceived nothing", never an
invented reading.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence

from ..axtree import AxElement
from ..desktop_ax import DesktopAxProvider
from ..observation import Geometry, Observation, SemanticNode, observe_ax

# Confidence of a populated AX reading; matches observe_ax's default so an
# adapter-built observation fuses exactly like a hand-built one.
DEFAULT_AX_CONFIDENCE = 0.9
EMPTY_CONFIDENCE = 0.0

SnapshotFn = Callable[[], "Sequence[AxElement] | None"]


class BaseObservationAdapter(ABC):
    """One sense, packaged: anything that can produce an ``Observation``."""

    @abstractmethod
    def get_observation(self) -> Observation:
        raise NotImplementedError


def geometry_from_bounds(bounds: tuple[int, int, int, int]) -> Geometry:
    """(left, top, right, bottom) -> Geometry(x, y, w, h), no clamping."""
    left, top, right, bottom = bounds
    return Geometry(x=left, y=top, w=right - left, h=bottom - top)


def semantic_node_from(el: AxElement) -> SemanticNode:
    return SemanticNode(
        role=el.role,
        name=el.name,
        automation_id=el.automation_id,
        bounds=geometry_from_bounds(el.bounds),
    )


def union_geometry(nodes: Sequence[SemanticNode]) -> Geometry:
    """Smallest rect covering every node with valid bounds; empty if none."""
    boxes = [n.bounds for n in nodes if n.bounds.valid]
    if not boxes:
        return Geometry()
    x0 = min(g.x for g in boxes)
    y0 = min(g.y for g in boxes)
    x1 = max(g.x + g.w for g in boxes)
    y1 = max(g.y + g.h for g in boxes)
    return Geometry(x=x0, y=y0, w=x1 - x0, h=y1 - y0)


class StructuralObservationAdapter(BaseObservationAdapter):
    """Turn an AX provider's ``snapshot()`` into an AX ``Observation``.

    ``ax_provider`` is either a ``DesktopAxProvider`` (anything with a
    ``.snapshot()`` method) or a bare zero-argument callable returning the same
    ``list[AxElement] | None``. Window identity and generation are supplied by
    the caller -- the AX snapshot itself does not carry them."""

    def __init__(
        self,
        ax_provider: DesktopAxProvider | SnapshotFn,
        *,
        window_id: int = 0,
        app_pid: int = 0,
        generation: int = 0,
        confidence: float = DEFAULT_AX_CONFIDENCE,
        clock: Callable[[], float] = time.time,
    ) -> None:
        snapshot = getattr(ax_provider, "snapshot", None)
        if callable(snapshot):
            self._snapshot: SnapshotFn = snapshot
        elif callable(ax_provider):
            self._snapshot = ax_provider
        else:
            raise TypeError("ax_provider must have a snapshot() method or be callable")
        self.window_id = window_id
        self.app_pid = app_pid
        self.generation = generation
        self.confidence = confidence
        self._clock = clock

    def get_observation(self) -> Observation:
        elements = self._snapshot()
        nodes = tuple(semantic_node_from(el) for el in elements or ())
        if not nodes:
            return self._empty_observation()
        return observe_ax(
            window_id=self.window_id,
            app_pid=self.app_pid,
            semantic_nodes=nodes,
            geometry=union_geometry(nodes),
            generation=self.generation,
            confidence=self.confidence,
            clock=self._clock,
        )

    def _empty_observation(self) -> Observation:
        return observe_ax(
            window_id=self.window_id,
            app_pid=self.app_pid,
            generation=self.generation,
            confidence=EMPTY_CONFIDENCE,
            clock=self._clock,
        )


__all__ = [
    "BaseObservationAdapter",
    "StructuralObservationAdapter",
    "geometry_from_bounds",
    "semantic_node_from",
    "union_geometry",
]
