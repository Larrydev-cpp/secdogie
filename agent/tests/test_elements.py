"""Tests for the live-loop element-targeting layer (elements.py) and its
DesktopBackend wiring. All headless: the pure listing/resolution logic and
element_targets against a fake accessibility provider. The real UI-Automation /
AT-SPI walk is on-machine (see desktop_ax.py) and tested only for its mapping in
test_axtree.py -- here the point is that shaping a snapshot into a model listing
and resolving the model's pick are provable without a desktop."""
from secdogie_agent import axtree, elements
from secdogie_agent.backend import DesktopBackend, ElementAware
from secdogie_agent.providers.base import VALID_ACTIONS, Action


def _tree():
    return [
        axtree.AxElement(role="Window", name="App", automation_id="", bounds=(0, 0, 800, 600)),
        axtree.AxElement(role="Pane", name="", automation_id="", bounds=(0, 40, 800, 600)),
        axtree.AxElement(role="Button", name="Save", automation_id="saveBtn", bounds=(100, 100, 200, 140)),
        axtree.AxElement(role="Button", name="Cancel", automation_id="", bounds=(220, 100, 320, 140)),
        axtree.AxElement(role="Edit", name="Filename", automation_id="fileBox", bounds=(100, 200, 400, 230)),
        axtree.AxElement(role="Button", name="", automation_id="", bounds=(500, 100, 540, 140)),  # anonymous
    ]


class FakeProvider:
    """A DesktopAxProvider whose snapshot the test controls."""

    def __init__(self, snapshot):
        self._snapshot = snapshot

    def snapshot(self):
        return self._snapshot


# -- interactable_targets -----------------------------------------------------

def test_interactable_targets_keeps_only_identifiable_interactables_in_order():
    targets = elements.interactable_targets(_tree())
    # Window/Pane are structural (not interactable roles); the anonymous Button
    # has an interactable role but nothing to identify it by -> dropped. The rest
    # stay in snapshot order.
    assert [(t.role, t.name) for t in targets] == [
        ("Button", "Save"),
        ("Button", "Cancel"),
        ("Edit", "Filename"),
    ]


def test_interactable_targets_matches_role_case_insensitively_across_platforms():
    # Linux AT-SPI reports "push button"/"entry"; Windows reports "Button"/"Edit".
    linux = [
        axtree.AxElement(role="push button", name="OK", automation_id="", bounds=(0, 0, 50, 20)),
        axtree.AxElement(role="entry", name="Name", automation_id="", bounds=(0, 30, 50, 50)),
        axtree.AxElement(role="filler", name="pad", automation_id="", bounds=(0, 60, 50, 80)),  # not interactable
    ]
    assert [t.name for t in elements.interactable_targets(linux)] == ["OK", "Name"]


def test_interactable_targets_empty_when_nothing_qualifies():
    only_structure = [axtree.AxElement(role="Pane", name="", automation_id="", bounds=(0, 0, 9, 9))]
    assert elements.interactable_targets(only_structure) == []


# -- render_for_model ---------------------------------------------------------

def test_render_lists_targets_with_1_based_refs_and_ids():
    targets = elements.interactable_targets(_tree())
    text = elements.render_for_model(targets)
    assert '[e1] Button "Save" (id=saveBtn)' in text
    assert '[e2] Button "Cancel"' in text and "(id=" not in text.split("[e2]")[1].split("\n")[0]
    assert '[e3] Edit "Filename" (id=fileBox)' in text
    assert "click_element" in text  # tells the model how to use a ref


def test_render_empty_targets_is_empty_string():
    # So the caller appends nothing and the step's prompt is byte-for-byte unchanged.
    assert elements.render_for_model([]) == ""


# -- resolve_ref / point_for_ref ----------------------------------------------

def test_resolve_ref_accepts_eN_bare_int_and_whitespace():
    targets = elements.interactable_targets(_tree())
    assert elements.resolve_ref(targets, "e1").name == "Save"
    assert elements.resolve_ref(targets, "E3").name == "Filename"  # case-insensitive
    assert elements.resolve_ref(targets, "  e2 ").name == "Cancel"  # trimmed
    assert elements.resolve_ref(targets, 2).name == "Cancel"        # bare int
    assert elements.resolve_ref(targets, "3").name == "Filename"    # digits without the e


def test_resolve_ref_rejects_out_of_range_and_junk():
    targets = elements.interactable_targets(_tree())
    assert elements.resolve_ref(targets, "e0") is None      # 1-based; there is no e0
    assert elements.resolve_ref(targets, "e99") is None     # past the end
    assert elements.resolve_ref(targets, "Save") is None    # a name is not a ref
    assert elements.resolve_ref(targets, "") is None
    assert elements.resolve_ref(targets, None) is None
    assert elements.resolve_ref(targets, True) is None      # bool is not a valid index


def test_point_for_ref_returns_the_elements_real_pixel_center():
    targets = elements.interactable_targets(_tree())
    assert elements.point_for_ref(targets, "e1") == (150, 120)   # Save button centre
    assert elements.point_for_ref(targets, "e9") is None


# -- action schema ------------------------------------------------------------

def test_click_element_is_a_valid_action_and_carries_the_ref():
    assert "click_element" in VALID_ACTIONS
    a = Action.from_dict({"action": "click_element", "element": "e2", "reasoning": "click Cancel"})
    assert a.kind == "click_element" and a.element == "e2"


def test_click_element_element_is_normalized_to_str():
    # A model that sends a bare integer ref still resolves (from_dict stringifies).
    a = Action.from_dict({"action": "click_element", "element": 3})
    assert a.element == "3"
    a2 = Action.from_dict({"action": "left_click", "x": 1, "y": 2})
    assert a2.element is None  # absent on actions that don't use it


# -- DesktopBackend.element_targets (via a fake provider) ---------------------

def test_desktop_backend_is_element_aware_and_returns_filtered_targets():
    b = DesktopBackend(ax_provider=FakeProvider(_tree()))
    assert isinstance(b, ElementAware)
    assert [t.name for t in b.element_targets()] == ["Save", "Cancel", "Filename"]


def test_element_targets_empty_without_a_provider_or_snapshot():
    assert DesktopBackend().element_targets() == []                       # no provider
    assert DesktopBackend(ax_provider=FakeProvider(None)).element_targets() == []  # tree unreadable
    assert DesktopBackend(ax_provider=FakeProvider([])).element_targets() == []    # empty tree
