"""Headless tests for the Structural Adapter (AxElement snapshot -> Observation).

Every sense is a fake: no AX, no DIB, no screen, no OS calls."""
from __future__ import annotations

import re

import pytest
from secdogie_agent.axtree import AxElement
from secdogie_agent.desktop_ax import DesktopAxProvider
from secdogie_agent.observation import (
    SOURCE_AX,
    Geometry,
    Observation,
    SemanticNode,
    VisualReference,
    fuse,
    observe_ax,
)
from secdogie_agent.perception.adapter import (
    DEFAULT_AX_CONFIDENCE,
    EMPTY_CONFIDENCE,
    NO_PRUNING,
    BaseObservationAdapter,
    DibProvider,
    FilterPolicy,
    NodeQuery,
    StructuralObservationAdapter,
    build_nodes,
    build_tree,
    geometry_from_bounds,
    normalize_role,
    union_geometry,
)
from secdogie_agent.perception.dib import (
    DIBFrameBuffer,
    HeapDibReader,
)


class FakeAxProvider:
    def __init__(self, elements):
        self.elements = elements
        self.calls = 0

    def snapshot(self):
        self.calls += 1
        return self.elements


class FakeDibProvider:
    """Returns a handle sized to the node; records which nodes were asked about."""

    def __init__(self, *, bit_count=32, fail_on=(), none_on=()):
        self.asked: list[SemanticNode] = []
        self.bit_count = bit_count
        self.fail_on = set(fail_on)
        self.none_on = set(none_on)

    def inspect(self, node):
        self.asked.append(node)
        if node.role in self.fail_on:
            raise OSError("atlas read denied")
        if node.role in self.none_on:
            return None
        return VisualReference(
            address=0x1000 + len(self.asked),
            width=node.bounds.w,
            height=node.bounds.h,
            bit_count=self.bit_count,
            content_hash=f"h{len(self.asked)}",
        )


def ax(role, name="", aid="", bounds=(0, 0, 10, 10), depth=-1):
    return AxElement(role, name, aid, bounds, depth)


# Walk order with real provider depths:
#   Window
#   ├── ToolBar
#   │   ├── Button Save
#   │   └── Button 打开
#   ├── Edit
#   └── Pane(canvas host)
#       └── Canvas
TREE = [
    ax("Window", "Untitled - Notepad", "", (100, 50, 900, 650), 0),
    ax("ToolBar", "Standard", "toolbar", (100, 80, 900, 110), 1),
    ax("Button", "Save", "btnSave", (110, 82, 150, 108), 2),
    ax("Button", "打开", "btnOpen", (160, 82, 200, 108), 2),
    ax("Edit", "Text Editor", "15", (100, 110, 900, 400), 1),
    ax("Pane", "Preview", "preview", (100, 400, 900, 630), 1),
    ax("Canvas", "", "", (110, 410, 890, 620), 2),
]


def _clock():
    return 1234.5


def _adapter(elements, **kw):
    kw.setdefault("clock", _clock)
    return StructuralObservationAdapter(FakeAxProvider(elements), **kw)


# --- contract -----------------------------------------------------------------


def test_base_adapter_is_abstract():
    with pytest.raises(TypeError):
        BaseObservationAdapter()  # type: ignore[abstract]


def test_fakes_satisfy_provider_protocols():
    assert isinstance(FakeAxProvider(TREE), DesktopAxProvider)
    assert isinstance(FakeDibProvider(), DibProvider)


def test_axelement_depth_is_walk_metadata_not_identity():
    assert AxElement("Button", "OK", "", (0, 0, 1, 1), depth=3) == AxElement("Button", "OK", "", (0, 0, 1, 1))
    assert AxElement("Button", "OK", "", (0, 0, 1, 1)).depth == -1


def test_accepts_bare_callable_providers():
    adapter = StructuralObservationAdapter(lambda: TREE, clock=_clock, dib_provider=lambda n: None)
    assert len(adapter.get_observation().semantic_nodes) == len(TREE)


@pytest.mark.parametrize("kw", [{}, {"dib_provider": object()}])
def test_rejects_non_providers(kw):
    provider = object() if not kw else FakeAxProvider(TREE)
    with pytest.raises(TypeError):
        StructuralObservationAdapter(provider, **kw)  # type: ignore[arg-type]


# --- field mapping (unchanged behaviour) -------------------------------------


