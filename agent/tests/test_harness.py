"""Headless tests for the desktop harness: omit-image policy, AX invoke
instead of mouse, SetValue instead of typing. Fake providers stand in for
the on-machine press/set_value half -- the real UIA/AT-SPI/AX calls are
proved against fakes in test_axtree.py, same as snapshot."""
from secdogie_agent import axtree, elements, harness, loop, screen
from secdogie_agent.backend import DesktopBackend
from secdogie_agent.providers.base import Action, VisionProvider


def _tree():
    return [
        axtree.AxElement(role="Window", name="App", automation_id="", bounds=(0, 0, 800, 600)),
        axtree.AxElement(role="Button", name="Save", automation_id="saveBtn", bounds=(100, 100, 200, 140)),
        axtree.AxElement(role="Button", name="Cancel", automation_id="", bounds=(220, 100, 320, 140)),
        axtree.AxElement(role="Edit", name="Filename", automation_id="fileBox", bounds=(100, 200, 400, 230)),
    ]


# -- pure policy --------------------------------------------------------------

def test_should_omit_screenshot_only_when_the_tree_is_healthy_and_we_are_not_looking():
    targets = elements.interactable_targets(_tree())
    assert harness.should_omit_screenshot(targets, refresh_view=True, boost_detail=False) is False
    assert harness.should_omit_screenshot(targets, refresh_view=False, boost_detail=True) is False
    assert harness.should_omit_screenshot([], refresh_view=False, boost_detail=False) is False
    assert harness.should_omit_screenshot(targets, refresh_view=False, boost_detail=False) is True


def test_is_editable_matches_all_three_platform_vocabularies():
    assert harness.is_editable(axtree.AxElement(role="Edit", name="n", automation_id="", bounds=(0, 0, 1, 1)))
    assert harness.is_editable(axtree.AxElement(role="entry", name="n", automation_id="", bounds=(0, 0, 1, 1)))
    assert harness.is_editable(axtree.AxElement(role="TextField", name="n", automation_id="", bounds=(0, 0, 1, 1)))
    assert not harness.is_editable(axtree.AxElement(role="Button", name="n", automation_id="", bounds=(0, 0, 1, 1)))


# -- backend invoke / set_value against a fake provider -----------------------

class FakeAx:
    """DesktopAxProvider whose snapshot/press/set_value the test controls."""

    def __init__(self, snapshot, *, press_ok=True, set_ok=True):
        self._snapshot = snapshot
        self.press_ok = press_ok
        self.set_ok = set_ok
        self.press_calls: list[dict] = []
        self.set_calls: list[tuple] = []

    def snapshot(self):
        return self._snapshot

    def press(self, **attrs):
        self.press_calls.append(attrs)
        return self.press_ok

    def set_value(self, text, **attrs):
        self.set_calls.append((text, attrs))
        return self.set_ok


def test_invoke_element_calls_press_with_the_selector_and_does_not_claim_a_mouse_click():
    b = DesktopBackend(ax_provider=FakeAx(_tree()))
    el = elements.interactable_targets(_tree())[0]  # Save
    result = b.invoke_element(el)
    assert result is not None and "cursor not moved" in result
    assert b.ax_provider.press_calls == [{"automation_id": "saveBtn", "role": "Button"}]


def test_invoke_element_none_when_press_refuses_or_is_missing():
    el = elements.interactable_targets(_tree())[0]
    assert DesktopBackend(ax_provider=FakeAx(_tree(), press_ok=False)).invoke_element(el) is None
    # A snapshot-only provider (the original FakeProvider shape) has no press().
    class SnapshotOnly:
        def snapshot(self):
            return _tree()

    assert DesktopBackend(ax_provider=SnapshotOnly()).invoke_element(el) is None
    assert DesktopBackend().invoke_element(el) is None  # no provider at all


def test_set_element_value_calls_set_value_and_falls_back_when_refused():
    el = elements.interactable_targets(_tree())[2]  # Filename
    b = DesktopBackend(ax_provider=FakeAx(_tree()))
    result = b.set_element_value(el, "part.dwg")
    assert result is not None and "part.dwg" in result and "cursor not moved" in result
    assert b.ax_provider.set_calls == [("part.dwg", {"automation_id": "fileBox", "role": "Edit"})]
    assert DesktopBackend(ax_provider=FakeAx(_tree(), set_ok=False)).set_element_value(el, "x") is None


# -- live loop ----------------------------------------------------------------

class RecordingProvider(VisionProvider):
    """Scripted model that records the screenshot it was handed each turn."""

    def __init__(self, script):
        self.script = list(script)
        self.shots: list[bytes | None] = []
        self.tasks: list[str] = []

    def next_action(self, task, screenshot_png, screen_size, history):
        self.shots.append(screenshot_png)
        self.tasks.append(task)
        return Action.from_dict(self.script.pop(0))


