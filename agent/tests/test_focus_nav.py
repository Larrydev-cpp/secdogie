"""Keyboard focus traversal: the pure navigator over an injected focus ring,
plus each provider's focused_node() against a fake accessibility layer."""
from __future__ import annotations

import sys

import pytest
from secdogie_agent import desktop_ax
from secdogie_agent.axtree import AxElement
from secdogie_agent.focus_nav import (
    DEFAULT_KEYS,
    FocusNavigator,
    Outcome,
    match_by,
    navigate_to,
)


def el(role, name, aid=""):
    return AxElement(role, name, aid, (0, 0, 10, 10))


class Ring:
    """A focus ring the keys walk. ``rings`` maps a key to an ordered list of
    stops; pressing that key advances a shared cursor through its list and wraps.
    ``start`` is the initially focused stop. A key not in ``rings`` isn't
    deliverable (send returns False)."""

    def __init__(self, start, rings, *, deliver_all=True):
        self.focus = start
        self.rings = rings
        self.deliver_all = deliver_all
        self.sent: list[str] = []

    def read(self):
        return self.focus

    def send(self, key):
        self.sent.append(key)
        ring = self.rings.get(key)
        if ring is None:
            return bool(self.deliver_all) and False  # not on any ring here
        i = ring.index(self.focus) if self.focus in ring else -1
        self.focus = ring[(i + 1) % len(ring)]
        return True


TOOLBAR = [el("Button", "New"), el("Button", "Open"), el("Button", "Save"), el("Button", "Close")]


def test_already_focused_sends_no_keys():
    r = Ring(TOOLBAR[2], {"tab": TOOLBAR})
    res = navigate_to(match_by(name="Save"), r.read, r.send)
    assert res.outcome is Outcome.ALREADY and res.ok and res.steps == 0
    assert r.sent == []


def test_tab_advances_to_the_target():
    r = Ring(TOOLBAR[0], {"tab": TOOLBAR})
    res = navigate_to(match_by(name="Save"), r.read, r.send)
    assert res.outcome is Outcome.REACHED and res.ok
    assert res.focused.name == "Save" and res.steps == 2
    assert [k[1] for k in res.path] == ["New", "Open", "Save"]


def test_target_absent_reports_cycled_without_looping_forever():
    r = Ring(TOOLBAR[0], {"tab": TOOLBAR})
    res = navigate_to(match_by(name="Missing"), r.read, r.send)
    assert res.outcome is Outcome.CYCLED and not res.ok
    assert res.steps == len(TOOLBAR)  # one full lap, then the wrap is detected
    assert r.sent == ["tab"] * len(TOOLBAR)


def test_match_by_automation_id_and_role():
    ring = [el("TextField", "", "user"), el("TextField", "", "pass"), el("Button", "OK", "ok")]
    r = Ring(ring[0], {"tab": ring})
    assert navigate_to(match_by(automation_id="pass"), r.read, r.send).focused.automation_id == "pass"
    r2 = Ring(ring[0], {"tab": ring})
    assert navigate_to(match_by(role="Button"), r2.read, r2.send).focused.name == "OK"


def test_falls_through_to_the_next_key_when_the_first_ring_wraps():
    # Tab cycles the toolbar; the target only sits on the Down ring.
    grid = [el("Cell", "A1"), el("Cell", "A2"), el("Cell", "B1")]
    r = Ring(TOOLBAR[0], {"tab": TOOLBAR, "down": [TOOLBAR[0], *grid]})
    res = navigate_to(match_by(name="B1"), r.read, r.send, keys=("tab", "down"))
    assert res.outcome is Outcome.REACHED and res.focused.name == "B1"
    assert "tab" in r.sent and "down" in r.sent


def test_no_focus_when_focus_cannot_be_read():
    res = navigate_to(match_by(name="x"), lambda: None, lambda k: True)
    assert res.outcome is Outcome.NO_FOCUS and res.focused is None and res.steps == 0


def test_no_focus_midway_is_reported():
    calls = {"n": 0}

    def read():
        calls["n"] += 1
        return TOOLBAR[0] if calls["n"] == 1 else None

    res = navigate_to(match_by(name="Save"), read, lambda k: True)
    assert res.outcome is Outcome.NO_FOCUS and res.steps == 1


def test_stuck_when_no_key_is_deliverable():
    r = Ring(TOOLBAR[0], {})  # no ring accepts any key
    res = navigate_to(match_by(name="Save"), r.read, r.send, keys=("tab", "down"))
    assert res.outcome is Outcome.STUCK and res.steps == 0


def test_budget_caps_the_walk():
    big = [el("Item", str(i)) for i in range(500)]
    r = Ring(big[0], {"tab": big})
    res = navigate_to(match_by(name="499"), r.read, r.send, max_steps=10)
    assert res.outcome is Outcome.EXHAUSTED and res.steps == 10


def test_match_by_requires_a_field():
    with pytest.raises(ValueError):
        match_by()


