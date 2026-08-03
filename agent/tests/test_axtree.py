"""Tests for the desktop accessibility layer. All headless: the pure axtree
queries, DesktopBackend's Locatable wiring against a fake provider, and the
platform factory's graceful no-op. The live UI-Automation walk (desktop_ax's
_WindowsUiaProvider) is on-machine and not tested here -- the point of the seam
is that the matching brain (axtree) is provable without a desktop."""
import sys

from secdogie_agent import axtree, desktop_ax
from secdogie_agent.backend import DesktopBackend, ElementSelector, Locatable


def _tree():
    return [
        axtree.AxElement(role="Window", name="App", automation_id="", bounds=(0, 0, 800, 600)),
        axtree.AxElement(role="Pane", name="", automation_id="", bounds=(0, 40, 800, 600)),
        axtree.AxElement(role="Button", name="Save", automation_id="saveBtn", bounds=(100, 100, 200, 140)),
        axtree.AxElement(role="Button", name="Cancel", automation_id="", bounds=(220, 100, 320, 140)),
    ]


# -- pure axtree --------------------------------------------------------------

def test_element_at_picks_the_smallest_box_containing_the_point():
    el = axtree.element_at(_tree(), 150, 120)
    assert el is not None and el.name == "Save"  # the button, not the window/pane it sits in


def test_element_at_returns_none_outside_everything():
    assert axtree.element_at(_tree(), 5000, 5000) is None


def test_find_elements_matches_exactly_not_by_substring():
    tree = _tree()
    assert [e.name for e in axtree.find_elements(tree, automation_id="saveBtn")] == ["Save"]
    assert [e.name for e in axtree.find_elements(tree, name="Cancel", role="Button")] == ["Cancel"]
    assert axtree.find_elements(tree, name="Sav") == []       # exact, not prefix/substring
    assert axtree.find_elements(tree, automation_id="save") == []


def test_selector_for_prefers_automation_id_then_name():
    tree = _tree()
    save, cancel, window, pane = tree[2], tree[3], tree[0], tree[1]
    assert axtree.selector_for(save) == {"automation_id": "saveBtn", "role": "Button"}   # stable id wins
    assert axtree.selector_for(cancel) == {"name": "Cancel", "role": "Button"}           # no id -> name
    assert axtree.selector_for(window) == {"name": "App", "role": "Window"}              # a named window is identifiable
    assert axtree.selector_for(pane) is None                                             # role only -> not identifiable


def test_center_and_area():
    el = axtree.AxElement(role="Button", name="X", automation_id="", bounds=(100, 100, 200, 140))
    assert el.center == (150, 120)
    assert el.area == 100 * 40


# -- DesktopBackend Locatable (via a fake provider) ---------------------------

class FakeProvider:
    """A DesktopAxProvider whose snapshot the test controls (and can change
    between calls to model the UI moving)."""

    def __init__(self, elements):
        self.elements = elements

    def snapshot(self):
        return self.elements


def test_desktop_backend_is_locatable_only_with_a_provider():
    assert isinstance(DesktopBackend(ax_provider=FakeProvider(_tree())), Locatable)
    # Without a provider the methods exist but no-op, so it behaves as not element-aware.
    plain = DesktopBackend()
    assert plain.describe_target(150, 120) is None
    assert plain.locate(ElementSelector(kind=axtree.SELECTOR_KIND, attrs={"name": "Save"})) is None


def test_describe_target_builds_a_selector_from_the_element_under_the_point():
    b = DesktopBackend(ax_provider=FakeProvider(_tree()))
    sel = b.describe_target(150, 120)
    assert sel == ElementSelector(kind=axtree.SELECTOR_KIND, attrs={"automation_id": "saveBtn", "role": "Button"})


def test_describe_target_none_when_nothing_identifiable_is_under_the_point():
    # (400, 300) lands only in the anonymous Pane (no id, no name) -> not identifiable.
    b = DesktopBackend(ax_provider=FakeProvider(_tree()))
    assert b.describe_target(400, 300) is None


def test_locate_resolves_a_selector_to_the_current_center_even_after_it_moved():
    b = DesktopBackend(ax_provider=FakeProvider(_tree()))
    sel = b.describe_target(150, 120)  # Save button, recorded at center (150,120)

    # The window re-lays-out and Save is now at a different place; locate must
    # re-find it by identity, not return the stale coordinate.
    moved = [axtree.AxElement(role="Button", name="Save", automation_id="saveBtn", bounds=(500, 300, 600, 340))]
    b.ax_provider = FakeProvider(moved)
    assert b.locate(sel) == (550, 320)


def test_locate_none_when_the_element_is_gone_or_kind_mismatches():
    b = DesktopBackend(ax_provider=FakeProvider([]))
    assert b.locate(ElementSelector(kind=axtree.SELECTOR_KIND, attrs={"automation_id": "saveBtn"})) is None
    b.ax_provider = FakeProvider(_tree())
    assert b.locate(ElementSelector(kind="android-uiautomator", attrs={"text": "Save"})) is None  # not ours


# -- platform factory ---------------------------------------------------------

