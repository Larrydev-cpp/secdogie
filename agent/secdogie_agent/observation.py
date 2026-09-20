"""Observation Fusion (Phase 2.4): one honest view of a window from many senses.

The agent perceives a window three ways, each with different trust and cost:

  * ``ax``     -- the accessibility tree (macOS AX / Windows UIA). PRIMARY: it
                  carries semantic identity (role, name, automation id), which is
                  what targeting and the Socratic gate reason about.
  * ``dib``    -- a device-independent bitmap reconstructed *read-only* from
                  process memory by the native ``atlas`` module. VERIFICATION:
                  proves something was actually drawn where AX says it is.
  * ``pixel``  -- a screen/window capture (mss / CGWindowListCreateImage).
                  FALLBACK: used only when AX is empty or the control is
                  owner-drawn.

This module fuses those into a single ``Observation`` and -- this is the point --
it *never silently overwrites* one sense with another. When AX and the DIB
disagree about geometry, when one reading is a stale generation, or when two
readings claim different windows, that is recorded as an ``ObservationConflict``
and the fused confidence drops. Perception that hides its own disagreement is how
an autonomous agent clicks the wrong thing; fail toward surfacing the conflict.

Two boundaries make this an honest bridge rather than a rewrite:

  * **DIB by reference, not by copy.** ``VisualReference`` carries the DIB's
    identity (address, dimensions, bit depth, a content hash) but NOT its pixel
    bytes. The native ``atlas`` binary already reconstructs the bitmap; Python
    holds a handle to it, so the "DIB processing budget" stays bounded and large
    visual data never bloats the Python heap or the event journal.
  * **Read-only.** Nothing here reads, writes, or injects into another process.
    It consumes already-captured, read-only evidence and reasons about it. There
    is no capture, no memory write, no anti-detection -- pure fusion logic, which
    is why it runs headless on CI with no desktop.

Everything is a pure function of its inputs, so the whole module is deterministic
and testable on Linux with no AX, no DIB, and no screen.
"""
from __future__ import annotations

import hashlib
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Sources and conflict kinds (string constants so JSON / journal stay stable)
# ---------------------------------------------------------------------------

SOURCE_AX = "ax"
SOURCE_DIB = "dib"
SOURCE_PIXEL = "pixel"
SOURCE_FUSED = "fused"
_SOURCES = frozenset({SOURCE_AX, SOURCE_DIB, SOURCE_PIXEL, SOURCE_FUSED})

# Trust order used only to break ties when picking a fused geometry / identity;
# it is NOT a licence to overwrite -- a disagreement is still a conflict.
_SOURCE_RANK = {SOURCE_AX: 3, SOURCE_DIB: 2, SOURCE_PIXEL: 1, SOURCE_FUSED: 0}

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
# Semantic nodes (the AX contribution) and visual references (the DIB bridge)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SemanticNode:
    """A minimal, hashable projection of one AX/UIA node. Identity-first: what
    targeting needs, without dragging the whole platform tree into fusion."""

    role: str = ""
    name: str = ""
    automation_id: str = ""
    bounds: Geometry = field(default_factory=Geometry)
    enabled: bool = True

    def key(self) -> tuple[str, str, str]:
        return (self.role, self.name, self.automation_id)


