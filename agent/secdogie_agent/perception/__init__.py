"""secdogie perception: zero-screenshot, structural sensing.

The agent never captures the screen to perceive it. It reads two structural
tracks and fuses them into one typed ``Observation``:

  * **AX** -- the operating system's accessibility tree: native windows and
    standard controls, with semantic identity and screen bounds.
  * **DIB** -- the Direct Inspection Buffer (``dib.py``): what AX cannot see --
    self-drawn controls, Canvas / WebGL, CAD drawing surfaces -- published by the
    application itself as a read-only structured buffer, mapped zero-copy and
    parsed into typed nodes.

Both tracks are read-only observation, so perception runs unattended with no
confirmation step. Acting on what was perceived is a separate matter: actions go
through the action gate, and a high-risk physical action is confirmed by a human
on the local device.

This package imports no image library and calls no capture API; a structural
test (``tests/test_perception_zero_screenshot.py``) keeps it that way.
"""
from __future__ import annotations

from .dib import (
    FLAG_ENABLED,
    FLAG_FOCUSED,
    FLAG_SELECTED,
    FLAG_VISIBLE,
    DibError,
    DibFormatError,
    DibNode,
    DibReader,
    DibSnapshot,
    DibTornRead,
    encode_dib,
    parse_dib,
)
from .observation import (
    CONFLICT_GENERATION,
    CONFLICT_GEOMETRY,
    CONFLICT_TIME,
    CONFLICT_WINDOW_IDENTITY,
    SOURCE_AX,
    SOURCE_DIB,
    SOURCE_FUSED,
    Budget,
    BudgetExceeded,
    BudgetViolation,
    FusionConfig,
    FusionResult,
    Geometry,
    Observation,
    ObservationConflict,
    SemanticNode,
    check_budget,
    enforce_budget,
    fuse,
    observe_ax,
    observe_dib,
)

__all__ = [
    "SOURCE_AX",
    "SOURCE_DIB",
    "SOURCE_FUSED",
    "CONFLICT_WINDOW_IDENTITY",
    "CONFLICT_GENERATION",
    "CONFLICT_GEOMETRY",
    "CONFLICT_TIME",
    "Geometry",
    "SemanticNode",
    "Observation",
    "ObservationConflict",
    "FusionResult",
    "FusionConfig",
    "Budget",
    "BudgetViolation",
    "BudgetExceeded",
    "check_budget",
    "enforce_budget",
    "observe_ax",
    "observe_dib",
    "fuse",
    "DibNode",
    "DibSnapshot",
    "DibReader",
    "DibError",
    "DibFormatError",
    "DibTornRead",
    "parse_dib",
    "encode_dib",
    "FLAG_VISIBLE",
    "FLAG_ENABLED",
    "FLAG_FOCUSED",
    "FLAG_SELECTED",
]
