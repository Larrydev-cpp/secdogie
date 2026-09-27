"""Headless tests for the macOS hidden-tree unlock and the richer AX fields.

A fake ApplicationServices module stands in for pyobjc: elements are dicts of
AX attributes, writes land back in those dicts (so a later read sees them), and
AXValueGetValue just unwraps a stand-in point/size/range. Everything the
provider decides -- which flags to set, what to restore, how values, selections
and table cells are read -- is proven without a Mac."""
from __future__ import annotations

import types

import pytest
from secdogie_agent import desktop_ax, elements
from secdogie_agent.axtree import AxElement
from secdogie_agent.desktop_ax import (
    AX_ENHANCED_USER_INTERFACE,
    AX_MANUAL_ACCESSIBILITY,
    MAX_TEXT_CHARS,
    _MacosAxProvider,
)
from secdogie_agent.perception.adapter import StructuralObservationAdapter


class V:
    """An AXValueRef wrapping a point / size / range."""

    def __init__(self, inner):
        self.inner = inner


def pos(x, y):
    return V(types.SimpleNamespace(x=x, y=y))


def size(w, h):
    return V(types.SimpleNamespace(width=w, height=h))


def rng(location, length=1):
    return V(types.SimpleNamespace(location=location, length=length))


class El:
    def __init__(self, **attrs):
        self.attrs = attrs


# AX error codes the fake returns.
OK = 0
UNSUPPORTED = -25205  # kAXErrorAttributeUnsupported


def fake_ax(system, *, pids=None, supports=None, fail_sets=()):
    """``pids`` maps app element -> pid. ``supports`` maps app element -> the
    set of app-level flags it accepts (others answer UNSUPPORTED, like a
    non-Electron app does for AXManualAccessibility)."""
    pids = pids or {}
    supports = supports or {}
    writes: list = []

    def copy_attr(element, attribute, _none):
        if attribute in element.attrs:
            return (OK, element.attrs[attribute])
        return (-1, None)

    def set_attr(element, attribute, value):
        writes.append((element, attribute, value))
        if attribute in fail_sets:
            return -1
        if attribute in (AX_MANUAL_ACCESSIBILITY, AX_ENHANCED_USER_INTERFACE):
            if attribute not in supports.get(element, set()):
                return UNSUPPORTED
        element.attrs[attribute] = value
        return OK

    def get_pid(element, _none=None):
        return (OK, pids[element]) if element in pids else (-1, 0)

    fake = types.SimpleNamespace(
        kAXFocusedApplicationAttribute="AXFocusedApplication",
        kAXFocusedWindowAttribute="AXFocusedWindow",
        kAXChildrenAttribute="AXChildren",
        kAXRoleAttribute="AXRole",
        kAXTitleAttribute="AXTitle",
        kAXDescriptionAttribute="AXDescription",
        kAXIdentifierAttribute="AXIdentifier",
        kAXPositionAttribute="AXPosition",
        kAXSizeAttribute="AXSize",
        kAXValueAttribute="AXValue",
        kAXSelectedTextAttribute="AXSelectedText",
        kAXValueCGPointType="CGPoint",
        kAXValueCGSizeType="CGSize",
        kAXValueCFRangeType="CFRange",
        AXUIElementCreateSystemWide=lambda: system,
        AXUIElementCopyAttributeValue=copy_attr,
        AXUIElementSetAttributeValue=set_attr,
        AXUIElementGetPid=get_pid,
        AXValueGetValue=lambda v, _t, _n: (True, v.inner),
    )
    fake.writes = writes
    return fake


def window(*children, title="Win"):
    return El(AXRole="AXWindow", AXTitle=title, AXPosition=pos(0, 0), AXSize=size(800, 600), AXChildren=list(children))


def app_with(win, **attrs):
    return El(AXFocusedWindow=win, **attrs)


def provider_for(app, *, pid=100, bundle=None, supports=(), **fake_kw):
    system = El(AXFocusedApplication=app)
    fake = fake_ax(system, pids={app: pid}, supports={app: set(supports)}, **fake_kw)
    prov = _MacosAxProvider(fake, bundle_id_of=lambda p: bundle)
    return prov, fake


def app_writes(fake, app):
    return [(attr, val) for el, attr, val in fake.writes if el is app]


# --- unlock: which flags are set --------------------------------------------