@dataclass(frozen=True)
class VisualReference:
    """A *handle* to a reconstructed DIB -- its identity, never its pixels.

    The native ``atlas`` module reconstructs the bitmap from read-only process
    memory; this reference records only what is needed to (a) prove a bitmap of
    the right shape exists and (b) tell whether it changed (``content_hash``).
    The pixel bytes stay on the native side, so this is bounded and cheap. That
    is the "DIB by reference, not copy" rule the plan requires."""

    address: int = 0
    width: int = 0
    height: int = 0
    bit_count: int = 0
    compression: int = 0
    source: str = "heap"  # native DibHit.source: "heap" (memory DIB) etc.
    content_hash: str = ""
    pixels_available: bool = False

    @property
    def approx_bytes(self) -> int:
        """The DIB's nominal size, used only for the DIB processing budget.
        Zero-size when dimensions are unknown."""
        if self.width <= 0 or self.height <= 0 or self.bit_count <= 0:
            return 0
        return self.width * self.height * ((self.bit_count + 7) // 8)

    @classmethod
    def from_dib_json(cls, dib: dict) -> VisualReference:
        """Bridge one native ``dibs[]`` JSON object (as emitted by
        ``inspect_json.cpp``: address/width/height/bit_count/compression/source
        and a base64 ``preview``) into a reference.

        The ``preview`` bytes, when present, are hashed *transiently* to fix the
        content identity and then dropped -- they are never retained in Python.
        With no preview, the hash falls back to the DIB's structural identity so
        a reference is still stable and comparable."""
        import base64

        width = int(dib.get("width", 0))
        height = int(dib.get("height", 0))
        bit_count = int(dib.get("bit_count", 0))
        compression = int(dib.get("compression", 0))
        address = int(dib.get("address", 0))
        source = str(dib.get("source", "heap")) or "heap"
        preview = dib.get("preview")
        pixels_available = False
        if isinstance(preview, str) and preview:
            try:
                raw = base64.b64decode(preview, validate=True)
                content_hash = hashlib.sha256(raw).hexdigest()
                pixels_available = True
                del raw  # do not keep the pixels: reference, not copy
            except (ValueError, TypeError):
                content_hash = _structural_dib_hash(address, width, height, bit_count, compression)
        else:
            content_hash = _structural_dib_hash(address, width, height, bit_count, compression)
        return cls(
            address=address,
            width=width,
            height=height,
            bit_count=bit_count,
            compression=compression,
            source=source,
            content_hash=content_hash,
            pixels_available=pixels_available,
        )


def _structural_dib_hash(address: int, w: int, h: int, bit_count: int, compression: int) -> str:
    material = f"dib:{address}:{w}x{h}:{bit_count}:{compression}".encode()
    return hashlib.sha256(material).hexdigest()


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
    visual_reference: VisualReference | None = None
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
    visual_reference: VisualReference | None,
) -> str:
    parts = [
        source,
        f"{window[0]}:{window[1]}",
        str(generation),
        f"{geometry.x},{geometry.y},{geometry.w},{geometry.h}",
    ]
    for n in semantic_nodes:
        parts.append("|".join(n.key()))
    if visual_reference is not None:
        parts.append(f"vref:{visual_reference.content_hash}")
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
    *,
    window_id: int,
    app_pid: int,
    visual_reference: VisualReference,
    geometry: Geometry | None = None,
    generation: int = 0,
    confidence: float = 0.7,
    timestamp: float | None = None,
    clock=time.time,
) -> Observation:
    """Build a DIB observation from a ``VisualReference`` (already a handle, not
    pixels). Medium confidence: it proves a bitmap exists, not what it means."""
    geom = geometry or Geometry(0, 0, visual_reference.width, visual_reference.height)
    ts = _now(clock) if timestamp is None else timestamp
    return Observation(
        source=SOURCE_DIB,
        window_id=window_id,
        app_pid=app_pid,
        generation=generation,
        timestamp=ts,
        confidence=confidence,
        geometry=geom,
        visual_reference=visual_reference,
        content_hash=_content_hash_for(
            SOURCE_DIB, (window_id, app_pid), generation, geom, (), visual_reference
        ),
    )


def observe_pixel(
    *,
    window_id: int,
    app_pid: int,
    geometry: Geometry,
    content_hash: str = "",
    generation: int = 0,
    confidence: float = 0.4,
    timestamp: float | None = None,
    clock=time.time,
) -> Observation:
    """Build a pixel/window-capture observation. Lowest default confidence: it
    is the owner-drawn fallback when AX is empty."""
    ts = _now(clock) if timestamp is None else timestamp
    ch = content_hash or _content_hash_for(SOURCE_PIXEL, (window_id, app_pid), generation, geometry, (), None)
    return Observation(
        source=SOURCE_PIXEL,
        window_id=window_id,
        app_pid=app_pid,
        generation=generation,
        timestamp=ts,
        confidence=confidence,
        geometry=geometry,
        content_hash=ch,
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
    max_dib_bytes: int = 8 * 1024 * 1024
    max_pixel_fallback_bytes: int = 8 * 1024 * 1024


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
    dib_bytes: int = 0,
    pixel_bytes: int = 0,
) -> tuple[BudgetViolation, ...]:
    """Return every exceeded hard limit (empty tuple == within budget)."""
    checks = (
        ("ax_node_budget", ax_nodes, budget.max_ax_nodes),
        ("ax_ipc_budget", ax_ipc_calls, budget.max_ax_ipc_calls),
        ("observation_latency_budget", latency_ms, budget.max_observation_latency_ms),
        ("dib_processing_budget", dib_bytes, budget.max_dib_bytes),
        ("pixel_fallback_budget", pixel_bytes, budget.max_pixel_fallback_bytes),
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
    fused_nodes = ax.semantic_nodes if ax is not None else ()
    fused_visual = dib.visual_reference if dib is not None else None

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
        visual_reference=fused_visual,
        content_hash=_content_hash_for(
            SOURCE_FUSED, anchor, freshest, fused_geometry, fused_nodes, fused_visual
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
    "SOURCE_PIXEL",
    "SOURCE_FUSED",
    "CONFLICT_WINDOW_IDENTITY",
    "CONFLICT_GENERATION",
    "CONFLICT_GEOMETRY",
    "CONFLICT_TIME",
    "Geometry",
    "SemanticNode",
    "VisualReference",
    "Observation",
    "observe_ax",
    "observe_dib",
    "observe_pixel",
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
