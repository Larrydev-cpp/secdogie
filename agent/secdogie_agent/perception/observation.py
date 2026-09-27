"""Observation Fusion: one honest view of a window from structural senses --
zero screenshots.

The agent perceives a window two structural ways, each with different trust and
reach. There is no pixel sense at all: no screenshot, no frame grab, no vision
model. Perception here is structural, not visual.

  * ``ax``  -- the accessibility tree (macOS AX / Windows UIA). PRIMARY: it
               carries semantic identity (role, name, automation id), which is
               what targeting and the Socratic gate reason about.
  * ``dib`` -- the Direct Inspection Buffer (dib.py): a read-only, structured
               description of what the application is showing, published by the
               application itself. It covers what AX cannot see -- self-drawn
               controls, Canvas / WebGL, a CAD drawing surface -- as typed nodes,
               not pixels.

This module fuses those into a single ``Observation`` and -- this is the point --
it *never silently overwrites* one sense with another. When AX and the DIB
disagree about geometry, when one reading is a stale generation, or when two
readings claim different windows, that is recorded as an ``ObservationConflict``
and the fused confidence drops. Perception that hides its own disagreement is how
an autonomous agent clicks the wrong thing; fail toward surfacing the conflict.

Read-only throughout: nothing here reads, writes, or injects into another
process. It reasons about evidence the two tracks already produced -- pure fusion
logic, which is why it runs headless on CI with no desktop. Everything is a pure
function of its inputs, so the module is deterministic and testable on Linux
with no AX, no DIB, and no screen.
"""
from __future__ import annotations

import hashlib
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # the DIB types live in dib.py, which imports Geometry from here
    from .dib import DibSnapshot

# ---------------------------------------------------------------------------
# Sources and conflict kinds (string constants so JSON / journal stay stable)
# ---------------------------------------------------------------------------

SOURCE_AX = "ax"
SOURCE_DIB = "dib"
SOURCE_FUSED = "fused"
_SOURCES = frozenset({SOURCE_AX, SOURCE_DIB, SOURCE_FUSED})

# Trust order used only to break ties when picking a fused geometry / identity;
# it is NOT a licence to overwrite -- a disagreement is still a conflict.
_SOURCE_RANK = {SOURCE_AX: 3, SOURCE_DIB: 2, SOURCE_FUSED: 0}

CONFLICT_WINDOW_IDENTITY = "window-identity-mismatch"
CONFLICT_GENERATION = "generation-skew"
CONFLICT_GEOMETRY = "geometry-mismatch"
CONFLICT_TIME = "time-skew"


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Geometry:
    """A window/control rectangle in screen coordinates. Empty (w<=0 or h<=0)
    means "no geometry", which is a valid state -- AX often knows identity
    before it knows bounds."""

    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0

    @property
    def valid(self) -> bool:
        return self.w > 0 and self.h > 0

    @property
    def area(self) -> int:
        return self.w * self.h if self.valid else 0

    def iou(self, other: Geometry) -> float:
        """Intersection-over-union, the standard geometry-agreement measure.
        0.0 when either rect is empty or they do not overlap; 1.0 when equal."""
        if not self.valid or not other.valid:
            return 0.0
        ax0, ay0, ax1, ay1 = self.x, self.y, self.x + self.w, self.y + self.h
        bx0, by0, bx1, by1 = other.x, other.y, other.x + other.w, other.y + other.h
        ix0, iy0 = max(ax0, bx0), max(ay0, by0)
        ix1, iy1 = min(ax1, bx1), min(ay1, by1)
        iw, ih = ix1 - ix0, iy1 - iy0
        if iw <= 0 or ih <= 0:
            return 0.0
        inter = iw * ih
        union = self.area + other.area - inter
        return inter / union if union > 0 else 0.0


# ---------------------------------------------------------------------------
# Semantic nodes (what both tracks contribute)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SemanticNode:
    """A minimal, hashable projection of one AX/UIA node or DIB node.
    Identity-first: what targeting needs, without dragging the whole platform
    tree (or the whole published scene) into fusion. DIB nodes carry
    ``automation_id = "dib:<node_id>"``."""

    role: str = ""
    name: str = ""
    automation_id: str = ""
    bounds: Geometry = field(default_factory=Geometry)
    enabled: bool = True

    def key(self) -> tuple[str, str, str]:
        return (self.role, self.name, self.automation_id)