def test_nodes_map_identity_and_window_fields():
    obs = _adapter(TREE, window_id=7, app_pid=4242, generation=3).get_observation()
    assert isinstance(obs, Observation)
    assert obs.source == SOURCE_AX
    assert obs.window == (7, 4242)
    assert obs.generation == 3
    assert obs.timestamp == 1234.5
    assert obs.confidence == DEFAULT_AX_CONFIDENCE
    for el, node in zip(TREE, obs.semantic_nodes, strict=True):
        assert (node.role, node.name, node.automation_id) == (el.role, el.name, el.automation_id)
        g = node.bounds
        assert (g.x, g.y, g.x + g.w, g.y + g.h) == el.bounds


def test_degenerate_bounds_keep_raw_numbers():
    g = geometry_from_bounds((50, 60, 40, 60))
    assert g == Geometry(50, 60, -10, 0)
    assert not g.valid


def test_window_geometry_is_union_of_valid_boxes():
    assert _adapter(TREE).get_observation().geometry == Geometry(100, 50, 800, 600)
    assert union_geometry(()) == Geometry()


def test_each_call_takes_a_fresh_snapshot():
    provider = FakeAxProvider(TREE)
    adapter = StructuralObservationAdapter(provider, clock=_clock)
    adapter.get_observation()
    provider.elements = TREE[:1]
    assert len(adapter.get_observation().semantic_nodes) == 1
    assert provider.calls == 2


def test_observation_feeds_fusion_with_structure_intact():
    obs = _adapter(TREE, window_id=1, app_pid=2).get_observation()
    result = fuse([obs])
    assert result.clean
    assert result.fused.semantic_nodes == obs.semantic_nodes


# --- 1. depth & hierarchy recovery -------------------------------------------


def test_depth_path_and_parent_from_walk_depth():
    obs = _adapter(TREE).get_observation()
    got = [(n.role, n.depth, n.path_index, n.parent_index) for n in obs.semantic_nodes]
    assert got == [
        ("Window", 0, (0,), -1),
        ("ToolBar", 1, (0, 0), 0),
        ("Button", 2, (0, 0, 0), 1),
        ("Button", 2, (0, 0, 1), 1),
        ("Edit", 1, (0, 1), 0),
        ("Pane", 1, (0, 2), 0),
        ("Canvas", 2, (0, 2, 0), 5),
    ]
    assert _adapter(TREE).last_report.depth_inferred is False


def test_provider_depth_gaps_attach_to_nearest_ancestor():
    # The provider dropped a zero-area container at depth 1, so its child jumps 0 -> 2.
    elements = [
        ax("Window", "w", "", (0, 0, 100, 100), 0),
        ax("Button", "deep", "", (10, 10, 20, 20), 2),
        ax("Button", "sibling", "", (30, 10, 40, 20), 1),
    ]
    nodes = _adapter(elements).get_observation().semantic_nodes
    assert [(n.name, n.depth, n.parent_index) for n in nodes] == [
        ("w", 0, -1),
        ("deep", 1, 0),
        ("sibling", 1, 0),
    ]


def test_multiple_roots_get_distinct_paths():
    elements = [
        ax("Window", "a", "", (0, 0, 50, 50), 0),
        ax("Button", "in a", "", (1, 1, 5, 5), 1),
        ax("Window", "b", "", (60, 0, 100, 50), 0),
    ]
    nodes = _adapter(elements).get_observation().semantic_nodes
    assert [n.path_index for n in nodes] == [(0,), (0, 0), (1,)]
    assert [n.parent_index for n in nodes] == [-1, 0, -1]


def test_depth_inferred_from_containment_when_walk_depth_missing():
    flat = [AxElement(e.role, e.name, e.automation_id, e.bounds) for e in TREE]
    adapter = _adapter(flat)
    nodes = adapter.get_observation().semantic_nodes
    assert adapter.last_report.depth_inferred is True
    assert [n.parent_index for n in nodes] == [-1, 0, 1, 1, 0, 0, 5]


def test_containment_attaches_boxless_nodes_without_popping_ancestors():
    elements = [
        ax("Window", "w", "", (0, 0, 100, 100)),
        ax("StaticText", "label", "", (0, 0, 0, 0)),  # named, no box: kept
        ax("Button", "ok", "", (10, 10, 20, 20)),
    ]
    nodes = _adapter(elements).get_observation().semantic_nodes
    assert [n.parent_index for n in nodes] == [-1, 0, 0]


