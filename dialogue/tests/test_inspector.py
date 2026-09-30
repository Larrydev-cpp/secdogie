"""The structural view: full snapshots, deltas, stale and inconsistent packets,
focus and DIB metadata (never pixels), and the text rows the TUI shows."""
from __future__ import annotations

import threading

import pytest
from secdogie_dialogue.inspector import EMPTY, apply, clean, header_line, render_lines, rows
from secdogie_dialogue.protocol import DibRef, NodeDelta, NodeOp, StateSnapshotPacket

A, U, R = NodeOp.ADD, NodeOp.UPDATE, NodeOp.REMOVE


def _within(seconds, fn, *args):
    """Call fn(*args) but fail fast instead of hanging (a cycle the tree check
    missed would otherwise loop forever)."""
    box = {}
    t = threading.Thread(target=lambda: box.setdefault("v", fn(*args)), daemon=True)
    t.start()
    t.join(seconds)
    assert not t.is_alive(), f"{fn.__name__} did not return within {seconds}s"
    return box["v"]


def _full(gen=1, window=42, focus=-1, dib=()):
    return StateSnapshotPacket(window, 7, gen, (
        NodeDelta(A, 0, role="AXWindow", name="Drawing"),
        NodeDelta(A, 1, role="AXGroup", name="Toolbar", parent_index=0),
        NodeDelta(A, 2, role="AXButton", name="Save", parent_index=1, is_interactive=True),
        NodeDelta(A, 3, role="canvas", parent_index=0),
        NodeDelta(A, 4, role="polyline", name="外墙", parent_index=3),
    ), dib_references=dib, focused_node_index=focus, full=True)


def _delta(gen, *nodes, window=42, focus=-1, dib=(), base=None):
    return StateSnapshotPacket(window, 7, gen, tuple(nodes), dib_references=dib, focused_node_index=focus,
                               base_generation=gen - 1 if base is None else base)


def test_a_lost_delta_is_detected_not_skipped_over():
    s = _base()  # generation 1
    s2 = apply(s, _delta(3, NodeDelta(R, 1), base=2))  # generation 2 never arrived
    assert s2.needs_resync and "gap" in s2.problem and s2.nodes == s.nodes
    assert not apply(s, _delta(2, NodeDelta(R, 1), base=1)).needs_resync  # the right base applies


def _base():
    s = apply(EMPTY, _full())
    assert not s.needs_resync
    return s


def test_nothing_until_a_full_snapshot():
    assert EMPTY.needs_resync
    s = apply(EMPTY, _delta(1, NodeDelta(A, 0, role="x")))
    assert s is EMPTY  # a delta has no base to apply to
    assert header_line(s) == "(waiting for the first snapshot)"


def test_full_snapshot_renders_the_tree_in_order():
    s = apply(EMPTY, _full(focus=4, dib=(DibRef(3, 800, 600, "BGRA8", "9f3a" + "0" * 60),)))
    assert render_lines(s) == [
        "window#42  pid 7  gen 1  focus=4",
        '▸ AXWindow "Drawing"',
        '  ▸ AXGroup "Toolbar"',
        '    ▸ AXButton "Save"',
        "  ▸ canvas  [DIB 800x600 BGRA8 #9f3a0000]",
        '    ▸ polyline "外墙"  ★',
    ]


def test_add_update_remove():
    s = _base()
    s = apply(s, _delta(2, NodeDelta(A, 5, role="AXButton", name="Open", parent_index=1),
                        NodeDelta(U, 2, role="AXButton", name="Save As", parent_index=1, enabled=False)))
    labels = [r.label for r in rows(s)]
    assert labels[:4] == ['AXWindow "Drawing"', 'AXGroup "Toolbar"', 'AXButton "Save As"', 'AXButton "Open"']
    assert not s.nodes[2].enabled
    assert "(disabled)" in render_lines(s)[3]
    s = apply(s, _delta(3, NodeDelta(R, 1)))
    assert set(s.nodes) == {0, 3, 4}  # the toolbar and everything under it


def test_remove_drops_the_subtrees_dib_metadata():
    s = apply(EMPTY, _full(dib=(DibRef(4, 10, 10, "BGRA8", "aa"),)))
    s = apply(s, _delta(2, NodeDelta(R, 3)))
    assert 4 not in s.nodes and s.dib == {}


def test_stale_and_duplicate_generations_are_ignored():
    s = _base()
    s2 = apply(s, _delta(2, NodeDelta(A, 9, role="x", parent_index=0)))
    assert apply(s2, _delta(2, NodeDelta(A, 10, role="y", parent_index=0))) is s2
    assert apply(s2, _delta(1, NodeDelta(R, 9))) is s2
    assert apply(s2, _full(gen=2)) is s2  # a late full snapshot is stale too


