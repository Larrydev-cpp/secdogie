"""Perception adapters: pure in-memory conversions from structural senses
(the accessibility tree, DIB handles) into the typed ``observation.Observation``."""
from __future__ import annotations

from .adapter import (
    AdapterReport,
    BaseObservationAdapter,
    DibProvider,
    FilterPolicy,
    NodeQuery,
    StructuralObservationAdapter,
    UiTreeNode,
    build_tree,
)

__all__ = [
    "AdapterReport",
    "BaseObservationAdapter",
    "DibProvider",
    "FilterPolicy",
    "NodeQuery",
    "StructuralObservationAdapter",
    "UiTreeNode",
    "build_tree",
]