def test_build_tree_recovers_nested_structure():
    obs = _adapter(TREE).get_observation()
    (root,) = build_tree(obs.semantic_nodes)
    assert root.node.role == "Window"
    assert [c.node.role for c in root.children] == ["ToolBar", "Edit", "Pane"]
    assert [c.node.name for c in root.children[0].children] == ["Save", "打开"]
    assert [t.index for t in root.walk()] == list(range(len(TREE)))


def test_structure_changes_content_hash_but_flat_hash_is_unchanged():
    a = _adapter(TREE).get_observation()
    reparented = [*TREE[:3], ax("Button", "打开", "btnOpen", (160, 82, 200, 108), 1), *TREE[4:]]
    assert _adapter(reparented).get_observation().content_hash != a.content_hash

    flat = (SemanticNode("Button", "OK"),)
    legacy = observe_ax(window_id=1, app_pid=1, semantic_nodes=flat, timestamp=0.0)
    assert legacy.content_hash == observe_ax(
        window_id=1, app_pid=1, semantic_nodes=(SemanticNode("Button", "OK"),), timestamp=0.0
    ).content_hash


# --- 2. pruning & interactive classification ---------------------------------


NOISY = [
    ax("Window", "w", "", (0, 0, 100, 100), 0),
    ax("Group", "", "", (0, 0, 0, 0), 1),  # pure placeholder
    ax("Button", "ok", "", (10, 10, 20, 20), 2),  # child of the placeholder
    ax("Text", "", "lbl", (5, 5, 5, 5), 1),  # no box but has an id: kept
]


def test_placeholders_pruned_and_children_reattached():
    adapter = _adapter(NOISY)
    nodes = adapter.get_observation().semantic_nodes
    assert [n.role for n in nodes] == ["Window", "Button", "Text"]
    assert [(n.depth, n.parent_index) for n in nodes] == [(0, -1), (1, 0), (1, 0)]
    report = adapter.last_report
    assert (report.raw_count, report.kept_count, report.pruned_count) == (4, 3, 1)


def test_pruning_can_be_disabled():
    nodes = _adapter(NOISY, filter_policy=NO_PRUNING).get_observation().semantic_nodes
    assert [n.role for n in nodes] == ["Window", "Group", "Button", "Text"]
    assert nodes[2].parent_index == 1


def test_all_placeholders_prune_to_empty_observation():
    obs = _adapter([ax("Group", bounds=(0, 0, 0, 0), depth=0)]).get_observation()
    assert obs.semantic_nodes == ()
    assert obs.confidence == EMPTY_CONFIDENCE


def test_interactive_classification():
    flags = {n.name or n.role: n.is_interactive for n in _adapter(TREE).get_observation().semantic_nodes}
    assert flags == {
        "Untitled - Notepad": False,
        "Standard": False,
        "Save": True,
        "打开": True,
        "Text Editor": True,
        "Preview": False,
        "Canvas": False,
    }


@pytest.mark.parametrize(
    "role",
    ["MenuItem", "CheckBox", "push button", "check box", "menu item", "TextField", "PopUpButton", "combo_box"],
)
def test_interactive_roles_across_platform_spellings(role):
    assert FilterPolicy().is_interactive(role)


def test_custom_interactive_roles():
    policy = FilterPolicy(interactive_roles=frozenset({normalize_role("Canvas")}))
    nodes = _adapter(TREE, filter_policy=policy).get_observation().semantic_nodes
    assert [n.role for n in nodes if n.is_interactive] == ["Canvas"]


def test_build_nodes_is_pure_and_reports_counts():
    nodes, pruned, inferred = build_nodes(NOISY)
    assert (len(nodes), pruned, inferred) == (3, 1, False)


# --- 3. DIB hybrid seam ------------------------------------------------------


def test_dib_attached_only_to_blind_nodes():
    dib = FakeDibProvider()
    adapter = _adapter(TREE, dib_provider=dib)
    nodes = adapter.get_observation().semantic_nodes
    assert [n.role for n in dib.asked] == ["Canvas"]
    canvas = nodes[6]
    assert canvas.visual_reference is not None
    assert (canvas.visual_reference.width, canvas.visual_reference.height) == (780, 210)
    assert all(n.visual_reference is None for n in nodes[:6])
    r = adapter.last_report
    assert (r.dib_requested, r.dib_attached, r.dib_bytes) == (1, 1, 780 * 210 * 4)