def test_make_provider_is_a_graceful_none_off_windows(caplog):
    # On this (non-Windows, no-a11y) box the factory must return None with a
    # hint rather than raising -- so --desktop-ax degrades instead of breaking.
    import logging

    provider = desktop_ax.make_desktop_ax_provider(logging.getLogger("test.ax"))
    if sys.platform.startswith("win") or sys.platform == "darwin":
        # On Windows / macOS it depends on whether the platform a11y library is
        # installed; either a provider or a clean None, never an exception.
        assert provider is None or isinstance(provider, desktop_ax.DesktopAxProvider)
    else:
        assert provider is None


# -- Linux AT-SPI provider (walk logic verified against a fake pyatspi) --------
# The real AT-SPI bus needs a live desktop; these fakes stand in for pyatspi's
# Accessible/Component objects so the provider's active-frame + walk + mapping
# logic is provable headless. Only the real bus binding is on-machine.

class _FakeExtents:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.width, self.height = x, y, w, h


class _FakeComponent:
    def __init__(self, extents):
        self._extents = extents

    def getExtents(self, coord_type):
        return self._extents


class _FakeState:
    def __init__(self, active):
        self._active = active

    def contains(self, state):
        return self._active and state == "STATE_ACTIVE"


class _FakeAccessible:
    def __init__(self, role="", name="", extents=None, active=False, children=()):
        self._role, self.name, self._extents = role, name, extents
        self._active, self._children = active, list(children)

    def getRoleName(self):
        return self._role

    def getState(self):
        return _FakeState(self._active)

    def getChildCount(self):
        return len(self._children)

    def getChildAtIndex(self, i):
        return self._children[i]

    def queryComponent(self):
        if self._extents is None:
            raise LookupError("this accessible has no Component interface")
        return _FakeComponent(self._extents)


def _fake_pyatspi(monkeypatch, desktop):
    import types as _types

    fake = _types.SimpleNamespace(
        STATE_ACTIVE="STATE_ACTIVE",
        DESKTOP_COORDS=0,
        Registry=_types.SimpleNamespace(getDesktop=lambda screen: desktop),
    )
    monkeypatch.setitem(sys.modules, "pyatspi", fake)
    return fake


def _desktop_with_active_button():
    button = _FakeAccessible(role="push button", name="Save", extents=_FakeExtents(100, 100, 100, 40))
    frame = _FakeAccessible(role="frame", name="App", extents=_FakeExtents(0, 0, 800, 600),
                            active=True, children=[button])
    app = _FakeAccessible(role="application", name="MyApp", children=[frame])  # no box -> skipped
    return _FakeAccessible(role="desktop frame", children=[app])


def test_atspi_snapshot_walks_the_active_frame(monkeypatch):
    from secdogie_agent.desktop_ax import _AtspiProvider

    _fake_pyatspi(monkeypatch, _desktop_with_active_button())
    els = _AtspiProvider().snapshot()
    assert els is not None
    got = {(e.role, e.name, e.bounds) for e in els}
    # The active frame and its button map through; the app container (no box) is skipped.
    assert ("push button", "Save", (100, 100, 200, 140)) in got
    assert ("frame", "App", (0, 0, 800, 600)) in got
    assert all(e.role != "application" for e in els)
    # And the pure query re-finds the button at its center.
    assert axtree.element_at(els, 150, 120).name == "Save"


def test_atspi_snapshot_is_none_when_no_window_is_active(monkeypatch):
    inactive_frame = _FakeAccessible(role="frame", name="Bg", extents=_FakeExtents(0, 0, 10, 10), active=False)
    app = _FakeAccessible(role="application", children=[inactive_frame])
    desktop = _FakeAccessible(role="desktop frame", children=[app])
    _fake_pyatspi(monkeypatch, desktop)

    from secdogie_agent.desktop_ax import _AtspiProvider

    assert _AtspiProvider().snapshot() is None


def test_make_provider_builds_the_atspi_provider_on_linux_when_bindings_exist(monkeypatch):
    if not sys.platform.startswith("linux"):
        return  # the linux dispatch branch only runs on linux
    _fake_pyatspi(monkeypatch, _desktop_with_active_button())
    provider = desktop_ax.make_desktop_ax_provider()
    assert isinstance(provider, desktop_ax._AtspiProvider)


# -- macOS AX provider (walk logic verified against a fake ApplicationServices) --
# The real AX API needs a live macOS session with Accessibility permission; these
# fakes stand in for pyobjc's AXUIElement objects and the AXValue unwrap so the
# provider's focused-window scoping + walk + AX-role/geometry mapping is provable
# headless. Only the real framework binding is on-machine.

class _FakeAXValue:
    """Stands in for an AXValueRef; AXValueGetValue just returns its inner
    CGPoint/CGSize (already carrying x/y or width/height)."""

    def __init__(self, inner):
        self.inner = inner


class _FakePoint:
    def __init__(self, x, y):
        self.x, self.y = x, y


class _FakeSize:
    def __init__(self, width, height):
        self.width, self.height = width, height


