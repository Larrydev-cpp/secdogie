"""Structural mode: the loop perceives through the accessibility tree and never
captures the screen -- not for the frame, "look", post-action verification,
planning or the briefing.

Every test makes screen capture fail loudly (the backend's `capture` and
`screen.capture_screenshot` both raise), so a single stray screenshot fails the
test rather than passing silently."""
from __future__ import annotations

import pytest
from secdogie_agent import actions, dib_source, elements, loop, screen
from secdogie_agent.axtree import AxElement
from secdogie_agent.dib_source import DibReading
from secdogie_agent.observation import VisualReference
from secdogie_agent.providers.base import Action, VisionProvider

pytest.importorskip("PIL")


class ScreenCaptured(AssertionError):
    pass


def _is_image(data) -> bool:
    # the model gets the AX pad re-encoded (JPEG or PNG); with every capture path
    # raising, an image can only have come from the accessibility tree
    return isinstance(data, bytes) and (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n")


def _no_screen(*_a, **_k):
    raise ScreenCaptured("the screen was captured in structural mode")


class FakeAx:
    """An accessibility provider: returns a fixed UI tree."""

    def __init__(self, tree):
        self.tree = list(tree)
        self.snapshots = 0

    def snapshot(self):
        self.snapshots += 1
        return list(self.tree)


class AxBackend:
    """A desktop backend whose only perception is its accessibility provider."""

    def __init__(self, provider):
        self.ax_provider = provider
        self.executed: list[str] = []

    def setup(self, logger):
        pass

    def capture(self, region=None):
        _no_screen()

    def execute(self, action):
        self.executed.append(action.kind)
        return "ok"

    def element_targets(self):
        snap = self.ax_provider.snapshot() if self.ax_provider else None
        return elements.interactable_targets(snap or [])

    def invoke_element(self, el):
        self.executed.append(f"invoke:{el.name}")
        return f"invoked {el.name}"


class RecordingProvider(VisionProvider):
    def __init__(self, script, subtasks=None):
        self.script = list(script)
        self.subtasks = subtasks
        self.seen: list[tuple[str, bytes | None]] = []
        self.results: list[str] = []

    def next_action(self, task, screenshot_png, screen_size, history):
        self.seen.append((task, screenshot_png))
        self.results = [h.result for h in history]
        return Action.from_dict(self.script.pop(0))

    def plan_task(self, task, screenshot_png, screen_size):
        self.seen.append(("<plan>", screenshot_png))
        return self.subtasks


TREE = [
    AxElement(role="Window", name="Settings", automation_id="", bounds=(0, 0, 800, 600)),
    AxElement(role="Button", name="Save", automation_id="save", bounds=(600, 540, 700, 580)),
    AxElement(role="Edit", name="Name", automation_id="name", bounds=(100, 100, 400, 130)),
]


@pytest.fixture(autouse=True)
def _guards(monkeypatch):
    monkeypatch.setattr(screen, "capture_screenshot", _no_screen)
    monkeypatch.setattr(loop.time, "sleep", lambda s: None)
    monkeypatch.setattr(actions, "execute", lambda action, **kw: "ok")


def _run(script, *, tree=TREE, subtasks=None, **cfg):
    backend = AxBackend(FakeAx(tree))
    provider = RecordingProvider(script, subtasks=subtasks)
    config = loop.AgentConfig(task="save the settings", auto=True, max_steps=10, backend=backend,
                              structural=True, action_pause=0, **cfg)
    return loop.run(provider, config), backend, provider


def test_runs_a_task_without_ever_capturing_the_screen():
    rc, backend, provider = _run([
        {"action": "click_element", "element": "e1"},
        {"action": "left_click", "x": 650, "y": 560},  # post-action verify path runs here
        {"action": "done", "text": "saved"},
    ])
    assert rc == 0
    assert backend.executed[0].startswith("invoke:") and "left_click" in backend.executed
    task, image = provider.seen[0]
    assert _is_image(image)  # the AX pad figure
    assert '"Save"' in task and "[e1]" in task  # the element listing
    assert backend.ax_provider.snapshots > 0


def test_look_and_planning_do_not_capture():
    rc, _backend, provider = _run(
        [{"action": "look"}, {"action": "done", "text": "ok"}],
        subtasks=["press save"], plan=True,
    )
    assert rc == 0
    plan_calls = [img for t, img in provider.seen if t == "<plan>"]
    assert plan_calls and _is_image(plan_calls[0])  # planned on the AX pad
    # after "look" the next step got a freshly built pad, still no screenshot
    assert len([t for t, _ in provider.seen if t != "<plan>"]) == 2


def test_track_click_is_refused_with_a_reason():
    rc, backend, provider = _run([
        {"action": "track_click", "x": 10, "y": 10, "seconds": 1},
        {"action": "done", "text": "ok"},
    ])
    assert rc == 0
    assert "track_click" not in backend.executed
    # the refusal is fed back to the model in its step history
    assert any("refused: track_click" in r for r in provider.results)


def test_no_accessibility_provider_stops_instead_of_screenshotting(monkeypatch):
    provider = RecordingProvider([{"action": "done", "text": "ok"}])
    rc = loop.run(provider, loop.AgentConfig(task="x", auto=True, backend=AxBackend(None), structural=True))
    assert rc == 4 and provider.seen == []

    # without an injected backend, structural mode asks the platform for its
    # provider -- and stops when there is none
    from secdogie_agent import desktop_ax
    monkeypatch.setattr(desktop_ax, "make_desktop_ax_provider", lambda logger=None: None)
    rc2 = loop.run(RecordingProvider([{"action": "done", "text": "ok"}]),
                   loop.AgentConfig(task="x", auto=True, structural=True))
    assert rc2 == 4


def test_dib_summary_rides_along(monkeypatch):
    ref = VisualReference(address=1, width=160, height=96, bit_count=32, content_hash="h")
    monkeypatch.setattr(dib_source, "inspect_dibs", lambda pid: DibReading(pid, (ref,)))
    rc, _backend, provider = _run([{"action": "done", "text": "ok"}], dib_pid=1234)
    assert rc == 0
    assert "dib: 1 bitmap(s) 160x96 32bpp" in provider.seen[0][0]


def test_default_is_unchanged():
    assert loop.AgentConfig(task="x").structural is False


def test_the_guard_is_real_without_structural_mode():
    # Contrast: the same backend with structural off takes the screenshot path,
    # which trips the guard -- so the tests above prove something.
    backend = AxBackend(FakeAx(TREE))
    config = loop.AgentConfig(task="x", auto=True, max_steps=3, backend=backend, action_pause=0)
    with pytest.raises(ScreenCaptured):
        loop.run(RecordingProvider([{"action": "done", "text": "ok"}]), config)
