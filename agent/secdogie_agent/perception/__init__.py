"""Perception adapters: pure in-memory conversions from structural senses
(the accessibility tree, DIB shared-memory framebuffers) into the typed
``observation.Observation``."""
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
from .dib import (
    DibBoundsError,
    DibBudgetError,
    DibError,
    DibFormatError,
    DIBFrameBuffer,
    DibTearError,
    HeapDibReader,
    read_tearfree,
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
    "DIBFrameBuffer",
    "DibBoundsError",
    "DibBudgetError",
    "DibError",
    "DibFormatError",
    "DibTearError",
    "HeapDibReader",
    "read_tearfree",
]