def test_electron_app_gets_manual_accessibility_only():
    app = app_with(window())
    prov, fake = provider_for(app, bundle="com.microsoft.VSCode", supports={AX_MANUAL_ACCESSIBILITY})
    prov.snapshot()
    assert app_writes(fake, app) == [(AX_MANUAL_ACCESSIBILITY, True)]
    assert app.attrs[AX_MANUAL_ACCESSIBILITY] is True
    assert prov.unlocked_flags() == [(100, AX_MANUAL_ACCESSIBILITY)]


def test_chrome_gets_both_flags():
    app = app_with(window())
    prov, fake = provider_for(
        app, bundle="com.google.Chrome", supports={AX_MANUAL_ACCESSIBILITY, AX_ENHANCED_USER_INTERFACE}
    )
    prov.snapshot()
    assert app_writes(fake, app) == [(AX_MANUAL_ACCESSIBILITY, True), (AX_ENHANCED_USER_INTERFACE, True)]
    assert {attr for _pid, attr in prov.unlocked_flags()} == {AX_MANUAL_ACCESSIBILITY, AX_ENHANCED_USER_INTERFACE}


def test_chrome_recognised_by_title_when_bundle_id_unavailable():
    app = app_with(window(), AXTitle="Google Chrome")
    prov, fake = provider_for(app, bundle=None, supports={AX_ENHANCED_USER_INTERFACE})
    prov.snapshot()
    assert (AX_ENHANCED_USER_INTERFACE, True) in app_writes(fake, app)


def test_native_app_is_asked_once_and_left_unchanged():
    # A plain Cocoa app rejects AXManualAccessibility and is never sent
    # AXEnhancedUserInterface (it has side effects outside browsers).
    app = app_with(window())
    prov, fake = provider_for(app, bundle="com.apple.TextEdit")
    prov.snapshot()
    prov.snapshot()
    assert app_writes(fake, app) == [(AX_MANUAL_ACCESSIBILITY, True)]  # one attempt, rejected
    assert AX_MANUAL_ACCESSIBILITY not in app.attrs
    assert prov.unlocked_flags() == []


def test_flag_already_on_is_not_touched_or_restored():
    # VoiceOver (or another client) already enabled the tree.
    app = app_with(window(), **{AX_ENHANCED_USER_INTERFACE: True})
    prov, fake = provider_for(
        app, bundle="com.google.Chrome", supports={AX_MANUAL_ACCESSIBILITY, AX_ENHANCED_USER_INTERFACE}
    )
    prov.snapshot()
    assert (AX_ENHANCED_USER_INTERFACE, True) not in app_writes(fake, app)
    prov.restore_accessibility()
    assert app.attrs[AX_ENHANCED_USER_INTERFACE] is True  # still on: it was never ours


def test_unlock_happens_once_per_app_across_calls():
    app = app_with(window(El(AXRole="AXButton", AXTitle="Go", AXPosition=pos(1, 1), AXSize=size(10, 10))))
    prov, fake = provider_for(app, bundle="com.microsoft.VSCode", supports={AX_MANUAL_ACCESSIBILITY})
    prov.snapshot()
    prov.snapshot()
    prov.press(name="Go")
    prov.hit_test(5, 5)
    assert app_writes(fake, app).count((AX_MANUAL_ACCESSIBILITY, True)) == 1


def test_unlock_can_be_disabled():
    app = app_with(window())
    system = El(AXFocusedApplication=app)
    fake = fake_ax(system, pids={app: 1}, supports={app: {AX_MANUAL_ACCESSIBILITY}})
    _MacosAxProvider(fake, bundle_id_of=lambda p: None, unlock_hidden_trees=False).snapshot()
    assert fake.writes == []


def test_bundle_lookup_errors_fall_back_to_title():
    app = app_with(window(), AXTitle="Arc")
    system = El(AXFocusedApplication=app)
    fake = fake_ax(system, pids={app: 7}, supports={app: {AX_ENHANCED_USER_INTERFACE}})

    def boom(pid):
        raise RuntimeError("AppKit unavailable")

    _MacosAxProvider(fake, bundle_id_of=boom).snapshot()
    assert (AX_ENHANCED_USER_INTERFACE, True) in app_writes(fake, app)


def test_missing_setter_or_pid_api_is_harmless():
    app = app_with(window())
    system = El(AXFocusedApplication=app)
    fake = fake_ax(system)
    del fake.AXUIElementSetAttributeValue
    del fake.AXUIElementGetPid
    prov = _MacosAxProvider(fake, bundle_id_of=lambda p: None)
    assert prov.snapshot() is not None
    assert prov.unlocked_flags() == []