class _FakeAXElement:
    """An AXUIElement whose attributes the test controls, keyed by the same AX
    constant strings the fake module exposes."""

    def __init__(self, attrs):
        self.attrs = attrs


def _fake_appservices(monkeypatch, system_element):
    import types as _types

    def copy_attr(element, attribute, _none):
        if attribute in element.attrs:
            return (0, element.attrs[attribute])   # kAXErrorSuccess == 0
        return (-1, None)                          # attribute absent

    def get_value(axvalue, value_type, _none):
        return (True, axvalue.inner)               # unwrap the CGPoint/CGSize

    fake = _types.SimpleNamespace(
        kAXFocusedApplicationAttribute="AXFocusedApplication",
        kAXFocusedWindowAttribute="AXFocusedWindow",
        kAXChildrenAttribute="AXChildren",
        kAXRoleAttribute="AXRole",
        kAXTitleAttribute="AXTitle",
        kAXDescriptionAttribute="AXDescription",
        kAXIdentifierAttribute="AXIdentifier",
        kAXPositionAttribute="AXPosition",
        kAXSizeAttribute="AXSize",
        kAXValueCGPointType="CGPoint",
        kAXValueCGSizeType="CGSize",
        AXUIElementCreateSystemWide=lambda: system_element,
        AXUIElementCopyAttributeValue=copy_attr,
        AXValueGetValue=get_value,
    )
    monkeypatch.setitem(sys.modules, "ApplicationServices", fake)
    return fake


def _macos_focused_window_with_button():
    button = _FakeAXElement({
        "AXRole": "AXButton",
        "AXTitle": "Save",
        "AXIdentifier": "saveBtn",
        "AXPosition": _FakeAXValue(_FakePoint(100, 100)),
        "AXSize": _FakeAXValue(_FakeSize(100, 40)),
    })
    window = _FakeAXElement({
        "AXRole": "AXWindow",
        "AXTitle": "App",
        "AXPosition": _FakeAXValue(_FakePoint(0, 0)),
        "AXSize": _FakeAXValue(_FakeSize(800, 600)),
        "AXChildren": [button],
    })
    app = _FakeAXElement({"AXFocusedWindow": window})
    return _FakeAXElement({"AXFocusedApplication": app})


def test_macos_snapshot_walks_the_focused_window(monkeypatch):
    fake = _fake_appservices(monkeypatch, _macos_focused_window_with_button())
    els = desktop_ax._MacosAxProvider(fake).snapshot()
    assert els is not None
    got = {(e.role, e.name, e.automation_id, e.bounds) for e in els}
    # "AX" prefix trimmed, bounds derived from AXPosition + AXSize, AXIdentifier
    # used as the automation-id.
    assert ("Button", "Save", "saveBtn", (100, 100, 200, 140)) in got
    assert ("Window", "App", "", (0, 0, 800, 600)) in got
    # The pure query re-finds the button at its centre, and it's an interactable
    # target the model would be offered.
    assert axtree.element_at(els, 150, 120).name == "Save"


def test_macos_snapshot_none_when_nothing_is_focused(monkeypatch):
    fake = _fake_appservices(monkeypatch, _FakeAXElement({}))  # no focused application
    assert desktop_ax._MacosAxProvider(fake).snapshot() is None


def test_macos_element_without_a_box_is_skipped(monkeypatch):
    boxless = _FakeAXElement({"AXRole": "AXButton", "AXTitle": "NoBox"})  # no position/size
    window = _FakeAXElement({
        "AXRole": "AXWindow",
        "AXTitle": "App",
        "AXPosition": _FakeAXValue(_FakePoint(0, 0)),
        "AXSize": _FakeAXValue(_FakeSize(800, 600)),
        "AXChildren": [boxless],
    })
    app = _FakeAXElement({"AXFocusedWindow": window})
    fake = _fake_appservices(monkeypatch, _FakeAXElement({"AXFocusedApplication": app}))
    els = desktop_ax._MacosAxProvider(fake).snapshot()
    assert [e.name for e in els] == ["App"]  # the boxless button is dropped, walk continues


def test_macos_geometry_accepts_the_renamed_value_type_constants(monkeypatch):
    # Newer pyobjc renamed kAXValueCGPointType -> kAXValueTypeCGPoint; the provider
    # must resolve either. Drop the old names, provide only the new ones.
    fake = _fake_appservices(monkeypatch, _macos_focused_window_with_button())
    del fake.kAXValueCGPointType
    del fake.kAXValueCGSizeType
    fake.kAXValueTypeCGPoint = "CGPoint"
    fake.kAXValueTypeCGSize = "CGSize"
    els = desktop_ax._MacosAxProvider(fake).snapshot()
    assert any(e.name == "Save" and e.bounds == (100, 100, 200, 140) for e in els)


def test_make_provider_builds_the_macos_provider_on_darwin_when_pyobjc_exists(monkeypatch):
    if sys.platform != "darwin":
        return  # the darwin dispatch branch only runs on macOS
    _fake_appservices(monkeypatch, _macos_focused_window_with_button())
    provider = desktop_ax.make_desktop_ax_provider()
    assert isinstance(provider, desktop_ax._MacosAxProvider)