# ---------------------------------------------------------------------------
# Observation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Observation:
    """One window perceived by one sense at one instant.

    ``generation`` is a monotonic counter for the *window* (bumped on every
    focus/resize/relayout by the capture layer); it is what lets fusion and, in
    Phase 2.5, action targeting detect that a reading is stale (TOCTOU) rather
    than trusting coordinates that may have moved."""

    source: str
    window_id: int
    app_pid: int
    generation: int = 0
    timestamp: float = 0.0
    confidence: float = 0.0
    geometry: Geometry = field(default_factory=Geometry)
    semantic_nodes: tuple[SemanticNode, ...] = ()
    dib: DibSnapshot | None = None  # the parsed buffer behind a dib / fused reading
    content_hash: str = ""

    def __post_init__(self) -> None:
        if self.source not in _SOURCES:
            raise ValueError(f"unknown observation source: {self.source!r}")

    @property
    def window(self) -> tuple[int, int]:
        """The window identity fusion groups by: id AND owning process."""
        return (self.window_id, self.app_pid)

    def is_stale_against(self, generation: int) -> bool:
        return self.generation < generation


def _now(clock) -> float:
    return clock()


def _content_hash_for(
    source: str,
    window: tuple[int, int],
    generation: int,
    geometry: Geometry,
    semantic_nodes: Sequence[SemanticNode],
    dib: DibSnapshot | None,
) -> str:
    parts = [
        source,
        f"{window[0]}:{window[1]}",
        str(generation),
        f"{geometry.x},{geometry.y},{geometry.w},{geometry.h}",
    ]
    for n in semantic_nodes:
        parts.append("|".join(n.key()))
    if dib is not None:
        parts.append(f"dib:{dib.digest}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def observe_ax(
    *,
    window_id: int,
    app_pid: int,
    semantic_nodes: Iterable[SemanticNode] = (),
    geometry: Geometry | None = None,
    generation: int = 0,
    confidence: float = 0.9,
    timestamp: float | None = None,
    clock=time.time,
) -> Observation:
    """Build an AX observation. Highest default confidence -- AX is the primary
    identity path -- but still just one reading to be fused, not ground truth."""
    nodes = tuple(semantic_nodes)
    geom = geometry or Geometry()
    ts = _now(clock) if timestamp is None else timestamp
    return Observation(
        source=SOURCE_AX,
        window_id=window_id,
        app_pid=app_pid,
        generation=generation,
        timestamp=ts,
        confidence=confidence,
        geometry=geom,
        semantic_nodes=nodes,
        content_hash=_content_hash_for(SOURCE_AX, (window_id, app_pid), generation, geom, nodes, None),
    )


def observe_dib(
    snapshot: DibSnapshot,
    *,
    geometry: Geometry | None = None,
    confidence: float = 0.8,
    timestamp: float | None = None,
    clock=time.time,
) -> Observation:
    """Build a DIB observation from a parsed Direct Inspection Buffer.

    Window, process and generation are the buffer's own claims (the header);
    fusion checks them against AX rather than trusting them, so a buffer that
    names another window is set aside as a conflict. Confidence defaults just
    below AX: the semantics come from the application itself, but its identity
    claim is self-reported. Geometry defaults to the union of the root nodes'
    bounds; the time to the writer's timestamp, else the local clock."""
    geom = geometry or snapshot.bounds()
    if timestamp is not None:
        ts = timestamp
    elif snapshot.timestamp_ns:
        ts = snapshot.timestamp
    else:
        ts = _now(clock)
    nodes = snapshot.semantic_nodes()
    window = (snapshot.window_id, snapshot.app_pid)
    return Observation(
        source=SOURCE_DIB,
        window_id=snapshot.window_id,
        app_pid=snapshot.app_pid,
        generation=snapshot.generation,
        timestamp=ts,
        confidence=confidence,
        geometry=geom,
        semantic_nodes=nodes,
        dib=snapshot,
        content_hash=_content_hash_for(SOURCE_DIB, window, snapshot.generation, geom, nodes, snapshot),
    )


# ---------------------------------------------------------------------------
# Conflicts and fusion result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ObservationConflict:
    """A recorded disagreement between two senses. Never resolved by silently
    dropping one side; it lowers fused confidence and is handed upward so the
    action-plan gate (Phase 2.6) can demand a re-observe."""

    kind: str
    a_source: str
    b_source: str
    detail: str = ""
    severity: float = 0.5  # 0..1, informational weight for confidence penalty