def test_moving_a_node_under_a_new_parent():
    s = apply(_base(), _delta(2, NodeDelta(U, 4, role="polyline", name="外墙", parent_index=1)))
    assert not s.needs_resync
    assert [r.index for r in rows(s)] == [0, 1, 2, 4, 3]


def test_focus_moves_with_each_snapshot():
    s = apply(EMPTY, _full(focus=2))
    assert [r.index for r in rows(s) if r.focused] == [2]
    s = apply(s, _delta(2, focus=-1))
    assert not any(r.focused for r in rows(s)) and "focus=" not in header_line(s)


@pytest.mark.parametrize("pkt,why", [
    (_delta(2, NodeDelta(U, 99, role="x")), "update of unknown node 99"),
    (_delta(2, NodeDelta(R, 99)), "remove of unknown node 99"),
    (_delta(2, NodeDelta(A, 2, role="dup", parent_index=0)), "add of existing node 2"),
    (_delta(2, NodeDelta(A, 9, role="orphan", parent_index=77)), "unknown parent 77"),
    (_delta(2, NodeDelta(U, 0, role="AXWindow", parent_index=4)), "cycle"),
    (_delta(2, focus=99), "focus on unknown node 99"),
    (_delta(2, dib=(DibRef(99, 1, 1, "BGRA8", "aa"),)), "DIB reference to unknown node 99"),
    (_delta(2, NodeDelta(A, 9, role="x", parent_index=0), window=43), "a delta for a window"),
])
def test_an_inconsistent_delta_keeps_the_last_good_tree_and_asks_for_a_resync(pkt, why):
    base = _base()
    s = _within(2, apply, base, pkt)
    assert s.needs_resync and why in s.problem
    assert s.nodes == base.nodes and s.generation == base.generation  # nothing half-applied
    assert "resync needed" in header_line(s)
    # further deltas are not applied on top of a state we no longer trust...
    assert apply(s, _delta(3, NodeDelta(A, 9, role="x", parent_index=0))) is s
    # ...until a full snapshot replaces it
    s = apply(s, _full(gen=5, window=pkt.window_id))
    assert not s.needs_resync and s.generation == 5


def test_a_full_snapshot_listing_a_node_twice_is_inconsistent():
    dup = StateSnapshotPacket(42, 7, 1, (NodeDelta(A, 0, role="a"), NodeDelta(A, 0, role="b")), full=True)
    assert apply(EMPTY, dup).needs_resync


def test_a_chain_through_every_node_listed_leaf_first_is_a_valid_tree():
    # The walk from the first node listed must climb through all of them.
    chain = tuple(NodeDelta(A, i, role="g", parent_index=i - 1) for i in range(5, -1, -1))
    s = apply(EMPTY, StateSnapshotPacket(42, 7, 1, chain, full=True))
    assert not s.needs_resync
    assert [r.depth for r in rows(s)] == [0, 1, 2, 3, 4, 5]


def test_a_full_snapshot_with_no_root_is_inconsistent():
    loop = StateSnapshotPacket(42, 7, 1, (NodeDelta(A, 0, role="a", parent_index=1),
                                          NodeDelta(A, 1, role="b", parent_index=0)), full=True)
    s = _within(2, apply, EMPTY, loop)
    assert s.needs_resync and "cycle" in s.problem


def test_a_full_snapshot_for_a_new_window_replaces_everything():
    s = apply(apply(EMPTY, _full(dib=(DibRef(4, 1, 1, "BGRA8", "aa"),))),
              StateSnapshotPacket(50, 8, 1, (NodeDelta(A, 0, role="AXWindow", name="Other"),), full=True))
    assert s.window_id == 50 and set(s.nodes) == {0} and s.dib == {}


def test_labels_are_one_line_and_bounded():
    s = apply(EMPTY, StateSnapshotPacket(1, 1, 1, (
        NodeDelta(A, 0, role="AXStaticText", name="line one\nline two\x1b[31m" + "x" * 500),), full=True))
    line = render_lines(s)[1]
    assert "\n" not in line and "\x1b" not in line
    assert len(rows(s)[0].label) == 120 and rows(s)[0].label.endswith("…")
    assert clean("a\tb\rc") == "a b c"


def test_dib_rows_carry_metadata_only():
    s = apply(EMPTY, _full(dib=(DibRef(3, 800, 600, "BGRA8", "ff" * 32),)))
    ref = next(r.dib for r in rows(s) if r.dib is not None)
    assert set(vars(ref)) == {"node_index", "width", "height", "pixel_format", "content_hash"}
