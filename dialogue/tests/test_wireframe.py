"""The vector wireframe: layout transform, semantic styling, DIB blind regions,
focus, stale state, and the two renderers (SVG, terminal radar) -- all from
structure alone, never pixels."""
from __future__ import annotations

import unicodedata
import xml.etree.ElementTree as ET

import pytest
from secdogie_dialogue.inspector import EMPTY, apply
from secdogie_dialogue.protocol import DibRef, NodeDelta, NodeOp, StateSnapshotPacket
from secdogie_dialogue.wireframe import (
    DIB_FILL,
    RADAR_OPTIONS,
    ShapeKind,
    VectorWireframeEngine,
    WireframeOptions,
    render_radar,
    to_svg,
    to_text_grid,
)

A, U, R = NodeOp.ADD, NodeOp.UPDATE, NodeOp.REMOVE
SQUARE = WireframeOptions(width=100, height=100, pad=0)


def _nodes(*extra, window=(0, 0, 800, 600)):
    return (
        NodeDelta(A, 0, role="AXWindow", name="Drawing", bounds=window),
        NodeDelta(A, 1, role="AXGroup", name="Toolbar", parent_index=0, bounds=(0, 0, 800, 80)),
        NodeDelta(A, 2, role="AXButton", name="Save", parent_index=1, is_interactive=True, bounds=(10, 20, 160, 40)),
        NodeDelta(A, 3, role="canvas", parent_index=0, bounds=(0, 80, 800, 520)),
        *extra,
    )


def _full(*extra, gen=1, focus=-1, dib=(), window=(0, 0, 800, 600)):
    return StateSnapshotPacket(42, 7, gen, _nodes(*extra, window=window), dib_references=dib,
                               focused_node_index=focus, full=True)


def _state(*extra, **kw):
    return apply(EMPTY, _full(*extra, **kw))


def _shape(wf, index, kind=None):
    return next(s for s in wf.shapes if s.index == index and (kind is None or s.kind is kind))


DIB = DibRef(3, 800, 520, "BGRA8888", "a1b2c3d4e5f6")


# ---- layout transform -----------------------------------------------------------


def test_bounds_are_xywh_and_fill_the_viewport_preserving_aspect():
    wf = VectorWireframeEngine(WireframeOptions(width=800, height=600, pad=0)).build(_state())
    assert wf.world == (0, 0, 800, 600) and wf.scale == 1.0
    assert _shape(wf, 0).rect == (0, 0, 800, 600)
    assert _shape(wf, 2).rect == (10, 20, 160, 40)  # (x, y, w, h), not (l, t, r, b)


