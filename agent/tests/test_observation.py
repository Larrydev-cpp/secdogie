"""Headless tests for Observation Fusion (Phase 2.4).

No AX, no DIB, no screen: fusion and conflict detection are pure functions of
their inputs, so every rule is checked on Linux CI. The DIB bridge is exercised
against the exact JSON shape ``native/atlas/src/inspect_json.cpp`` emits."""
from __future__ import annotations

import base64
import hashlib

import pytest
from secdogie_agent.observation import (
    CONFLICT_GENERATION,
    CONFLICT_GEOMETRY,
    CONFLICT_TIME,
    CONFLICT_WINDOW_IDENTITY,
    SOURCE_AX,
    SOURCE_DIB,
    SOURCE_FUSED,
    Budget,
    BudgetExceeded,
    FusionConfig,
    Geometry,
    Observation,
    SemanticNode,
    VisualReference,
    check_budget,
    enforce_budget,
    fuse,
    observe_ax,
    observe_dib,
)

# --- Geometry ---------------------------------------------------------------


def test_geometry_iou_identical_is_one_and_disjoint_is_zero():
    a = Geometry(0, 0, 100, 100)
    assert a.iou(a) == pytest.approx(1.0)
    assert a.iou(Geometry(200, 200, 10, 10)) == 0.0
    assert Geometry(0, 0, 0, 0).valid is False
    assert Geometry(0, 0, 0, 0).iou(a) == 0.0


def test_geometry_iou_partial_overlap():
    a = Geometry(0, 0, 10, 10)
    b = Geometry(5, 0, 10, 10)  # half overlap -> inter=50, union=150
    assert a.iou(b) == pytest.approx(50 / 150)


# --- DIB bridge (reference, not copy) ---------------------------------------


def _dib_json(preview_bytes: bytes | None):
    obj = {
        "address": 0x7F0000,
        "width": 4,
        "height": 2,
        "bit_count": 32,
        "compression": 0,
        "source": "heap",
        "preview": None if preview_bytes is None else base64.b64encode(preview_bytes).decode(),
    }
    return obj


def test_visual_reference_hashes_preview_then_drops_the_pixels():
    pixels = bytes(range(32))  # 4x2 RGBA
    ref = VisualReference.from_dib_json(_dib_json(pixels))
    # identity carried
    assert ref.width == 4 and ref.height == 2 and ref.bit_count == 32
    assert ref.source == "heap" and ref.pixels_available is True
    # content hash equals sha256 of the raw pixels...
    assert ref.content_hash == hashlib.sha256(pixels).hexdigest()
    # ...but the pixels themselves are NOT retained anywhere on the reference.
    assert not hasattr(ref, "rgba")
    assert pixels not in repr(ref).encode(errors="ignore")


def test_visual_reference_without_preview_is_structurally_stable():
    ref1 = VisualReference.from_dib_json(_dib_json(None))
    ref2 = VisualReference.from_dib_json(_dib_json(None))
    assert ref1.pixels_available is False
    assert ref1.content_hash == ref2.content_hash  # deterministic structural id
    assert ref1.content_hash != ""


def test_visual_reference_approx_bytes_for_budget():
    ref = VisualReference.from_dib_json(_dib_json(None))
    assert ref.approx_bytes == 4 * 2 * 4  # w*h*(bit_count/8)


# --- Observation construction -----------------------------------------------


def test_unknown_source_is_rejected():
    with pytest.raises(ValueError):
        Observation(source="telepathy", window_id=1, app_pid=2)


def test_observe_helpers_set_expected_defaults():
    ax = observe_ax(window_id=1, app_pid=9, timestamp=100.0)
    assert ax.source == SOURCE_AX and ax.confidence == 0.9 and ax.window == (1, 9)
    dib = observe_dib(
        window_id=1,
        app_pid=9,
        visual_reference=VisualReference(width=10, height=10, bit_count=24),
        timestamp=100.0,
    )
    assert dib.source == SOURCE_DIB and dib.geometry.valid  # defaulted from the DIB size


# --- Fusion: the happy path -------------------------------------------------


def _same_window_pair():
    geom = Geometry(0, 0, 100, 40)
    ax = observe_ax(
        window_id=7,
        app_pid=1234,
        semantic_nodes=(SemanticNode(role="button", name="Save", automation_id="save"),),
        geometry=geom,
        generation=5,
        timestamp=100.0,
    )
    ref = VisualReference.from_dib_json(_dib_json(bytes(32)))
    dib = observe_dib(
        window_id=7, app_pid=1234, visual_reference=ref, geometry=geom, generation=5, timestamp=100.1
    )
    return ax, dib


def test_fuse_agreeing_sources_is_clean_and_keeps_both_contributions():
    ax, dib = _same_window_pair()
    res = fuse([ax, dib])
    assert res.clean
    assert res.fused.source == SOURCE_FUSED
    assert res.fused.window == (7, 1234)
    # semantic identity comes from AX, the visual handle from the DIB
    assert res.fused.semantic_nodes and res.fused.semantic_nodes[0].name == "Save"
    assert res.fused.visual_reference is not None
    assert res.fused.confidence >= 0.9  # no conflicts, no penalty
    assert set(res.contributors) == {ax, dib}