def _patch_capture(monkeypatch, executed):
    monkeypatch.setattr(screen, "capture_screenshot", lambda region=None: (b"fake-png", (1920, 1080)))
    monkeypatch.setattr(screen, "prepare_for_model", lambda raw, size, **kw: (raw, size, 1.0))
    from secdogie_agent import actions

    monkeypatch.setattr(actions, "execute", lambda action, **kw: executed.append(action) or "ok")


def test_loop_invokes_via_ax_and_does_not_move_the_mouse(monkeypatch):
    executed = []
    _patch_capture(monkeypatch, executed)
    ax = FakeAx(_tree())
    backend = DesktopBackend(ax_provider=ax)
    provider = RecordingProvider(
        [
            {"action": "click_element", "element": "e1", "reasoning": "Save"},
            {"action": "done", "text": "saved"},
        ]
    )
    rc = loop.run(
        provider,
        loop.AgentConfig(
            task="save the file", auto=True, max_steps=5, desktop_ax=True, backend=backend,
            verify_actions=False,
        ),
    )
    assert rc == 0
    assert ax.press_calls  # accessibility Invoke/Press, not pyautogui
    assert executed == []  # actions.execute (the mouse) must not have run


def test_loop_falls_back_to_a_pixel_click_when_press_returns_false(monkeypatch):
    executed = []
    _patch_capture(monkeypatch, executed)
    backend = DesktopBackend(ax_provider=FakeAx(_tree(), press_ok=False))
    provider = RecordingProvider(
        [
            {"action": "click_element", "element": "e1", "reasoning": "Save"},
            {"action": "done", "text": "ok"},
        ]
    )
    rc = loop.run(
        provider,
        loop.AgentConfig(
            task="t", auto=True, max_steps=5, desktop_ax=True, backend=backend,
            verify_actions=False,
        ),
    )
    assert rc == 0
    assert [a.kind for a in executed] == ["left_click"]
    assert (executed[0].x, executed[0].y) == (150, 120)  # Save centre


def test_loop_omits_the_screenshot_after_the_first_ax_frame(monkeypatch):
    executed = []
    _patch_capture(monkeypatch, executed)
    backend = DesktopBackend(ax_provider=FakeAx(_tree()))
    provider = RecordingProvider(
        [
            {"action": "click_element", "element": "e1", "reasoning": "Save"},
            {"action": "click_element", "element": "e2", "reasoning": "Cancel"},
            {"action": "done", "text": "ok"},
        ]
    )
    rc = loop.run(
        provider,
        loop.AgentConfig(
            task="t", auto=True, max_steps=8, desktop_ax=True, backend=backend,
            verify_actions=False,
        ),
    )
    assert rc == 0
    assert provider.shots[0] == b"fake-png"          # first frame still has pixels
    assert provider.shots[1] is None                 # subsequent AX-healthy step: no image
    assert harness.OMIT_IMAGE_NOTE[:20] in provider.tasks[1]


def test_loop_sends_pixels_again_after_look(monkeypatch):
    executed = []
    _patch_capture(monkeypatch, executed)
    backend = DesktopBackend(ax_provider=FakeAx(_tree()))
    provider = RecordingProvider(
        [
            {"action": "click_element", "element": "e1"},
            {"action": "look"},
            {"action": "done", "text": "ok"},
        ]
    )
    rc = loop.run(
        provider,
        loop.AgentConfig(
            task="t", auto=True, max_steps=8, desktop_ax=True, backend=backend,
            verify_actions=False,
        ),
    )
    assert rc == 0
    assert provider.shots[0] == b"fake-png"
    assert provider.shots[1] is None       # AX-only turn, model asks for look
    assert provider.shots[2] == b"fake-png"  # look honoured: pixels come back


def test_loop_types_into_a_listed_field_via_set_value(monkeypatch):
    executed = []
    _patch_capture(monkeypatch, executed)
    ax = FakeAx(_tree())
    backend = DesktopBackend(ax_provider=ax)
    provider = RecordingProvider(
        [
            {"action": "type", "element": "e3", "text": "part.dwg", "reasoning": "filename"},
            {"action": "done", "text": "ok"},
        ]
    )
    rc = loop.run(
        provider,
        loop.AgentConfig(
            task="t", auto=True, max_steps=5, desktop_ax=True, backend=backend,
            verify_actions=False,
        ),
    )
    assert rc == 0
    assert ax.set_calls == [("part.dwg", {"automation_id": "fileBox", "role": "Edit"})]
    assert executed == []  # no synthesized keystrokes


def test_pixel_guess_on_an_ax_only_turn_is_refused_not_clicked(monkeypatch):
    executed = []
    _patch_capture(monkeypatch, executed)
    backend = DesktopBackend(ax_provider=FakeAx(_tree()))
    provider = RecordingProvider(
        [
            {"action": "click_element", "element": "e1"},
            {"action": "left_click", "x": 10, "y": 10, "reasoning": "guess"},
            {"action": "done", "text": "ok"},
        ]
    )
    rc = loop.run(
        provider,
        loop.AgentConfig(
            task="t", auto=True, max_steps=8, desktop_ax=True, backend=backend,
            verify_actions=False,
        ),
    )
    assert rc == 0
    assert executed == []  # the guessed click was never delivered
