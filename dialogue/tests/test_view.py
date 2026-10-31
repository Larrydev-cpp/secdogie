"""The unified view surface: tree vs geometry, the toggle, resize, and status."""
from __future__ import annotations

from secdogie_dialogue.inspector import EMPTY, apply
from secdogie_dialogue.protocol import DibRef, NodeDelta, NodeOp, StateSnapshotPacket
from secdogie_dialogue.view import InspectorView, ViewMode, render_view

A = NodeOp.ADD


def _state(focus=-1, dib=()):
    pkt = StateSnapshotPacket(42, 7, 1, (
        NodeDelta(A, 0, role="AXWindow", name="Drawing", bounds=(0, 0, 800, 600)),
        NodeDelta(A, 1, role="AXButton", name="Save", parent_index=0, is_interactive=True, bounds=(10, 20, 160, 40)),
        NodeDelta(A, 2, role="canvas", parent_index=0, bounds=(0, 80, 800, 520)),
    ), dib_references=dib, focused_node_index=focus, full=True)
    return apply(EMPTY, pkt)


DIB = DibRef(2, 800, 520, "BGRA8888", "a1b2c3d4e5")


def test_tree_mode_is_the_inspector_listing():
    from secdogie_dialogue.inspector import render_lines

    s = _state()
    assert render_view(s, ViewMode.TREE) == render_lines(s)
    assert any('AXButton "Save"' in line for line in render_view(s, ViewMode.TREE))


def test_geometry_mode_is_the_radar():
    lines = render_view(_state(dib=(DIB,)), ViewMode.GEOMETRY)
    assert lines[0].startswith("window#42")  # inspector header
    assert any("░" in line for line in lines)  # the DIB blind region


def test_default_mode_is_tree():
    assert render_view(_state()) == render_view(_state(), ViewMode.TREE)


def test_toggle_flips_between_modes():
    v = InspectorView().with_state(_state())
    assert v.mode is ViewMode.TREE
    g = v.toggle()
    assert g.mode is ViewMode.GEOMETRY
    assert g.toggle().mode is ViewMode.TREE
    assert v.mode is ViewMode.TREE  # immutable: original unchanged


def test_with_state_keeps_the_mode():
    v = InspectorView().toggle()  # geometry
    v2 = v.with_state(_state())
    assert v2.mode is ViewMode.GEOMETRY and v2.state.window_id == 42


def test_lines_match_the_mode():
    v = InspectorView().with_state(_state(dib=(DIB,)))
    assert v.lines() == render_view(v.state, ViewMode.TREE)
    assert v.toggle().lines() == render_view(v.state, ViewMode.GEOMETRY, radar_options=v.radar_options)


def test_resize_changes_the_radar_but_not_the_tree():
    v = InspectorView().with_state(_state()).with_mode(ViewMode.GEOMETRY).resized(60, 20)
    lines = v.lines()
    assert len(lines) == 1 + 20  # header + 20 radar rows
    assert all(len(line) <= 60 or "window#42" in line for line in lines)
    # The tree view ignores the radar size.
    tree = v.with_mode(ViewMode.TREE)
    assert tree.lines() == render_view(v.state, ViewMode.TREE)


def test_status_names_the_mode_and_next_key():
    v = InspectorView().with_state(_state())
    assert "structural tree" in v.status() and "tab → geometry" in v.status()
    assert "geometry radar" in v.toggle().status() and "tab → tree" in v.toggle().status()


def test_status_flags_resync():
    good = _state()
    bad = apply(good, StateSnapshotPacket(42, 7, 2, (NodeDelta(NodeOp.UPDATE, 99),), base_generation=1))
    assert "⚠ resync" in InspectorView().with_state(bad).status()
    assert "(ok)" in InspectorView().with_state(good).status()


def test_empty_view_renders_in_both_modes():
    v = InspectorView()
    assert v.lines()  # tree: waiting-for-snapshot line
    assert v.toggle().lines()  # geometry: header + blank radar, no crash


def test_mode_label_and_values():
    assert ViewMode.TREE.label == "structural tree"
    assert ViewMode.GEOMETRY.label == "geometry radar"
    assert [m.value for m in ViewMode] == ["tree", "geometry"]


def test_view_is_deterministic():
    a = InspectorView().with_state(_state(focus=1, dib=(DIB,))).toggle()
    b = InspectorView().with_state(_state(focus=1, dib=(DIB,))).toggle()
    assert a.lines() == b.lines()