def test_fuse_requires_at_least_one():
    with pytest.raises(ValueError):
        fuse([])


def test_fuse_single_observation_passes_through():
    ax = observe_ax(window_id=1, app_pid=2, geometry=Geometry(0, 0, 5, 5), timestamp=1.0)
    res = fuse([ax])
    assert res.clean and res.fused.geometry == Geometry(0, 0, 5, 5)


# --- Fusion: conflicts are surfaced, never silently overwritten -------------


def test_geometry_mismatch_is_a_conflict_and_lowers_confidence():
    ax = observe_ax(window_id=7, app_pid=1, geometry=Geometry(0, 0, 100, 40), generation=1, timestamp=1.0)
    dib = observe_dib(
        window_id=7,
        app_pid=1,
        visual_reference=VisualReference(width=100, height=40, bit_count=24),
        geometry=Geometry(500, 500, 100, 40),
        generation=1,
        timestamp=1.0,
    )
    res = fuse([ax, dib])
    kinds = {c.kind for c in res.conflicts}
    assert CONFLICT_GEOMETRY in kinds
    assert res.fused.confidence < 0.9  # penalized for the disagreement
    # both readings are still present in the record
    assert ax in res.contributors and dib in res.contributors


def test_stale_generation_is_excluded_not_fused():
    fresh = observe_ax(
        window_id=7,
        app_pid=1,
        semantic_nodes=(SemanticNode(name="new"),),
        geometry=Geometry(0, 0, 10, 10),
        generation=9,
        timestamp=2.0,
    )
    stale = observe_ax(
        window_id=7,
        app_pid=1,
        semantic_nodes=(SemanticNode(name="OLD"),),
        geometry=Geometry(900, 900, 10, 10),
        generation=3,
        timestamp=1.0,
    )
    res = fuse([fresh, stale])
    assert CONFLICT_GENERATION in {c.kind for c in res.conflicts}
    assert stale in res.stale and stale not in res.contributors
    # fused geometry/semantics come from the fresh generation only
    assert res.fused.generation == 9
    assert res.fused.geometry == Geometry(0, 0, 10, 10)
    assert res.fused.semantic_nodes[0].name == "new"


def test_foreign_window_is_set_aside():
    a = observe_ax(window_id=1, app_pid=1, geometry=Geometry(0, 0, 10, 10), timestamp=1.0, confidence=0.9)
    other = observe_dib(
        window_id=2,
        app_pid=2,
        visual_reference=VisualReference(width=10, height=10, bit_count=24),
        geometry=Geometry(0, 0, 10, 10),
        timestamp=1.0,
        confidence=0.4,
    )
    res = fuse([a, other])
    assert CONFLICT_WINDOW_IDENTITY in {c.kind for c in res.conflicts}
    assert other in res.foreign and other not in res.contributors
    assert res.fused.window == (1, 1)  # anchored on the higher-confidence reading


def test_time_skew_is_flagged():
    cfg = FusionConfig(time_skew_ms=100.0)
    ax = observe_ax(window_id=7, app_pid=1, geometry=Geometry(0, 0, 10, 10), generation=1, timestamp=1.0)
    dib = observe_dib(
        window_id=7,
        app_pid=1,
        visual_reference=VisualReference(width=10, height=10, bit_count=24),
        geometry=Geometry(0, 0, 10, 10),
        generation=1,
        timestamp=1.5,  # 500ms later, over the 100ms budget
    )
    res = fuse([ax, dib], config=cfg)
    assert CONFLICT_TIME in {c.kind for c in res.conflicts}


def test_fusion_is_deterministic_regardless_of_input_order():
    ax, dib = _same_window_pair()
    r1 = fuse([ax, dib])
    r2 = fuse([dib, ax])
    assert r1.fused.content_hash == r2.fused.content_hash
    assert [c.kind for c in r1.conflicts] == [c.kind for c in r2.conflicts]


# --- Budgets: hard limits ---------------------------------------------------


def test_check_budget_flags_each_exceeded_limit():
    b = Budget(max_ax_nodes=10, max_dib_bytes=1000)
    vio = check_budget(budget=b, ax_nodes=11, dib_bytes=2000)
    kinds = {v.kind for v in vio}
    assert kinds == {"ax_node_budget", "dib_processing_budget"}


def test_check_budget_within_limits_is_empty():
    b = Budget()
    assert check_budget(budget=b, ax_nodes=1, ax_ipc_calls=1, latency_ms=1.0) == ()


def test_enforce_budget_is_fail_closed():
    b = Budget(max_observation_latency_ms=50.0)
    with pytest.raises(BudgetExceeded) as ei:
        enforce_budget(budget=b, latency_ms=999.0)
    assert ei.value.violations[0].kind == "observation_latency_budget"
