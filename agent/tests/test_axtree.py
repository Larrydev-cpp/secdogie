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
    if sys.platform.startswith("win"):
        # On Windows it depends on whether `uiautomation` is installed; either a
        # provider or a clean None, never an exception.
        assert provider is None or isinstance(provider, desktop_ax.DesktopAxProvider)
    else:
        assert provider is None