def test_labelled_canvas_is_still_blind():
    elements = [ax("Window", "w", "", (0, 0, 100, 100), 0), ax("Canvas", "Chart", "chart", (0, 0, 50, 50), 1)]
    dib = FakeDibProvider()
    _adapter(elements, dib_provider=dib).get_observation()
    assert [n.name for n in dib.asked] == ["Chart"]


def test_opaque_leaf_pane_is_blind_but_containers_and_labelled_panes_are_not():
    elements = [
        ax("Window", "w", "", (0, 0, 300, 300), 0),
        ax("Pane", "", "", (0, 0, 100, 100), 1),  # opaque leaf: render surface
        ax("Pane", "", "", (100, 0, 200, 100), 1),  # container
        ax("Button", "b", "", (110, 10, 120, 20), 2),
        ax("Pane", "Sidebar", "", (200, 0, 300, 100), 1),  # labelled leaf
    ]
    dib = FakeDibProvider()
    nodes = _adapter(elements, dib_provider=dib).get_observation().semantic_nodes
    assert [n.path_index for n in dib.asked] == [(0, 0)]
    assert nodes[1].visual_reference is not None

    dib_off = FakeDibProvider()
    policy = FilterPolicy(dib_for_opaque_leaves=False)
    _adapter(elements, dib_provider=dib_off, filter_policy=policy).get_observation()
    assert dib_off.asked == []


def test_boxless_blind_node_is_not_inspected():
    dib = FakeDibProvider()
    _adapter([ax("Canvas", "c", "", (0, 0, 0, 0), 0)], dib_provider=dib).get_observation()
    assert dib.asked == []


def test_dib_failure_keeps_ax_reading_and_is_reported():
    adapter = _adapter(TREE, dib_provider=FakeDibProvider(fail_on={"Canvas"}))
    obs = adapter.get_observation()
    assert len(obs.semantic_nodes) == len(TREE)
    assert obs.semantic_nodes[6].visual_reference is None
    assert obs.confidence == DEFAULT_AX_CONFIDENCE
    (err,) = adapter.last_report.dib_errors
    assert "0.2.0 Canvas" in err and "atlas read denied" in err


def test_dib_none_and_wrong_type_are_handled():
    adapter = _adapter(TREE, dib_provider=FakeDibProvider(none_on={"Canvas"}))
    assert adapter.get_observation().semantic_nodes[6].visual_reference is None
    assert adapter.last_report.dib_errors == ()

    bad = _adapter(TREE, dib_provider=lambda node: b"raw pixels")
    assert bad.get_observation().semantic_nodes[6].visual_reference is None
    assert "not a VisualReference" in bad.last_report.dib_errors[0]


def test_dib_budget_caps_total_bytes():
    elements = [
        ax("Window", "w", "", (0, 0, 300, 100), 0),
        ax("Canvas", "a", "", (0, 0, 100, 100), 1),
        ax("Canvas", "b", "", (100, 0, 200, 100), 1),
    ]
    adapter = _adapter(elements, dib_provider=FakeDibProvider(bit_count=8), dib_budget_bytes=15_000)
    nodes = adapter.get_observation().semantic_nodes
    assert nodes[1].visual_reference is not None
    assert nodes[2].visual_reference is None
    r = adapter.last_report
    assert (r.dib_attached, r.dib_skipped_budget, r.dib_bytes) == (1, 1, 10_000)


def test_dib_reference_enters_content_hash():
    plain = _adapter(TREE).get_observation()
    stitched = _adapter(TREE, dib_provider=FakeDibProvider()).get_observation()
    assert plain.content_hash != stitched.content_hash


# --- 3b. real heap framebuffer (DIBFrameBuffer) seam -------------------------


class FakeWriter:
    """A cooperating producer: bumps its seqlock odd while writing a frame into
    its heap and even when settled. ``tear`` makes it commit mid-read."""

    def __init__(self, *, seq=0, tear=False):
        self.seq = seq
        self.reads = 0
        self.tear = tear

    def read_seq(self):
        self.reads += 1
        current = self.seq
        if self.tear and self.reads == 1:
            self.seq += 2  # a write lands between the reader's two samples
        return current