# --- restore -----------------------------------------------------------------


def test_restore_turns_off_only_what_we_turned_on():
    app = app_with(window())
    prov, fake = provider_for(
        app, bundle="com.google.Chrome", supports={AX_MANUAL_ACCESSIBILITY, AX_ENHANCED_USER_INTERFACE}
    )
    prov.snapshot()
    assert prov.restore_accessibility() == 2
    assert app.attrs[AX_MANUAL_ACCESSIBILITY] is False
    assert app.attrs[AX_ENHANCED_USER_INTERFACE] is False
    assert prov.unlocked_flags() == []
    assert prov.restore_accessibility() == 0  # idempotent


def test_restore_survives_an_app_that_quit():
    app = app_with(window())
    prov, fake = provider_for(app, bundle="com.microsoft.VSCode", supports={AX_MANUAL_ACCESSIBILITY})
    prov.snapshot()
    fake.AXUIElementSetAttributeValue = lambda *_a: (_ for _ in ()).throw(RuntimeError("invalid element"))
    assert prov.restore_accessibility() == 0  # no raise
    assert prov.unlocked_flags() == []


def test_restore_is_registered_with_atexit_only_when_something_changed(monkeypatch):
    registered = []
    monkeypatch.setattr("atexit.register", lambda fn: registered.append(fn))
    native = app_with(window())
    prov, _ = provider_for(native, bundle="com.apple.TextEdit")
    prov.snapshot()
    assert registered == []

    electron = app_with(window())
    prov2, _ = provider_for(electron, bundle="com.tinyspeck.slackmacgap", supports={AX_MANUAL_ACCESSIBILITY})
    prov2.snapshot()
    prov2.snapshot()
    assert registered == [prov2.restore_accessibility]


