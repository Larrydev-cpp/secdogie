"""Headless tests for touch exploration (hit-test probing) on macOS.

The fake ApplicationServices module hit-tests by geometry over a z-ordered
list of elements, the way AXUIElementCopyElementAtPosition answers on a real
Mac, and elements can be hit-testable without being listed in their parent's
AXChildren -- exactly the case a tree walk misses. No screen, no pixels."""
from __future__ import annotations

import types

import pytest
from secdogie_agent import touch_probe
from secdogie_agent.axtree import AxElement
from secdogie_agent.backend import DesktopBackend
from secdogie_agent.desktop_ax import ProbeResult, _MacosAxProvider
from secdogie_agent.perception.adapter import StructuralObservationAdapter
from secdogie_agent.perception.touch import TouchProbingProvider, probe_regions
from secdogie_agent.touch_probe import adaptive_probe, merge_chains

# --- pure: adaptive sweep ------------------------------------------------------


def test_uniform_region_costs_one_cell():
    sweep = adaptive_probe((0, 0, 400, 300), lambda x, y: "pane")
    assert sweep.probes == 5
    assert sweep.distinct() == {"pane"}
    assert not sweep.truncated


def test_edges_are_refined_and_both_sides_found():
    sweep = adaptive_probe((0, 0, 400, 400), lambda x, y: "left" if x < 150 else "right", min_cell=8)
    assert sweep.distinct() == {"left", "right"}
    # Refinement follows the edge: far fewer touches than a full 8px grid.
    assert sweep.probes < (400 // 8) ** 2 // 4


def test_small_target_inside_a_large_pane_is_found():
    def hit(x, y):
        return "button" if 180 <= x < 220 and 140 <= y < 160 else "pane"

    sweep = adaptive_probe((0, 0, 400, 300), hit, max_probes=400, min_cell=8)
    # The first cell's centre lands on the button; refinement keeps it.
    assert "button" in sweep.distinct()


def test_probe_budget_truncates_breadth_first():
    sweep = adaptive_probe((0, 0, 400, 400), lambda x, y: (x // 10, y // 10), max_probes=30)
    assert sweep.truncated and sweep.probes == 30


def test_time_budget_truncates():
    ticks = iter(range(1000))
    sweep = adaptive_probe(
        (0, 0, 400, 400), lambda x, y: (x, y), max_seconds=3, clock=lambda: next(ticks)
    )
    assert sweep.truncated and sweep.probes <= 3


def test_failed_touch_is_nothing_not_a_crash():
    def hit(x, y):
        raise RuntimeError("IPC timeout")

    sweep = adaptive_probe((0, 0, 100, 100), hit)
    assert sweep.probes == 5 and sweep.distinct() == set()


@pytest.mark.parametrize("region", [(0, 0, 0, 10), (10, 10, 5, 20)])
def test_empty_region(region):
    assert adaptive_probe(region, lambda x, y: 1).probes == 0


def test_points_are_inside_the_exclusive_rect():
    seen = []
    adaptive_probe((10, 20, 30, 40), lambda x, y: seen.append((x, y)) or (x > 20))
    assert all(10 <= x < 30 and 20 <= y < 40 for x, y in seen)


# --- pure: merging chains ------------------------------------------------------


def ax(role, name, bounds=(0, 0, 10, 10), depth=-1, **kw):
    return AxElement(role, name, "", bounds, depth, **kw)


W = ax("Window", "w", (0, 0, 100, 100), 0)
P = ax("Group", "p", (0, 0, 50, 50), 1)
Q = ax("Group", "q", (50, 0, 100, 50), 1)
B = ax("Button", "b", (1, 1, 9, 9))
L = ax("StaticText", "label", (2, 2, 8, 8))


def test_merge_inserts_under_deepest_known_ancestor_at_end_of_subtree():
    snap = [W, P, ax("Button", "old", depth=2), Q]
    merged, added = merge_chains(snap, [[W, P, B, L]])
    assert added == 2
    assert [(e.name, e.depth) for e in merged] == [
        ("w", 0),
        ("p", 1),
        ("old", 2),
        ("b", 2),
        ("label", 3),
        ("q", 1),
    ]


def test_merge_skips_known_elements_and_is_idempotent():
    snap = [W, P]
    merged, added = merge_chains(snap, [[W, P, B], [W, P, B]])
    assert added == 1
    again, added2 = merge_chains(merged, [[W, P, B]])
    assert added2 == 0 and again == merged


def test_merge_unknown_chain_becomes_a_new_root():
    other = ax("Window", "other", (200, 0, 300, 100))
    merged, added = merge_chains([W], [[other, B]])
    assert [(e.name, e.depth) for e in merged] == [("w", 0), ("other", 0), ("b", 1)]
    assert added == 2


def test_merge_without_walk_depths_appends_with_unknown_depth():
    flat = [ax("Window", "w", (0, 0, 100, 100))]
    merged, added = merge_chains(flat, [[flat[0], B]])
    assert added == 1 and merged[-1].depth == -1 and merged[-1].name == "b"


def test_merged_tree_feeds_the_adapter():
    merged, _ = merge_chains([W, P], [[W, P, B]])
    nodes = StructuralObservationAdapter(lambda: merged, clock=lambda: 0.0).get_observation().semantic_nodes
    assert [(n.name, n.parent_index) for n in nodes] == [("w", -1), ("p", 0), ("b", 1)]


# --- fake macOS AX with geometric hit testing ---------------------------------


class V:
    def __init__(self, inner):
        self.inner = inner


class El:
    def __init__(self, role, title="", box=None, children=(), parent=None, **attrs):
        self.attrs = {"AXRole": role, **attrs}
        if title:
            self.attrs["AXTitle"] = title
        if box is not None:
            left, top, right, bottom = box
            self.attrs["AXPosition"] = V(types.SimpleNamespace(x=left, y=top))
            self.attrs["AXSize"] = V(types.SimpleNamespace(width=right - left, height=bottom - top))
            self.box = box
        else:
            self.box = None
        self.attrs["AXChildren"] = list(children)
        for c in children:
            c.attrs["AXParent"] = self
        if parent is not None:
            self.attrs["AXParent"] = parent


def contains(box, x, y):
    return box is not None and box[0] <= x < box[2] and box[1] <= y < box[3]


def fake_mac(app, zorder, *, text=None):
    """``zorder``: elements top-most first; the hit test returns the first whose
    box contains the point. ``text``: element -> list of line strings, each line
    10px tall starting at the element's top."""
    system = El("AXSystemWide")
    system.attrs["AXFocusedApplication"] = app
    text = text or {}

    def copy_attr(element, attribute, _none):
        if attribute in element.attrs:
            v = element.attrs[attribute]
            return (0, v) if v != [] or attribute != "AXChildren" else (0, v)
        return (-1, None)

    def copy_at(element, x, y, _none=None):
        for el in zorder:
            if contains(el.box, x, y):
                return (0, el)
        return (-1, None)

    def param(element, attribute, parameter, _none):
        lines = text.get(element)
        if lines is None:
            return (-25205, None)  # unsupported
        top = element.box[1]
        if attribute == "AXRangeForPosition":
            _x, y = parameter.inner
            n = min(int((y - top) // 10), len(lines) - 1)
            return (0, V(types.SimpleNamespace(location=n * 100, length=0)))
        if attribute == "AXLineForIndex":
            return (0, parameter // 100)
        if attribute == "AXRangeForLine":
            return (0, V(types.SimpleNamespace(location=parameter * 100, length=len(lines[parameter]))))
        line = parameter.inner.location // 100
        if attribute == "AXStringForRange":
            return (0, lines[line])
        if attribute == "AXBoundsForRange":
            y0 = top + 10 * line
            rect = types.SimpleNamespace(
                origin=types.SimpleNamespace(x=element.box[0], y=y0),
                size=types.SimpleNamespace(width=element.box[2] - element.box[0], height=10),
            )
            return (0, V(rect))
        return (-1, None)

    return types.SimpleNamespace(
        kAXFocusedApplicationAttribute="AXFocusedApplication",
        kAXFocusedWindowAttribute="AXFocusedWindow",
        kAXChildrenAttribute="AXChildren",
        kAXParentAttribute="AXParent",
        kAXRoleAttribute="AXRole",
        kAXTitleAttribute="AXTitle",
        kAXDescriptionAttribute="AXDescription",
        kAXIdentifierAttribute="AXIdentifier",
        kAXPositionAttribute="AXPosition",
        kAXSizeAttribute="AXSize",
        kAXValueAttribute="AXValue",
        kAXValueCGPointType="CGPoint",
        kAXValueCGSizeType="CGSize",
        kAXValueCFRangeType="CFRange",
        kAXValueCGRectType="CGRect",
        AXUIElementCreateSystemWide=lambda: system,
        AXUIElementCopyAttributeValue=copy_attr,
        AXUIElementCopyElementAtPosition=copy_at,
        AXUIElementCopyParameterizedAttributeValue=param,
        AXUIElementSetAttributeValue=lambda *a: -25205,
        AXUIElementPerformAction=lambda *a: 0,
        AXValueGetValue=lambda v, _t, _n: (True, v.inner),
        AXValueCreate=lambda _t, value: V(value),
    )


def hidden_button_app():
    """A SwiftUI-style window: the hosting group lists no children, but the
    button inside it is hit-testable (its AXParent is the group)."""
    host = El("AXGroup", box=(0, 0, 400, 300))
    button = El("AXButton", "Continue", (150, 120, 250, 160), parent=host)
    win = El("AXWindow", "App", (0, 0, 400, 300), children=[host])
    app = El("AXApplication", "App")
    app.attrs["AXWindows"] = [win]
    win.attrs["AXParent"] = app
    return app, win, host, button


def provider(app, zorder, **kw):
    return _MacosAxProvider(fake_mac(app, zorder, **kw), bundle_id_of=lambda p: None)


def test_walk_misses_the_hidden_button_but_touch_finds_it():
    app, win, host, button = hidden_button_app()
    prov = provider(app, [button, host, win])
    walked = prov.snapshot()
    assert "Continue" not in [e.name for e in walked]

    result = prov.probe((0, 0, 400, 300), max_probes=200, min_cell=8)
    names = {tuple(e.name or e.role for e in chain) for chain in result.chains}
    assert ("App", "Group", "Continue") in names
    button_el = [c for c in result.chains if c[-1].name == "Continue"][0][-1]
    assert button_el.origin == "hit-test"
    assert button_el.bounds == (150, 120, 250, 160)


def test_touch_reads_text_lines_by_position():
    area = El("AXTextArea", box=(0, 0, 300, 30))
    win = El("AXWindow", "Doc", (0, 0, 300, 30), children=[area])
    app = El("AXApplication")
    app.attrs["AXWindows"] = [win]
    prov = provider(app, [area, win], text={area: ["first line", "second line", "third"]})
    result = prov.probe((0, 0, 300, 30), max_probes=100, min_cell=4, read_text=True)
    lines = sorted((c[-1].name, c[-1].bounds) for c in result.chains if c[-1].role == "TextLine")
    assert lines == [
        ("first line", (0, 0, 300, 10)),
        ("second line", (0, 10, 300, 20)),
        ("third", (0, 20, 300, 30)),
    ]
    assert all(c[-1].origin == "touch-text" for c in result.chains if c[-1].role == "TextLine")
    assert result.text_lines == 3


def test_text_reading_degrades_when_unsupported():
    area = El("AXTextArea", box=(0, 0, 300, 30))
    win = El("AXWindow", "Doc", (0, 0, 300, 30), children=[area])
    app = El("AXApplication")
    app.attrs["AXWindows"] = [win]
    result = provider(app, [area, win]).probe((0, 0, 300, 30), read_text=True)
    assert result.text_lines == 0
    assert not any(c[-1].role == "TextLine" for c in result.chains)


def test_probe_uses_the_app_element_not_system_wide():
    app, win, host, button = hidden_button_app()
    fake = fake_mac(app, [button, host, win])
    asked = []
    inner = fake.AXUIElementCopyElementAtPosition
    fake.AXUIElementCopyElementAtPosition = lambda el, x, y, n=None: asked.append(el) or inner(el, x, y)
    _MacosAxProvider(fake, bundle_id_of=lambda p: None).probe((0, 0, 400, 300), max_probes=10)
    assert asked and all(el is app for el in asked)


# --- occlusion ---------------------------------------------------------------


def test_button_behind_a_sheet_is_occluded():
    app, win, host, button = hidden_button_app()
    sheet = El("AXSheet", "Save changes?", (100, 100, 300, 200), parent=win)
    prov = provider(app, [sheet, button, host, win])
    target = AxElement("Button", "Continue", "", (150, 120, 250, 160))
    cover = prov.occluder_of(target)
    assert cover is not None and cover.role == "Sheet" and cover.name == "Save changes?"


def test_touching_the_target_or_its_label_is_not_occlusion():
    app, win, host, button = hidden_button_app()
    label = El("AXStaticText", "Continue", (160, 130, 240, 150), parent=button)
    target = AxElement("Button", "Continue", "", (150, 120, 250, 160))
    assert provider(app, [button, host, win]).occluder_of(target) is None
    assert provider(app, [label, button, host, win]).occluder_of(target) is None


def test_container_hit_that_holds_the_target_is_not_occlusion():
    # The button isn't hit-testable; the touch lands on its containing group.
    btn = El("AXButton", "OK", (10, 10, 50, 30))
    group = El("AXGroup", box=(0, 0, 100, 100), children=[btn])
    win = El("AXWindow", "W", (0, 0, 100, 100), children=[group])
    app = El("AXApplication")
    app.attrs["AXWindows"] = [win]
    target = AxElement("Button", "OK", "", (10, 10, 50, 30))
    assert provider(app, [group, win]).occluder_of(target) is None


def test_unknown_touch_is_not_reported_as_occlusion():
    app, *_ = hidden_button_app()
    assert provider(app, []).occluder_of(AxElement("Button", "x", "", (0, 0, 10, 10))) is None
    assert provider(app, []).occluder_of(AxElement("Button", "x", "", (0, 0, 0, 0))) is None


class _OccludingProvider:
    def __init__(self, cover):
        self.cover = cover
        self.pressed = []

    def occluder_of(self, el):
        return self.cover

    def press(self, **attrs):
        self.pressed.append(attrs)
        return True


def test_invoke_element_refuses_to_press_a_covered_control():
    sheet = AxElement("Sheet", "Save changes?", "", (0, 0, 10, 10))
    prov = _OccludingProvider(sheet)
    backend = DesktopBackend(ax_provider=prov)
    result = backend.invoke_element(AxElement("Button", "Continue", "", (0, 0, 10, 10)))
    assert result.startswith("did not press Button 'Continue'") and "Sheet 'Save changes?'" in result
    assert prov.pressed == []


def test_invoke_element_presses_when_nothing_covers_it():
    prov = _OccludingProvider(None)
    result = DesktopBackend(ax_provider=prov).invoke_element(AxElement("Button", "Go", "", (0, 0, 10, 10)))
    assert result.startswith("invoked Button 'Go'") and prov.pressed == [{"name": "Go", "role": "Button"}]


# --- wrapper: which regions, merging, caching ---------------------------------


def test_probe_regions_picks_blind_nodes_and_shallow_windows():
    els = [
        ax("Window", "rich", (0, 0, 100, 100), 0),
        ax("Button", "a", (1, 1, 5, 5), 1),
        ax("Button", "b", (6, 1, 9, 5), 1),
        ax("Button", "c", (1, 6, 5, 9), 1),
        ax("Button", "d", (6, 6, 9, 9), 1),
        ax("Canvas", "", (10, 10, 90, 90), 1),
        ax("Window", "empty", (200, 0, 400, 200), 0),
        ax("Group", "", (210, 10, 390, 190), 1),  # opaque leaf: blind too
    ]
    got = [(o.role, o.name) for o, _box in probe_regions(els)]
    assert got == [("Canvas", ""), ("Group", ""), ("Window", "empty")]


def test_probe_regions_touch_each_box_once():
    els = [ax("Window", "empty", (0, 0, 100, 100), 0), ax("Group", "", (0, 0, 100, 100), 1)]
    assert [box for _o, box in probe_regions(els)] == [(0, 0, 100, 100)]


def test_probe_regions_without_depth_only_blind_nodes():
    els = [ax("Window", "w", (0, 0, 100, 100)), ax("Canvas", "", (0, 0, 50, 50))]
    assert [o.role for o, _ in probe_regions(els)] == ["Canvas"]


class FakeBase:
    def __init__(self, walked, results):
        self.walked = walked
        self.results = results  # bounds -> ProbeResult
        self.probed = []
        self.pressed = False

    def snapshot(self):
        return list(self.walked)

    def probe(self, box, **kw):
        self.probed.append((box, kw))
        r = self.results.get(box)
        if isinstance(r, Exception):
            raise r
        return r or ProbeResult()

    def press(self, **attrs):
        self.pressed = True
        return True


WIN = ax("Window", "w", (0, 0, 400, 300), 0)
CANVAS = ax("Canvas", "", (0, 0, 400, 300), 1)


def test_wrapper_grafts_touched_elements_and_reports():
    btn = ax("Button", "Play", (10, 10, 50, 30), origin="hit-test")
    base = FakeBase([WIN, CANVAS], {CANVAS.bounds: ProbeResult(chains=((WIN, CANVAS, btn),), probes=40, distinct=frozenset({btn, CANVAS}))})
    prov = TouchProbingProvider(base)
    snap = prov.snapshot()
    assert [(e.name or e.role, e.depth) for e in snap] == [("w", 0), ("Canvas", 1), ("Play", 2)]
    assert snap[-1].origin == "hit-test"
    r = prov.last_report
    assert (r.regions, r.probes, r.added, r.opaque_regions) == (1, 40, 1, 0)


def test_opaque_canvas_is_reported_and_left_as_a_blind_leaf():
    base = FakeBase([WIN, CANVAS], {CANVAS.bounds: ProbeResult(chains=((WIN, CANVAS),), probes=5, distinct=frozenset({CANVAS}))})
    prov = TouchProbingProvider(base)
    snap = prov.snapshot()
    assert snap == [WIN, CANVAS]
    assert prov.last_report.opaque_regions == 1 and prov.last_report.added == 0


def test_same_walk_reuses_the_sweep_until_invalidated():
    base = FakeBase([WIN, CANVAS], {})
    prov = TouchProbingProvider(base)
    prov.snapshot()
    prov.snapshot()
    assert len(base.probed) == 1 and prov.last_report.reused
    prov.invalidate_probe()
    prov.snapshot()
    assert len(base.probed) == 2


def test_changed_walk_triggers_a_new_sweep():
    base = FakeBase([WIN, CANVAS], {})
    prov = TouchProbingProvider(base)
    prov.snapshot()
    base.walked = [WIN, CANVAS, ax("Button", "new", (1, 1, 5, 5), 2)]
    prov.snapshot()
    assert len(base.probed) == 2


def test_budget_is_split_across_regions_and_capped():
    canvases = [ax("Canvas", str(i), (i * 50, 0, i * 50 + 40, 40), 1) for i in range(4)]
    base = FakeBase([ax("Window", "w", (0, 0, 400, 300), 0), *canvases], {})
    TouchProbingProvider(base, max_probes=100).snapshot()
    shares = [kw["max_probes"] for _box, kw in base.probed]
    assert shares == [25, 25, 25, 25]


def test_failed_probe_keeps_the_walk():
    base = FakeBase([WIN, CANVAS], {CANVAS.bounds: RuntimeError("AX timeout")})
    assert TouchProbingProvider(base).snapshot() == [WIN, CANVAS]


def test_base_without_probe_passes_through_and_delegates():
    class Plain:
        def snapshot(self):
            return [WIN]

        def press(self, **attrs):
            return "pressed"

    prov = TouchProbingProvider(Plain())
    assert prov.snapshot() == [WIN]
    assert prov.press(name="x") == "pressed"  # delegated


def test_read_text_flag_is_forwarded():
    base = FakeBase([WIN, CANVAS], {})
    TouchProbingProvider(base, read_text=False).snapshot()
    assert base.probed[0][1]["read_text"] is False


# --- loop wiring ----------------------------------------------------------------


@pytest.mark.parametrize("enabled", [True, False])
def test_loop_wraps_a_probing_provider(monkeypatch, tmp_path, enabled):
    from secdogie_agent import desktop_ax, loop

    base = FakeBase([WIN], {})
    monkeypatch.setattr(desktop_ax, "make_desktop_ax_provider", lambda logger=None: base)
    captured = {}

    class CapturingBackend:
        def __init__(self, **kw):
            captured.update(kw)
            self.ax_provider = kw["ax_provider"]

        def setup(self, logger):
            return None

    monkeypatch.setattr(loop, "DesktopBackend", CapturingBackend)
    config = loop.AgentConfig(
        task="t", auto=True, max_steps=0, desktop_ax=True, touch_probe=enabled, log_path=str(tmp_path / "l")
    )
    loop.run(object(), config)
    wrapped = captured["ax_provider"]
    assert isinstance(wrapped, TouchProbingProvider) is enabled
    assert (wrapped is base) is (not enabled)


def test_touch_probe_module_exports():
    assert touch_probe.DEFAULT_MAX_PROBES > 0


# --- acting on touched elements -----------------------------------------------


class _TouchActingProvider:
    def __init__(self, press_ok=True, set_ok=True):
        self.calls = []
        self.press_ok, self.set_ok = press_ok, set_ok

    def occluder_of(self, el):
        return None

    def press_at(self, x, y):
        self.calls.append(("press_at", x, y))
        return self.press_ok

    def press(self, **attrs):
        self.calls.append(("press", attrs))
        return True

    def set_value_at(self, x, y, text):
        self.calls.append(("set_value_at", x, y, text))
        return self.set_ok

    def set_value(self, text, **attrs):
        self.calls.append(("set_value", text, attrs))
        return True


def test_touched_element_is_pressed_by_position():
    prov = _TouchActingProvider()
    el = AxElement("Button", "Continue", "", (150, 120, 250, 160), origin="hit-test")
    result = DesktopBackend(ax_provider=prov).invoke_element(el)
    assert "at its position" in result
    assert prov.calls == [("press_at", 200, 140)]


def test_touched_element_falls_back_to_identity_press():
    prov = _TouchActingProvider(press_ok=False)
    el = AxElement("Button", "Continue", "", (150, 120, 250, 160), origin="hit-test")
    DesktopBackend(ax_provider=prov).invoke_element(el)
    assert [c[0] for c in prov.calls] == ["press_at", "press"]


def test_walked_element_is_pressed_by_identity():
    prov = _TouchActingProvider()
    DesktopBackend(ax_provider=prov).invoke_element(AxElement("Button", "Go", "", (0, 0, 10, 10)))
    assert [c[0] for c in prov.calls] == ["press"]


def test_touched_field_is_set_by_position():
    prov = _TouchActingProvider()
    el = AxElement("TextField", "Name", "", (0, 0, 100, 20), origin="hit-test")
    result = DesktopBackend(ax_provider=prov).set_element_value(el, "Ada")
    assert "at its position" in result and prov.calls == [("set_value_at", 50, 10, "Ada")]


def _field_app():
    host = El("AXGroup", box=(0, 0, 400, 300))
    field = El("AXTextField", "Name", (10, 10, 210, 30), parent=host)
    button = El("AXButton", "OK", (10, 50, 60, 70), parent=host)
    win = El("AXWindow", "App", (0, 0, 400, 300), children=[host])
    app = El("AXApplication")
    app.attrs["AXWindows"] = [win]
    return app, [field, button, host, win], field


def test_set_value_at_writes_only_text_fields():
    app, zorder, field = _field_app()
    fake = fake_mac(app, zorder)
    writes = []
    fake.AXUIElementSetAttributeValue = lambda el, attr, v: writes.append((el, attr, v)) or 0
    prov = _MacosAxProvider(fake, bundle_id_of=lambda p: None, unlock_hidden_trees=False)
    assert prov.set_value_at(100, 20, "Ada") is True
    assert writes == [(field, "AXValue", "Ada")]
    assert prov.set_value_at(30, 60, "x") is False  # a button is on top there
    assert prov.set_value_at(390, 290, "x") is False  # only the group
    assert len(writes) == 1