@dataclass(frozen=True)
class FusionResult:
    fused: Observation
    conflicts: tuple[ObservationConflict, ...] = ()
    contributors: tuple[Observation, ...] = ()  # fresh, same-window, fused in
    stale: tuple[Observation, ...] = ()  # older generation, excluded
    foreign: tuple[Observation, ...] = ()  # different window, excluded

    @property
    def clean(self) -> bool:
        return not self.conflicts


@dataclass(frozen=True)
class FusionConfig:
    geometry_iou_threshold: float = 0.6
    time_skew_ms: float = 750.0
    conflict_penalty: float = 0.75  # multiply confidence per distinct conflict kind
    min_confidence: float = 0.05


# ---------------------------------------------------------------------------
# Performance budgets (hard limits, enforced from Phase 2.4 on)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Budget:
    """Hard perception limits. Over budget is a violation to be surfaced (and,
    on the fail-closed path, refused) -- not silently truncated evidence."""

    max_ax_nodes: int = 4000
    max_ax_ipc_calls: int = 2000
    max_observation_latency_ms: float = 1500.0
    max_dib_nodes: int = 20_000


@dataclass(frozen=True)
class BudgetViolation:
    kind: str
    limit: float
    actual: float
    detail: str = ""


class BudgetExceeded(RuntimeError):
    def __init__(self, violations: Sequence[BudgetViolation]):
        self.violations = tuple(violations)
        super().__init__("; ".join(f"{v.kind}: {v.actual}>{v.limit}" for v in violations))


def check_budget(
    *,
    budget: Budget,
    ax_nodes: int = 0,
    ax_ipc_calls: int = 0,
    latency_ms: float = 0.0,
    dib_nodes: int = 0,
) -> tuple[BudgetViolation, ...]:
    """Return every exceeded hard limit (empty tuple == within budget)."""
    checks = (
        ("ax_node_budget", ax_nodes, budget.max_ax_nodes),
        ("ax_ipc_budget", ax_ipc_calls, budget.max_ax_ipc_calls),
        ("observation_latency_budget", latency_ms, budget.max_observation_latency_ms),
        ("dib_node_budget", dib_nodes, budget.max_dib_nodes),
    )
    return tuple(
        BudgetViolation(kind=kind, limit=float(limit), actual=float(actual))
        for kind, actual, limit in checks
        if actual > limit
    )


def enforce_budget(**kwargs) -> None:
    """Fail-closed wrapper: raise ``BudgetExceeded`` if any hard limit is over.
    Same keyword arguments as :func:`check_budget`."""
    violations = check_budget(**kwargs)
    if violations:
        raise BudgetExceeded(violations)


# ---------------------------------------------------------------------------
# Fusion
# ---------------------------------------------------------------------------


def _anchor_window(observations: Sequence[Observation]) -> tuple[int, int]:
    # The most-trusted, highest-confidence reading names the window we fuse.
    best = max(
        observations,
        key=lambda o: (o.confidence, _SOURCE_RANK.get(o.source, 0), -o.timestamp),
    )
    return best.window


def _pick(observations: Sequence[Observation], source: str) -> Observation | None:
    cands = [o for o in observations if o.source == source]
    if not cands:
        return None
    return max(cands, key=lambda o: (o.confidence, o.generation, -o.timestamp))