def canvas_frame(*, width=8, height=4, fmt="BGRA8888", writer=None, seq=0, budget_ok=True):
    """A DIBFrameBuffer for the Canvas node, backed by a bytearray heap."""
    from secdogie_agent.perception.dib import bytes_per_pixel

    writer = writer or FakeWriter(seq=seq)
    stride = width * bytes_per_pixel(fmt)
    size = stride * height
    heap = bytearray(i % 251 for i in range(size))
    return DIBFrameBuffer(
        address=0xC0FFEE,
        width=width,
        height=height,
        stride=stride,
        pixel_format=fmt,
        size_bytes=size,
        _buffer=heap,
        _seq=writer.read_seq,
    ), writer


def _heap_reader(frame):
    return HeapDibReader(lambda node: frame if normalize_role(node.role) == "canvas" else None)


def test_heap_framebuffer_is_read_tearfree_and_attached():
    frame, writer = canvas_frame(seq=8)
    adapter = _adapter(TREE, dib_provider=_heap_reader(frame))
    nodes = adapter.get_observation().semantic_nodes
    ref = nodes[6].visual_reference
    assert ref is not None
    assert ref.pixels_available is True  # real bytes were read, not a bare handle
    assert ref.pixel_format == "BGRA8888"
    assert ref.stride == 32 and ref.width == 8 and ref.height == 4
    assert ref.seqlock == 8  # settled even sequence
    assert ref.size_bytes == 128
    r = adapter.last_report
    assert (r.dib_requested, r.dib_attached, r.dib_bytes) == (1, 1, 128)
    assert writer.reads == 2  # sampled before and after the copy


def test_heap_framebuffer_content_hash_matches_bytes():
    import hashlib

    frame, _ = canvas_frame(seq=2)
    data, seq = frame.read()
    adapter = _adapter(TREE, dib_provider=_heap_reader(frame))
    ref = adapter.get_observation().semantic_nodes[6].visual_reference
    assert ref.content_hash == hashlib.sha256(data).hexdigest()


def test_concurrent_write_tears_frame_and_backs_off():
    frame, writer = canvas_frame(writer=FakeWriter(seq=4, tear=True))
    adapter = _adapter(TREE, dib_provider=_heap_reader(frame), dib_retries=0)
    obs = adapter.get_observation()
    assert obs.semantic_nodes[6].visual_reference is None  # torn: not attached
    assert len(obs.semantic_nodes) == len(TREE)  # AX node kept
    assert obs.confidence == DEFAULT_AX_CONFIDENCE
    r = adapter.last_report
    assert r.dib_skipped_tearing == 1 and r.dib_attached == 0
    assert "torn frame" in r.dib_errors[0]


def test_odd_lock_retries_then_succeeds():
    class MidWrite:
        def __init__(self):
            self.reads = 0

        def read_seq(self):
            self.reads += 1
            return 3 if self.reads == 1 else 4  # odd (writing) then settled

    frame, _ = canvas_frame(writer=MidWrite())
    adapter = _adapter(TREE, dib_provider=_heap_reader(frame), dib_retries=2)
    ref = adapter.get_observation().semantic_nodes[6].visual_reference
    assert ref is not None and ref.seqlock == 4


def test_oversized_heap_frame_hits_budget():
    frame, _ = canvas_frame(width=64, height=64, seq=2)  # 16384 bytes
    adapter = _adapter(TREE, dib_provider=_heap_reader(frame), dib_budget_bytes=4096)
    obs = adapter.get_observation()
    assert obs.semantic_nodes[6].visual_reference is None
    r = adapter.last_report
    assert r.dib_skipped_budget == 1 and r.dib_attached == 0


def test_heap_out_of_bounds_is_recorded_but_node_kept():
    # size_bytes claims a frame larger than the backing heap.
    bad = DIBFrameBuffer(
        address=1,
        width=8,
        height=4,
        stride=32,
        pixel_format="BGRA8888",
        size_bytes=128,
        _buffer=bytearray(64),  # too small
        _seq=lambda: 0,
    )
    adapter = _adapter(TREE, dib_provider=_heap_reader(bad))
    obs = adapter.get_observation()
    assert obs.semantic_nodes[6].visual_reference is None
    r = adapter.last_report
    assert r.dib_attached == 0 and r.dib_skipped_tearing == 0
    assert "out of buffer" in r.dib_errors[0]


