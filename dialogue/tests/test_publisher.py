"""The node's snapshot publisher: element lists become a full tree, then
deltas, that the operator's inspector folds without a gap; it reads only the
structural fields it names (a tripwire proves it), and never carries pixels."""
from __future__ import annotations

import json
import random
from dataclasses import dataclass

import pytest

pytest.importorskip("nacl")

from secdogie_dialogue.inspector import EMPTY, apply, render_lines, rows  # noqa: E402
from secdogie_dialogue.protocol import (  # noqa: E402
    PROTOCOL_VERSION,
    Envelope,
    Header,
    NodeOp,
    PacketKind,
    SessionEvent,
    SessionPacket,
    StateSnapshotPacket,
    to_wire,
)
from secdogie_dialogue.publisher import (  # noqa: E402
    DIB_FIELDS,
    FIELDS,
    MAX_NODES,
    MAX_TEXT,
    SnapshotPublisher,
)


@dataclass(frozen=True)
class El:
    """Shaped like the loop's accessibility element."""

    role: str
    name: str
    automation_id: str = ""
    bounds: tuple = (0, 0, 10, 10)


@dataclass(frozen=True)
class Ref:
    width: int = 64
    height: int = 32
    pixel_format: str = "BGRA8888"
    bit_count: int = 32
    content_hash: str = "ab" * 32


def pub(**kw):
    sent = []
    return SnapshotPublisher(sent.append, window_id=42, app_pid=7, **kw), sent


def fold(packets, state=EMPTY):
    for p in packets:
        state = apply(state, p)
    return state


def labels(state):
    return sorted(r.label for r in rows(state) if r.index != 0)


SAVE, OPEN, NAME = El("Button", "Save", "ID_SAVE"), El("Button", "Open"), El("Edit", "File name", "", (5, 5, 105, 25))


def test_first_publish_is_a_full_tree_under_one_window_node():
    p, sent = pub()
    pkt = p.publish([SAVE, NAME])
    assert sent == [pkt] and pkt.full and pkt.generation == 0 and pkt.base_generation == -1
    state = fold(sent)
    assert not state.needs_resync
    assert labels(state) == ['Button "Save"', 'Edit "File name"']
    (edit,) = [n for n in state.nodes.values() if n.role == "Edit"]
    assert edit.bounds == (5, 5, 100, 20) and edit.parent_index == 0  # (l, t, r, b) -> (x, y, w, h)


def test_nothing_changed_sends_nothing():
    p, sent = pub()
    p.publish([SAVE, NAME])
    assert p.publish([SAVE, NAME]) is None and len(sent) == 1


def test_changes_go_as_deltas_on_stable_handles():
    p, sent = pub()
    first = p.publish([SAVE, NAME])
    handles = {d.name: d.index for d in first.nodes}
    moved = El("Edit", "File name", "", (5, 5, 205, 25))
    delta = p.publish([SAVE, moved, OPEN])
    assert not delta.full and delta.base_generation == first.generation and delta.generation == 1
    ops = {(d.op, d.index) for d in delta.nodes}
    assert (NodeOp.UPDATE, handles["File name"]) in ops  # same identity: same handle
    assert any(op is NodeOp.ADD for op, _ in ops) and all(h != handles["Save"] for _, h in ops)
    gone = p.publish([moved, OPEN])
    assert [(d.op, d.index) for d in gone.nodes] == [(NodeOp.REMOVE, handles["Save"])]
    back = p.publish([SAVE, moved, OPEN])
    (add,) = back.nodes
    assert add.op is NodeOp.ADD and add.index == handles["Save"]  # same identity comes back on its handle
    state = fold(sent)
    assert not state.needs_resync and labels(state) == ['Button "Open"', 'Button "Save"', 'Edit "File name"']


def test_identical_elements_get_distinct_handles():
    p, sent = pub()
    pkt = p.publish([OPEN, OPEN, OPEN])
    assert len({d.index for d in pkt.nodes}) == 4  # the window + three
    assert len(rows(fold(sent))) == 4


def test_the_inspector_folds_any_sequence_without_a_gap():
    rng = random.Random(7)
    pool = [El(r, n, a, (x, 0, x + 10, 10)) for r, n, a, x in
            [("Button", "Save", "S", 0), ("Button", "Save", "S", 0), ("Button", "Open", "", 20),
             ("Edit", "Name", "N", 40), ("Edit", "Name", "N", 60), ("MenuItem", "File", "", 80),
             ("CheckBox", "Wrap", "W", 100), ("Link", "Help", "", 120)]]
    for _ in range(30):
        p, sent = pub(full_every=0)
        state = EMPTY
        for _ in range(25):
            els = [rng.choice(pool) for _ in range(rng.randint(0, 6))]
            if rng.random() < 0.2:
                els = [El(e.role, e.name, e.automation_id, (1, 1, rng.randint(2, 50), 9)) for e in els]
            before = len(sent)
            p.publish(els)
            state = fold(sent[before:], state)
            assert not state.needs_resync, state.problem
            want = sorted(f'{e.role} "{e.name}"' for e in els)
            assert labels(state) == want


def test_a_lost_delta_is_caught_and_a_resync_repairs_it():
    p, sent = pub(full_every=0)
    state = fold([p.publish([SAVE])])
    p.publish([SAVE, OPEN])  # lost on the way
    state = apply(state, p.publish([SAVE, OPEN, NAME]))
    assert state.needs_resync and "gap" in state.problem
    full = p.request_full()
    assert full.full and full.generation == 3
    state = apply(state, full)
    assert not state.needs_resync and len(labels(state)) == 3