def fuse(observations: Iterable[Observation], *, config: FusionConfig | None = None) -> FusionResult:
    """Fuse observations of (ideally) one window into a single ``fused``
    observation plus the conflicts found. Deterministic and side-effect free.

    Rules, all fail-toward-surfacing:
      * observations of a *different* window are set aside (window-identity
        conflict), never blended in;
      * observations of an *older generation* than the freshest are set aside
        (generation-skew conflict) -- stale coordinates are not fused;
      * geometries that disagree beyond the IoU threshold raise a geometry
        conflict but both are kept in the record;
      * timestamps spanning more than the skew budget raise a time conflict;
      * each distinct conflict kind multiplies fused confidence down.
    """
    cfg = config or FusionConfig()
    obs = list(observations)
    if not obs:
        raise ValueError("fuse() requires at least one observation")

    anchor = _anchor_window(obs)
    same_window = [o for o in obs if o.window == anchor]
    foreign = [o for o in obs if o.window != anchor]

    conflicts: list[ObservationConflict] = []
    anchor_source = (_pick(same_window, SOURCE_AX) or same_window[0]).source
    for o in foreign:
        conflicts.append(
            ObservationConflict(
                kind=CONFLICT_WINDOW_IDENTITY,
                a_source=anchor_source,
                b_source=o.source,
                detail=f"anchor window {anchor} vs {o.window}",
                severity=1.0,
            )
        )

    freshest = max(o.generation for o in same_window)
    contributors = [o for o in same_window if o.generation == freshest]
    stale = [o for o in same_window if o.generation < freshest]
    for o in stale:
        conflicts.append(
            ObservationConflict(
                kind=CONFLICT_GENERATION,
                a_source=o.source,
                b_source=SOURCE_FUSED,
                detail=f"generation {o.generation} < freshest {freshest}",
                severity=0.8,
            )
        )

    # Geometry agreement among fresh contributors that actually have bounds.
    geo = [o for o in contributors if o.geometry.valid]
    for i in range(len(geo)):
        for j in range(i + 1, len(geo)):
            iou = geo[i].geometry.iou(geo[j].geometry)
            if iou < cfg.geometry_iou_threshold:
                conflicts.append(
                    ObservationConflict(
                        kind=CONFLICT_GEOMETRY,
                        a_source=geo[i].source,
                        b_source=geo[j].source,
                        detail=f"iou={iou:.2f} < {cfg.geometry_iou_threshold:.2f}",
                        severity=1.0 - iou,
                    )
                )

    # Time consistency across fresh contributors.
    times = [o.timestamp for o in contributors if o.timestamp]
    if len(times) >= 2:
        span_ms = (max(times) - min(times)) * 1000.0
        if span_ms > cfg.time_skew_ms:
            conflicts.append(
                ObservationConflict(
                    kind=CONFLICT_TIME,
                    a_source="oldest",
                    b_source="newest",
                    detail=f"span {span_ms:.0f}ms > {cfg.time_skew_ms:.0f}ms",
                    severity=min(1.0, span_ms / (cfg.time_skew_ms * 4)),
                )
            )

    ax = _pick(contributors, SOURCE_AX)
    dib = _pick(contributors, SOURCE_DIB)

    # Fused geometry: the highest-ranked source that actually has bounds.
    with_geometry = sorted(
        (o for o in contributors if o.geometry.valid),
        key=lambda o: (_SOURCE_RANK.get(o.source, 0), o.confidence),
        reverse=True,
    )
    fused_geometry = with_geometry[0].geometry if with_geometry else Geometry()
    # AX first (primary identity), then the DIB nodes AX does not already have --
    # this is where a canvas / self-drawn region AX sees as one opaque box gains
    # its structure.
    fused_nodes = ax.semantic_nodes if ax is not None else ()
    if dib is not None:
        have = {n.key() for n in fused_nodes}
        fused_nodes = fused_nodes + tuple(n for n in dib.semantic_nodes if n.key() not in have)
    fused_dib = dib.dib if dib is not None else None

    base_conf = max(o.confidence for o in contributors)
    distinct_conflict_kinds = {c.kind for c in conflicts}
    conf = base_conf * (cfg.conflict_penalty ** len(distinct_conflict_kinds))
    conf = max(cfg.min_confidence, min(1.0, conf))

    window_id, app_pid = anchor
    fused_ts = max(o.timestamp for o in contributors)
    fused = Observation(
        source=SOURCE_FUSED,
        window_id=window_id,
        app_pid=app_pid,
        generation=freshest,
        timestamp=fused_ts,
        confidence=conf,
        geometry=fused_geometry,
        semantic_nodes=fused_nodes,
        dib=fused_dib,
        content_hash=_content_hash_for(
            SOURCE_FUSED, anchor, freshest, fused_geometry, fused_nodes, fused_dib
        ),
    )
    # Deterministic conflict order: by kind then sources.
    conflicts.sort(key=lambda c: (c.kind, c.a_source, c.b_source, c.detail))
    return FusionResult(
        fused=fused,
        conflicts=tuple(conflicts),
        contributors=tuple(contributors),
        stale=tuple(stale),
        foreign=tuple(foreign),
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
    "observe_ax",
    "observe_dib",
    "ObservationConflict",
    "FusionResult",
    "FusionConfig",
    "Budget",
    "BudgetViolation",
    "BudgetExceeded",
    "check_budget",
    "enforce_budget",
    "fuse",
]