def test_letterboxing_centres_the_world():
    wf = VectorWireframeEngine(SQUARE).build(_state())  # 800x600 into 100x100
    assert wf.scale == pytest.approx(100 / 800)
    x, y, w, h = _shape(wf, 0).rect
    # 800x600 -> 100x75, letterboxed vertically; ±1 for the edge rounding.
    assert (x, w) == (0, 100) and h in (74, 75, 76) and abs(y - (100 - h) // 2) <= 1


def test_cell_aspect_compresses_rows():
    flat = VectorWireframeEngine(WireframeOptions(width=200, height=100, pad=0)).build(_state())
    tall_cells = VectorWireframeEngine(WireframeOptions(width=200, height=100, pad=0, cell_aspect=2.0)).build(_state())
    _, _, w1, h1 = _shape(flat, 0).rect
    _, _, w2, h2 = _shape(tall_cells, 0).rect
    assert h1 / w1 == pytest.approx(0.75, abs=0.02)
    assert h2 / w2 == pytest.approx(0.375, abs=0.02)


def test_negative_coordinates_from_a_left_monitor():
    wf = VectorWireframeEngine(WireframeOptions(width=800, height=600, pad=0)).build(
        _state(window=(-1920, -200, 800, 600))
    )
    assert wf.world[:2] == (-1920, -200)
    assert _shape(wf, 0).rect == (0, 0, 800, 600)


def test_to_world_inverts_the_layout():
    wf = VectorWireframeEngine(WireframeOptions(width=333, height=211, pad=5, cell_aspect=2.0)).build(_state())
    x, y, w, h = _shape(wf, 3).rect  # canvas at (0, 80, 800, 520)
    wx, wy = wf.to_world(x, y)
    unit_x, unit_y = 1 / wf.scale, wf.cell_aspect / wf.scale  # one viewport unit in screen px
    assert abs(wx - 0) <= unit_x and abs(wy - 80) <= unit_y


def test_world_is_the_union_of_roots():
    second = NodeDelta(A, 9, role="AXWindow", name="Palette", bounds=(900, 0, 100, 300))
    wf = VectorWireframeEngine(SQUARE).build(_state(second))
    assert wf.world == (0, 0, 1000, 600)


# ---- edge cases -------------------------------------------------------------------


def test_empty_state_is_an_empty_wireframe():
    wf = VectorWireframeEngine().build(EMPTY)
    assert wf.shapes == () and wf.stale and wf.problem
    assert to_text_grid(VectorWireframeEngine(RADAR_OPTIONS).build(EMPTY)) == [" " * 100] * 30


def test_zero_area_and_tiny_nodes_are_dropped_and_counted():
    zero = NodeDelta(A, 5, role="AXStaticText", name="hidden", parent_index=0, bounds=(0, 0, 0, 0))
    tiny = NodeDelta(A, 6, role="AXButton", name="dot", parent_index=0, bounds=(5, 5, 2, 2))
    wf = VectorWireframeEngine(SQUARE).build(_state(zero, tiny))
    assert {s.index for s in wf.shapes}.isdisjoint({5, 6})
    assert wf.dropped_degenerate == 2


def test_offscreen_parts_are_clipped_and_counted():
    spill = NodeDelta(A, 5, role="AXPopover", name="menu", parent_index=0, bounds=(700, 500, 400, 400))
    wf = VectorWireframeEngine(WireframeOptions(width=800, height=600, pad=0)).build(_state(spill))
    x, y, w, h = _shape(wf, 5).rect
    assert x + w <= 800 and y + h <= 600 and wf.clipped == 1


def test_shape_cap_keeps_focus_dib_and_interactive_first():
    many = [NodeDelta(A, 10 + i, role="AXGroup", parent_index=0, bounds=(i * 10, 300, 8, 8)) for i in range(50)]
    wf = VectorWireframeEngine(WireframeOptions(width=800, height=600, pad=0, max_shapes=4)).build(
        _state(*many, focus=2, dib=(DIB,))
    )
    kinds = [s.kind for s in wf.shapes]
    assert ShapeKind.FOCUS in kinds and ShapeKind.DIB in kinds and ShapeKind.INTERACTIVE in kinds
    assert len(wf.shapes) == 4 and wf.dropped_overflow == len(many) + 4 + 1 - 4


# ---- semantic mapping ---------------------------------------------------------------


def test_kinds_follow_role_state_and_dib():
    disabled = NodeDelta(A, 5, role="AXButton", name="Undo", parent_index=1, is_interactive=True,
                         enabled=False, bounds=(200, 20, 100, 40))
    wf = VectorWireframeEngine().build(_state(disabled, dib=(DIB,)))
    assert _shape(wf, 0).kind is ShapeKind.FRAME
    assert _shape(wf, 2).kind is ShapeKind.INTERACTIVE
    assert _shape(wf, 3).kind is ShapeKind.DIB
    assert _shape(wf, 5).kind is ShapeKind.DISABLED


def test_dib_shape_carries_metadata_only():
    s = _shape(VectorWireframeEngine().build(_state(dib=(DIB,))), 3)
    assert s.dib == DIB
    assert s.label == "DIB 800x520 BGRA8888 #a1b2c3d4"


def test_focus_is_an_overlay_painted_last():
    wf = VectorWireframeEngine().build(_state(focus=2, dib=(DIB,)))
    assert wf.shapes[-1].kind is ShapeKind.FOCUS and wf.shapes[-1].index == 2
    assert _shape(wf, 2, ShapeKind.INTERACTIVE).rect == wf.shapes[-1].rect
    order = [s.kind for s in wf.shapes]
    assert order.index(ShapeKind.FRAME) < order.index(ShapeKind.INTERACTIVE) < order.index(ShapeKind.DIB)


# ---- inspector integration -------------------------------------------------------------


def test_stale_state_draws_the_last_consistent_tree():
    good = _state()
    bad = apply(good, StateSnapshotPacket(42, 7, 2, (NodeDelta(U, 99, role="AXButton"),)))
    assert bad.needs_resync
    wf = VectorWireframeEngine().build(bad)
    assert wf.stale and "unknown node 99" in wf.problem
    assert {s.index for s in wf.shapes} == {0, 1, 2, 3}
    assert "resync needed" in to_svg(wf)


def test_folded_deltas_match_the_equivalent_full_snapshot():
    engine = VectorWireframeEngine()
    moved = NodeDelta(U, 2, role="AXButton", name="Save", parent_index=1, is_interactive=True, bounds=(300, 20, 160, 40))
    folded = apply(_state(), StateSnapshotPacket(42, 7, 2, (moved,)))
    direct = StateSnapshotPacket(42, 7, 2, (*_nodes()[:2], moved, _nodes()[3]), full=True)
    assert engine.build(folded).shapes == engine.from_packet(direct).shapes


def test_from_packet_refuses_a_delta():
    with pytest.raises(ValueError, match="full snapshot"):
        VectorWireframeEngine().from_packet(StateSnapshotPacket(42, 7, 2, ()))


def test_from_packet_on_an_inconsistent_full_snapshot_is_stale():
    dup = StateSnapshotPacket(42, 7, 1, (_nodes()[0], _nodes()[0]), full=True)
    wf = VectorWireframeEngine().from_packet(dup)
    assert wf.stale and wf.shapes == ()


# ---- SVG ---------------------------------------------------------------------------------

_SVG_ALLOWED = {"svg", "defs", "pattern", "line", "rect", "text"}


def _hostile_state():
    evil = '<script>alert(1)</script> & "q" ‮'
    return _state(NodeDelta(A, 5, role="AXButton", name=evil, parent_index=0, is_interactive=True,
                            bounds=(100, 200, 600, 100)), dib=(DIB,))


def test_svg_is_well_formed_and_uses_only_whitelisted_elements():
    svg = to_svg(VectorWireframeEngine().build(_hostile_state()))
    root = ET.fromstring(svg)
    tags = {el.tag.split("}")[-1] for el in root.iter()}
    assert tags <= _SVG_ALLOWED


def test_svg_escapes_labels_and_carries_no_raster_or_links():
    svg = to_svg(VectorWireframeEngine().build(_hostile_state()))
    for banned in ("<script", "<image", "href", "data:", "<foreignObject", "‮"):
        assert banned not in svg
    assert "&lt;script&gt;" in svg and "&amp;" in svg and "&quot;" in svg


def test_svg_shapes_are_tagged_with_node_and_kind():
    root = ET.fromstring(to_svg(VectorWireframeEngine().build(_state(focus=2, dib=(DIB,)))))
    tagged = [(el.get("data-index"), el.get("data-kind")) for el in root.iter() if el.get("data-kind")]
    assert ("3", "dib") in tagged and ("2", "focus") in tagged and ("2", "interactive") in tagged


# ---- terminal radar ------------------------------------------------------------------------


def _display_width(line: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in line)


RADAR = WireframeOptions(width=120, height=30, pad=0, cell_aspect=2.0, min_size=1)


def test_radar_has_exact_dimensions_even_with_wide_characters():
    wide = NodeDelta(A, 5, role="AXButton", name="外墙图层尺寸", parent_index=1, is_interactive=True,
                     bounds=(560, 20, 220, 40))
    lines = to_text_grid(VectorWireframeEngine(RADAR).build(_state(wide)))
    assert len(lines) == 30
    assert all(_display_width(line) == 120 for line in lines)
    assert any("外墙" in line for line in lines)


def test_radar_marks_dib_interactive_and_focus():
    lines = to_text_grid(VectorWireframeEngine(RADAR).build(_state(focus=2, dib=(DIB,))))
    text = "\n".join(lines)
    assert DIB_FILL in text and "DIB 800x520" in text
    assert "╔" in text and "╝" in text  # focus double box
    assert 'AXButton "Save' in text  # the focus keeps the node's label visible


def test_later_label_on_a_shared_edge_leaves_no_residue():
    lines = to_text_grid(VectorWireframeEngine(RADAR).build(_state()))
    top = next(line for line in lines if "Toolbar" in line)
    assert 'AXGroup "Toolbar"─' in top  # window's longer label fully cleared


def test_one_row_shapes_are_bracketed_and_focus_uses_end_marks():
    coarse = WireframeOptions(width=80, height=12, pad=0, cell_aspect=2.0, min_size=1)
    plain = "\n".join(to_text_grid(VectorWireframeEngine(coarse).build(_state())))
    focused = "\n".join(to_text_grid(VectorWireframeEngine(coarse).build(_state(focus=2))))
    assert "[AXBut" in plain
    assert "«AXBut" in focused and "»" in focused


def test_render_radar_prepends_the_inspector_header():
    lines = render_radar(_state(focus=2))
    assert lines[0].startswith("window#42  pid 7  gen 1") and "focus=2" in lines[0]
    assert len(lines) == 1 + RADAR_OPTIONS.height


# ---- determinism / options ---------------------------------------------------------------------


def test_output_is_deterministic():
    a, b = _state(focus=2, dib=(DIB,)), _state(focus=2, dib=(DIB,))
    assert to_svg(VectorWireframeEngine().build(a)) == to_svg(VectorWireframeEngine().build(b))
    assert to_text_grid(VectorWireframeEngine(RADAR).build(a)) == to_text_grid(VectorWireframeEngine(RADAR).build(b))


@pytest.mark.parametrize(
    "kw",
    [
        {"width": 0},
        {"height": -1},
        {"pad": 50, "width": 100, "height": 100},
        {"cell_aspect": 0},
        {"max_shapes": -1},
        {"min_size": 0},
    ],
)
def test_invalid_options_are_rejected(kw):
    with pytest.raises(ValueError):
        WireframeOptions(**kw)