class _RestoringProvider:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def restore_accessibility(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")
        return 1


class _IdleBackend:
    def __init__(self, provider):
        self.ax_provider = provider

    def setup(self, logger):
        return None


@pytest.mark.parametrize("fail", [False, True])
def test_loop_restores_flags_when_the_run_ends(tmp_path, fail):
    from secdogie_agent import loop

    prov = _RestoringProvider(fail=fail)
    config = loop.AgentConfig(
        task="t", backend=_IdleBackend(prov), auto=True, max_steps=0, log_path=str(tmp_path / "log.txt")
    )
    rc = loop.run(object(), config)  # no steps: straight into try/finally
    assert rc == 3
    assert prov.calls == 1  # a failing restore is logged, never raised


# --- AXValue / AXSelectedText ------------------------------------------------


def field(**attrs):
    base = dict(AXRole="AXTextField", AXTitle="Search", AXPosition=pos(10, 10), AXSize=size(200, 20))
    base.update(attrs)
    return El(**base)


def snapshot_of(*children):
    app = app_with(window(*children))
    prov, _ = provider_for(app)
    return {e.name: e for e in prov.snapshot()}


def test_value_and_selected_text_are_read():
    els = snapshot_of(field(AXValue="hello world", AXSelectedText="world"))
    f = els["Search"]
    assert f.value == "hello world"
    assert f.selected_text == "world"


def test_numeric_and_boolean_values_are_kept():
    slider = El(AXRole="AXSlider", AXTitle="Volume", AXValue=0.5, AXPosition=pos(0, 50), AXSize=size(100, 10))
    box = El(AXRole="AXCheckBox", AXTitle="Wrap", AXValue=True, AXPosition=pos(0, 70), AXSize=size(20, 20))
    els = snapshot_of(slider, box)
    assert els["Volume"].value == "0.5"
    assert els["Wrap"].value == "1"


def test_opaque_values_are_dropped_and_long_text_capped():
    opaque = field(AXTitle="Opaque", AXValue=V(object()))
    long_text = field(AXTitle="Doc", AXValue="x" * (MAX_TEXT_CHARS + 500))
    els = snapshot_of(opaque, long_text)
    assert els["Opaque"].value == ""
    assert len(els["Doc"].value) == MAX_TEXT_CHARS


@pytest.mark.parametrize(
    "attrs",
    [
        {"AXRole": "AXSecureTextField"},
        {"AXRole": "AXTextField", "AXSubrole": "AXSecureTextField"},
    ],
)
def test_secure_fields_never_expose_their_value(attrs):
    pw = field(AXTitle="Password", AXValue="hunter2", AXSelectedText="hunter2", **attrs)
    els = snapshot_of(pw)
    assert els["Password"].value == "" and els["Password"].selected_text == ""


def test_unlabelled_secure_field_value_is_not_used_as_its_name():
    pw = El(AXRole="AXSecureTextField", AXValue="hunter2", AXIdentifier="pw", AXPosition=pos(0, 0), AXSize=size(50, 10))
    app = app_with(window(pw))
    prov, _ = provider_for(app)
    got = [e for e in prov.snapshot() if e.automation_id == "pw"][0]
    assert got.name == "" and got.value == ""


def test_value_does_not_change_element_identity():
    a = AxElement("TextField", "Search", "", (0, 0, 1, 1), value="a")
    b = AxElement("TextField", "Search", "", (0, 0, 1, 1), value="b", selected_text="b")
    assert a == b


# --- table cells ---------------------------------------------------------------


def cell(title, **attrs):
    return El(AXRole="AXCell", AXTitle=title, AXPosition=pos(0, 0), AXSize=size(10, 10), **attrs)


def row(index, *cells):
    return El(AXRole="AXRow", AXIndex=index, AXPosition=pos(0, 0), AXSize=size(100, 10), AXChildren=list(cells))


def table(*rows):
    return El(AXRole="AXTable", AXTitle="Grid", AXPosition=pos(0, 0), AXSize=size(300, 300), AXChildren=list(rows))


def test_cells_take_row_index_and_position_in_row():
    els = snapshot_of(table(row(0, cell("a0"), cell("a1")), row(1, cell("b0"), cell("b1"))))
    assert {n: els[n].table_cell for n in ("a0", "a1", "b0", "b1")} == {
        "a0": (0, 0),
        "a1": (0, 1),
        "b0": (1, 0),
        "b1": (1, 1),
    }
    assert els["Grid"].table_cell is None


def test_explicit_index_ranges_win():
    c = cell("x", AXRowIndexRange=rng(7), AXColumnIndexRange=rng(3))
    els = snapshot_of(table(row(0, c)))
    assert els["x"].table_cell == (7, 3)


def test_view_based_row_children_are_cells_too():
    text = El(AXRole="AXStaticText", AXTitle="name", AXPosition=pos(0, 0), AXSize=size(10, 10))
    els = snapshot_of(table(row(4, text)))
    assert els["name"].table_cell == (4, 0)


def test_cell_outside_a_row_without_ranges_has_no_position():
    els = snapshot_of(cell("lonely"))
    assert els["lonely"].table_cell is None


def test_cell_with_ranges_outside_a_row():
    els = snapshot_of(cell("ranged", AXRowIndexRange=rng(2), AXColumnIndexRange=rng(5)))
    assert els["ranged"].table_cell == (2, 5)


# --- downstream: listing and adapter ------------------------------------------


def test_listing_shows_value_selection_and_cell():
    targets = [
        AxElement("TextField", "Search", "", (0, 0, 10, 10), value="hello", selected_text="ell"),
        AxElement("Cell", "Alice", "", (0, 0, 10, 10), table_cell=(2, 1)),
        AxElement("Button", "Save", "", (0, 0, 10, 10), value="Save"),  # value == name: not repeated
    ]
    text = elements.render_for_model(targets)
    assert "[e1] TextField \"Search\" value='hello' selected='ell'" in text
    assert '[e2] Cell "Alice" [row 2, col 1]' in text
    assert '[e3] Button "Save"\n' in text + "\n"


def test_listing_clips_long_values():
    el = AxElement("TextArea", "Body", "", (0, 0, 10, 10), value="y" * 500)
    line = elements.render_for_model([el]).splitlines()[-1]
    assert len(line) < 150 and line.endswith("…'")


def test_adapter_carries_value_and_cell_into_semantic_nodes():
    els = [
        AxElement("Window", "w", "", (0, 0, 100, 100), depth=0),
        AxElement("TextField", "Search", "", (1, 1, 50, 10), depth=1, value="q", selected_text="q"),
        AxElement("Cell", "c", "", (1, 20, 20, 30), depth=1, table_cell=(0, 2)),
    ]
    nodes = StructuralObservationAdapter(lambda: els, clock=lambda: 0.0).get_observation().semantic_nodes
    assert (nodes[1].value, nodes[1].selected_text) == ("q", "q")
    assert nodes[2].table_cell == (0, 2)


def test_module_exports():
    assert desktop_ax.CHROMIUM_BROWSER_BUNDLE_IDS
    assert "com.google.Chrome" in desktop_ax.CHROMIUM_BROWSER_BUNDLE_IDS