def test_navigate_requires_a_key():
    with pytest.raises(ValueError):
        navigate_to(match_by(name="x"), lambda: el("B", "x"), lambda k: True, keys=())


def test_navigator_binds_reader_and_sender():
    r = Ring(TOOLBAR[0], {"tab": TOOLBAR, "shift+tab": list(reversed(TOOLBAR))})
    nav = FocusNavigator(r.read, r.send, keys=DEFAULT_KEYS)
    assert nav.current().name == "New"
    res = nav.to(name="Close")
    assert res.ok and res.focused.name == "Close"


# ---- provider focused_node() against fakes -----------------------------------


class _WinControl:
    def __init__(self, name, aid, ctype, rect):
        self.Name, self.AutomationId, self.ControlTypeName = name, aid, ctype
        self.BoundingRectangle = type("R", (), dict(zip("left top right bottom".split(), rect, strict=True)))()


def test_windows_focused_node(monkeypatch):
    focused = _WinControl("Save", "btnSave", "ButtonControl", (10, 10, 110, 40))
    fake = type("A", (), {"GetFocusedControl": staticmethod(lambda: focused)})
    monkeypatch.setitem(sys.modules, "uiautomation", fake)
    got = desktop_ax._WindowsUiaProvider().focused_node()
    assert got == AxElement("Button", "Save", "btnSave", (10, 10, 110, 40))

    none_fake = type("A", (), {"GetFocusedControl": staticmethod(lambda: None)})
    monkeypatch.setitem(sys.modules, "uiautomation", none_fake)
    assert desktop_ax._WindowsUiaProvider().focused_node() is None


class _FakeState:
    def __init__(self, flags):
        self.flags = set(flags)

    def contains(self, flag):
        return flag in self.flags


class _FakeExtents:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.width, self.height = x, y, w, h


class _FakeComponent:
    def __init__(self, ext):
        self._ext = ext

    def getExtents(self, _coords):
        return self._ext


class _Acc:
    def __init__(self, role, name, states=(), ext=None, children=()):
        self._role, self._name, self._states = role, name, states
        self._ext, self._children = ext, list(children)

    def getRoleName(self):
        return self._role

    @property
    def name(self):
        return self._name

    def getState(self):
        return _FakeState(self._states)

    def getChildCount(self):
        return len(self._children)

    def getChildAtIndex(self, i):
        return self._children[i]

    def queryComponent(self):
        if self._ext is None:
            raise LookupError("no component")
        return _FakeComponent(self._ext)


def _atspi_module(monkeypatch, desktop):
    import types

    fake = types.SimpleNamespace(
        STATE_ACTIVE="active",
        STATE_FOCUSED="focused",
        DESKTOP_COORDS=0,
        Registry=types.SimpleNamespace(getDesktop=lambda i: desktop),
    )
    monkeypatch.setitem(sys.modules, "pyatspi", fake)


def test_atspi_focused_node(monkeypatch):
    entry = _Acc("entry", "Search", states=["focused"], ext=_FakeExtents(5, 5, 120, 20))
    button = _Acc("push button", "Go", ext=_FakeExtents(130, 5, 40, 20))
    frame = _Acc("frame", "App", states=["active"], ext=_FakeExtents(0, 0, 400, 300), children=[entry, button])
    app = _Acc("application", "App", children=[frame])
    desktop = _Acc("desktop frame", "", children=[app])
    _atspi_module(monkeypatch, desktop)
    got = desktop_ax._AtspiProvider().focused_node()
    assert got == AxElement("entry", "Search", "", (5, 5, 125, 25))


def test_atspi_focused_node_none_when_nothing_focused(monkeypatch):
    frame = _Acc("frame", "App", states=["active"], ext=_FakeExtents(0, 0, 400, 300),
                 children=[_Acc("push button", "Go", ext=_FakeExtents(1, 1, 10, 10))])
    app = _Acc("application", "App", children=[frame])
    _atspi_module(monkeypatch, _Acc("desktop frame", "", children=[app]))
    assert desktop_ax._AtspiProvider().focused_node() is None


class _AXVal:
    def __init__(self, inner):
        self.inner = inner


def _macos_fake(monkeypatch, app):
    import types

    system = type("E", (), {})()

    def copy_attr(element, attr, _none):
        table = getattr(element, "attrs", {})
        return (0, table[attr]) if attr in table else (-1, None)

    fake = types.SimpleNamespace(
        kAXFocusedApplicationAttribute="AXFocusedApplication",
        kAXFocusedUIElementAttribute="AXFocusedUIElement",
        kAXRoleAttribute="AXRole",
        kAXTitleAttribute="AXTitle",
        kAXDescriptionAttribute="AXDescription",
        kAXValueAttribute="AXValue",
        kAXRoleDescriptionAttribute="AXRoleDescription",
        kAXSelectedTextAttribute="AXSelectedText",
        kAXIdentifierAttribute="AXIdentifier",
        kAXPositionAttribute="AXPosition",
        kAXSizeAttribute="AXSize",
        kAXValueCGPointType="p",
        kAXValueCGSizeType="s",
        AXUIElementCreateSystemWide=lambda: system,
        AXUIElementCopyAttributeValue=copy_attr,
        AXValueGetValue=lambda v, t, n: (True, v.inner),
        AXUIElementSetAttributeValue=lambda *a: -1,
    )
    system.attrs = {"AXFocusedApplication": app}
    monkeypatch.setitem(sys.modules, "ApplicationServices", fake)
    return fake


