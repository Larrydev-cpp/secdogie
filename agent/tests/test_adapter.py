"""Headless tests for the Structural Adapter (AxElement snapshot -> Observation).

The provider is a fake: no AX, no screen, no OS calls."""
from __future__ import annotations

import pytest
from secdogie_agent.axtree import AxElement
from secdogie_agent.desktop_ax import DesktopAxProvider
from secdogie_agent.observation import SOURCE_AX, Geometry, Observation, fuse
from secdogie_agent.perception.adapter import (
    DEFAULT_AX_CONFIDENCE,
    EMPTY_CONFIDENCE,
    BaseObservationAdapter,
    StructuralObservationAdapter,
    geometry_from_bounds,
    union_geometry,
)


class FakeAxProvider:
    def __init__(self, elements):
        self.elements = elements
        self.calls = 0

    def snapshot(self):
        self.calls += 1
        return self.elements


# A small window tree in provider walk order: window -> toolbar -> buttons, edit.
TREE = [
    AxElement("Window", "Untitled - Notepad", "", (100, 50, 900, 650)),
    AxElement("ToolBar", "Standard", "toolbar", (100, 80, 900, 110)),
    AxElement("Button", "Save", "btnSave", (110, 82, 150, 108)),
    AxElement("Button", "打开", "btnOpen", (160, 82, 200, 108)),
    AxElement("Edit", "Text Editor", "15", (100, 110, 900, 630)),
]


def _clock():
    return 1234.5


def _adapter(elements, **kw):
    return StructuralObservationAdapter(FakeAxProvider(elements), clock=_clock, **kw)


# --- contract -----------------------------------------------------------------


def test_base_adapter_is_abstract():
    with pytest.raises(TypeError):
        BaseObservationAdapter()  # type: ignore[abstract]


def test_fake_provider_satisfies_desktop_ax_protocol():
    assert isinstance(FakeAxProvider(TREE), DesktopAxProvider)


# --- field mapping ------------------------------------------------------------


def test_nodes_map_role_name_id_and_bounds_in_order():
    obs = _adapter(TREE, window_id=7, app_pid=4242, generation=3).get_observation()

    assert isinstance(obs, Observation)
    assert obs.source == SOURCE_AX
    assert obs.window == (7, 4242)
    assert obs.generation == 3
    assert obs.timestamp == 1234.5
    assert obs.confidence == DEFAULT_AX_CONFIDENCE
    assert len(obs.semantic_nodes) == len(TREE)
    for el, node in zip(TREE, obs.semantic_nodes, strict=True):
        assert (node.role, node.name, node.automation_id) == (el.role, el.name, el.automation_id)
        assert node.enabled is True
    # Provider walk order (the only hierarchy AxElement carries) is preserved.
    assert [n.role for n in obs.semantic_nodes] == ["Window", "ToolBar", "Button", "Button", "Edit"]


def test_bounds_convert_ltrb_to_xywh_losslessly():
    obs = _adapter(TREE).get_observation()
    save = obs.semantic_nodes[2]
    assert save.bounds == Geometry(x=110, y=82, w=40, h=26)
    for el, node in zip(TREE, obs.semantic_nodes, strict=True):
        g = node.bounds
        assert (g.x, g.y, g.x + g.w, g.y + g.h) == el.bounds


def test_degenerate_bounds_keep_raw_numbers():
    g = geometry_from_bounds((50, 60, 40, 60))
    assert g == Geometry(50, 60, -10, 0)
    assert not g.valid


def test_window_geometry_is_union_of_valid_boxes():
    obs = _adapter(TREE).get_observation()
    assert obs.geometry == Geometry(100, 50, 800, 600)


def test_union_ignores_invalid_boxes():
    elements = [
        AxElement("Pane", "", "", (0, 0, 0, 0)),
        AxElement("Button", "OK", "", (10, 20, 30, 40)),
    ]
    obs = _adapter(elements).get_observation()
    assert len(obs.semantic_nodes) == 2
    assert obs.geometry == Geometry(10, 20, 20, 20)


def test_all_invalid_boxes_give_empty_geometry_but_keep_nodes():
    obs = _adapter([AxElement("Pane", "p", "", (5, 5, 5, 5))]).get_observation()
    assert obs.semantic_nodes[0].name == "p"
    assert obs.geometry == Geometry()
    assert union_geometry(()) == Geometry()


def test_content_hash_is_deterministic_and_tracks_changes():
    a = _adapter(TREE).get_observation()
    b = _adapter(list(TREE)).get_observation()
    assert a.content_hash and a.content_hash == b.content_hash
    renamed = [*TREE[:2], AxElement("Button", "Save As", "btnSave", TREE[2].bounds), *TREE[3:]]
    assert _adapter(renamed).get_observation().content_hash != a.content_hash


def test_each_call_takes_a_fresh_snapshot():
    provider = FakeAxProvider(TREE)
    adapter = StructuralObservationAdapter(provider, clock=_clock)
    adapter.get_observation()
    provider.elements = TREE[:1]
    obs = adapter.get_observation()
    assert provider.calls == 2
    assert len(obs.semantic_nodes) == 1


def test_accepts_bare_callable_provider():
    adapter = StructuralObservationAdapter(lambda: TREE, clock=_clock)
    assert len(adapter.get_observation().semantic_nodes) == len(TREE)


def test_rejects_non_provider():
    with pytest.raises(TypeError):
        StructuralObservationAdapter(object())  # type: ignore[arg-type]


def test_observation_feeds_fusion():
    obs = _adapter(TREE, window_id=1, app_pid=2).get_observation()
    result = fuse([obs])
    assert result.clean
    assert result.fused.semantic_nodes == obs.semantic_nodes


# --- empty / null guards ------------------------------------------------------


@pytest.mark.parametrize("snapshot", [None, []])
def test_none_or_empty_snapshot_gives_empty_observation(snapshot):
    obs = _adapter(snapshot, window_id=9, app_pid=10, generation=2).get_observation()
    assert obs.source == SOURCE_AX
    assert obs.window == (9, 10)
    assert obs.generation == 2
    assert obs.semantic_nodes == ()
    assert obs.geometry == Geometry()
    assert not obs.geometry.valid
    assert obs.confidence == EMPTY_CONFIDENCE
    assert obs.visual_reference is None
    assert obs.content_hash


def test_none_and_empty_produce_identical_observations():
    assert _adapter(None).get_observation() == _adapter([]).get_observation()


def test_provider_errors_are_not_swallowed():
    def boom():
        raise RuntimeError("tree read failed")

    with pytest.raises(RuntimeError):
        StructuralObservationAdapter(boom).get_observation()