def test_request_full_before_anything_was_published():
    p, sent = pub()
    assert p.request_full() is None and sent == []
    assert p.publish([SAVE]).full


def test_a_new_window_starts_a_new_stream():
    p, sent = pub()
    p.publish([SAVE])
    pkt = p.publish([SAVE], window_id=43, app_pid=8)
    assert pkt.full and pkt.window_id == 43 and pkt.app_pid == 8
    state = fold(sent)
    assert state.window_id == 43 and not state.needs_resync


def test_focus_names_a_position_in_the_list():
    p, sent = pub()
    pkt = p.publish([SAVE, NAME], focused=1)
    handle = next(d.index for d in pkt.nodes if d.name == "File name")
    assert pkt.focused_node_index == handle
    assert p.publish([SAVE, NAME], focused=None).focused_node_index == -1
    assert p.publish([SAVE, NAME], focused=9) is None  # out of range: no focus, and nothing else changed
    state = fold(sent)
    assert state.focused == -1


def test_a_full_tree_every_so_often():
    p, sent = pub(full_every=3)
    for i in range(7):
        p.publish([SAVE] if i % 2 else [OPEN])
    assert [s.full for s in sent] == [True, False, False, True, False, False, True]  # one in every three
    assert not fold(sent).needs_resync


def test_a_failed_send_makes_the_next_one_full():
    calls = []

    def flaky(pkt):
        calls.append(pkt)
        if len(calls) == 2:
            raise OSError("network down")

    p = SnapshotPublisher(flaky)
    assert p.publish([SAVE]).full
    assert p.publish([SAVE, OPEN]) is None  # the send failed
    assert p.publish([SAVE, OPEN, NAME]).full


def test_dib_metadata_only():
    @dataclass(frozen=True)
    class Canvas:
        role: str = "Canvas"
        name: str = "drawing"
        bounds: tuple = (0, 0, 64, 32)
        visual_reference: object = Ref()

    p, sent = pub()
    pkt = p.publish([Canvas(), Canvas(visual_reference=Ref(content_hash="")), SAVE])
    (ref,) = pkt.dib_references
    assert (ref.width, ref.height, ref.pixel_format, ref.content_hash) == (64, 32, "BGRA8888", "ab" * 32)
    other = p.publish([Canvas(visual_reference=Ref(pixel_format=""))], window_id=9)
    assert other.dib_references[0].pixel_format == "32bpp"
    state = fold(sent[:1])
    assert any("[DIB 64x32 BGRA8888" in ln for ln in render_lines(state))


class Tripwire:
    """Answers the structural fields it is given; any other attribute is a
    reach for something the publisher must not read."""

    def __init__(self, allowed, **fields):
        object.__setattr__(self, "_allowed", allowed)
        object.__setattr__(self, "_fields", fields)

    def __getattr__(self, name):
        if name not in self._allowed:
            raise AssertionError(f"the publisher read {name!r}")
        if name in self._fields:
            return self._fields[name]
        raise AttributeError(name)


def test_only_the_named_structural_fields_are_read_and_no_pixels_travel():
    ref = Tripwire(DIB_FIELDS, width=8, height=8, content_hash="cd" * 32, pixel_format="RGBA8888")
    el = Tripwire(FIELDS, role="Canvas", name="map", bounds=(0, 0, 8, 8), visual_reference=ref)
    p, sent = pub()
    pkt = p.publish([el])
    wire = json.dumps(to_wire(pkt))
    assert "pixels" not in wire and "address" not in wire
    assert pkt.dib_references[0].content_hash == "cd" * 32


def test_the_tripwire_would_catch_a_stray_read():
    with pytest.raises(AssertionError):
        _ = Tripwire(FIELDS).pixels


def test_sizes_are_bounded():
    p, _ = pub()
    many = [El("Button", f"b{i}") for i in range(MAX_NODES + 50)]
    assert len(p.publish(many).nodes) == MAX_NODES + 1  # + the window node
    p2, _ = pub()
    pkt = p2.publish([El("Text", "x" * 10_000, "y" * 10_000, ("a", None, 3, 4))])
    node = pkt.nodes[1]
    assert len(node.name) == MAX_TEXT and len(node.automation_id) == MAX_TEXT
    assert node.bounds == (0, 0, 3, 4)  # junk coordinates become 0, never a crash


def test_the_bridge_hands_targets_to_the_publisher_and_answers_resync():
    from secdogie_dialogue.agent_bridge import OperatorBridge
    from secdogie_identity import Allowlist, Identity

    class S:
        peer_did = "did:key:app"
        on_envelope = on_peer_down = None

        def __init__(self):
            self.sent = []

        def send(self, packet, *, reliable=None):
            self.sent.append(packet)

    node = Identity.generate()
    s = S()
    p = SnapshotPublisher(s.send)
    bridge = OperatorBridge(node, s, operators=Allowlist(set()), publisher=p)
    hooks = bridge.hooks()
    assert hooks.on_targets == p.publish
    hooks.on_targets([SAVE])
    bridge.on_envelope(Envelope(Header(PROTOCOL_VERSION, s.peer_did, node.did, "x", 1, 0), PacketKind.SESSION,
                                SessionPacket(SessionEvent.RESYNC), s.peer_did))
    snaps = [x for x in s.sent if isinstance(x, StateSnapshotPacket)]
    assert [x.full for x in snaps] == [True, True] and snaps[1].generation == 1
    assert OperatorBridge(node, s, operators=Allowlist(set())).hooks().on_targets is None