def test_bad_format_frame_is_recorded_but_node_kept():
    bad = DIBFrameBuffer(1, 8, 4, stride=32, pixel_format="YUV420", size_bytes=128, _buffer=bytearray(128), _seq=lambda: 0)
    adapter = _adapter(TREE, dib_provider=_heap_reader(bad))
    obs = adapter.get_observation()
    assert obs.semantic_nodes[6].visual_reference is None
    assert "unknown pixel format" in adapter.last_report.dib_errors[0]


def test_heap_reader_via_public_package_and_protocol():
    from secdogie_agent import perception

    frame, _ = canvas_frame(seq=6)
    reader = perception.HeapDibReader(lambda node: frame)
    assert isinstance(reader, DibProvider)
    assert isinstance(perception.DIBFrameBuffer, type)


# --- 4. chainable queries ----------------------------------------------------


def test_query_interactive():
    q = NodeQuery(_adapter(TREE).get_observation())
    assert [n.name for n in q.query_interactive()] == ["Save", "打开", "Text Editor"]


def test_find_by_automation_id_exact_casefold_first_match():
    q = _adapter(TREE).query()
    assert q.find_by_automation_id("btnSave").name == "Save"
    assert q.find_by_automation_id("BTNSAVE").name == "Save"
    assert q.find_by_automation_id("btnSav") is None
    assert q.find_by_automation_id("") is None


def test_find_by_role_pattern_glob_and_regex():
    q = _adapter(TREE).query()
    assert [n.name for n in q.find_by_role_pattern("Button", "s*")] == ["Save"]
    assert [n.role for n in q.find_by_role_pattern("*bar")] == ["ToolBar"]
    assert [n.name for n in q.find_by_role_pattern(re.compile("^(Button|Edit)$"), re.compile("[^\x00-\x7f]"))] == [
        "打开"
    ]
    assert len(q.find_by_role_pattern(None, None)) == len(TREE)


def test_queries_chain_and_navigate_the_tree():
    q = _adapter(TREE).query()
    save = q.query_interactive().find_by_role_pattern("Button", "Save").first()
    assert save is not None
    assert q.query_interactive().find_by_role_pattern("Button").find_by_automation_id("btnOpen").name == "打开"
    assert q.parent_of(save).role == "ToolBar"
    assert [n.role for n in q.ancestors_of(save)] == ["ToolBar", "Window"]
    toolbar = q.parent_of(save)
    assert [n.name for n in q.children_of(toolbar)] == ["Save", "打开"]
    # Narrowed queries still navigate the full tree.
    narrowed = q.query_interactive()
    assert narrowed.parent_of(save).role == "ToolBar"
    assert [n.role for n in q.at_depth(1)] == ["ToolBar", "Edit", "Pane"]


def test_query_helpers_on_empty_and_foreign_nodes():
    q = NodeQuery(_adapter(None).get_observation())
    assert not q and len(q) == 0 and q.first() is None
    assert q.query_interactive().nodes() == ()
    with pytest.raises(ValueError):
        q.children_of(SemanticNode("Button"))


def test_with_visual_reference_query():
    q = NodeQuery(_adapter(TREE, dib_provider=FakeDibProvider()).get_observation())
    assert [n.role for n in q.with_visual_reference()] == ["Canvas"]


# --- empty / null guards ------------------------------------------------------


@pytest.mark.parametrize("snapshot", [None, []])
def test_none_or_empty_snapshot_gives_empty_observation(snapshot):
    adapter = _adapter(snapshot, window_id=9, app_pid=10, generation=2, dib_provider=FakeDibProvider())
    obs = adapter.get_observation()
    assert obs.source == SOURCE_AX
    assert obs.window == (9, 10)
    assert obs.generation == 2
    assert obs.semantic_nodes == ()
    assert obs.geometry == Geometry()
    assert obs.confidence == EMPTY_CONFIDENCE
    assert obs.visual_reference is None
    assert obs.content_hash
    assert adapter.last_report.raw_count == 0


def test_none_and_empty_produce_identical_observations():
    assert _adapter(None).get_observation() == _adapter([]).get_observation()


def test_ax_provider_errors_are_not_swallowed():
    def boom():
        raise RuntimeError("tree read failed")

    with pytest.raises(RuntimeError):
        StructuralObservationAdapter(boom).get_observation()