class _MacEl:
    def __init__(self, attrs):
        self.attrs = attrs


def test_macos_focused_node(monkeypatch):
    field = _MacEl({
        "AXRole": "AXTextField",
        "AXTitle": "Email",
        "AXPosition": _AXVal(type("P", (), {"x": 20, "y": 40})()),
        "AXSize": _AXVal(type("S", (), {"width": 200, "height": 24})()),
    })
    app = _MacEl({"AXFocusedUIElement": field})
    _macos_fake(monkeypatch, app)
    prov = desktop_ax._MacosAxProvider(sys.modules["ApplicationServices"], unlock_hidden_trees=False)
    assert prov.focused_node() == AxElement("TextField", "Email", "", (20, 40, 220, 64))


def test_macos_focused_node_none_when_no_focus(monkeypatch):
    app = _MacEl({})
    _macos_fake(monkeypatch, app)
    prov = desktop_ax._MacosAxProvider(sys.modules["ApplicationServices"], unlock_hidden_trees=False)
    assert prov.focused_node() is None


# ---- backend keyboard-reach fallback -----------------------------------------

from secdogie_agent import backend as backend_mod  # noqa: E402
from secdogie_agent.backend import DesktopBackend  # noqa: E402


class _ReachProvider:
    """press() by identity fails (the target isn't tree-reachable); focus_node
    walks a ring advanced by the keys the backend sends."""

    def __init__(self, ring, start):
        self.ring = ring
        self.focus = start
        self.keys: list[str] = []

    def press(self, **attrs):
        return False  # cannot re-find it by identity

    def focused_node(self):
        return self.focus

    def _advance(self, key):
        self.keys.append(key)
        if key == "tab" and self.focus in self.ring:
            self.focus = self.ring[(self.ring.index(self.focus) + 1) % len(self.ring)]


def _backend_for(provider, monkeypatch, supported=True):
    monkeypatch.setattr(backend_mod, "_keyboard_reach_supported", lambda: supported)
    b = DesktopBackend(ax_provider=provider)
    # Route sent keys into the fake ring instead of pyautogui.
    monkeypatch.setattr(b, "_send_key", lambda chord: (provider._advance(chord) or True))
    return b


def test_invoke_falls_back_to_keyboard_reach(monkeypatch):
    ring = [el("Button", "New", "new"), el("Button", "Save", "save")]
    prov = _ReachProvider(ring, ring[0])
    b = _backend_for(prov, monkeypatch)
    out = b.invoke_element(el("Button", "Save", "save"))
    assert out is not None and "by keyboard" in out and "Space" in out
    assert prov.keys == ["tab", "space"]  # one Tab to reach Save, then activate


def test_keyboard_reach_refused_off_windows_linux(monkeypatch):
    ring = [el("Button", "Save", "save")]
    prov = _ReachProvider(ring, ring[0])
    b = _backend_for(prov, monkeypatch, supported=False)
    assert b.invoke_element(el("Button", "Save", "save")) is None
    assert prov.keys == []


def test_keyboard_reach_only_activates_safe_roles(monkeypatch):
    # A generic pane is focusable but Space wouldn't "click" it: don't pretend.
    prov = _ReachProvider([el("Pane", "canvas", "c")], el("Pane", "canvas", "c"))
    b = _backend_for(prov, monkeypatch)
    assert b.invoke_element(el("Pane", "canvas", "c")) is None
    assert "space" not in prov.keys


def test_keyboard_reach_gives_up_when_target_absent(monkeypatch):
    ring = [el("Button", "New", "new"), el("Button", "Open", "open")]
    prov = _ReachProvider(ring, ring[0])
    b = _backend_for(prov, monkeypatch)
    assert b.invoke_element(el("Button", "Missing", "missing")) is None
    assert "space" not in prov.keys  # never activated something that wasn't the target


def test_identity_press_success_skips_keyboard(monkeypatch):
    class _OK:
        def __init__(self):
            self.keys = []

        def press(self, **attrs):
            return True

        def focused_node(self):
            return None

    prov = _OK()
    b = _backend_for(prov, monkeypatch)
    out = b.invoke_element(el("Button", "Save", "save"))
    assert "via accessibility (cursor not moved)" in out and prov.keys == []


def test_reach_supported_matches_platform(monkeypatch):
    for plat, ok in [("win32", True), ("linux", True), ("darwin", False)]:
        monkeypatch.setattr(backend_mod.sys, "platform", plat)
        assert backend_mod._keyboard_reach_supported() is ok
