"""Stand-ins for the end-to-end tests: a scripted model and a fake desktop with
an accessibility tree. Everything else -- the production runner, the agent
loop, both gates, the memory, the dialogue -- is the real thing.

``install(setattr)`` wires them into the agent the way the production runner
looks them up. Tests pass ``monkeypatch.setattr``; the process launcher
(``fake_desk_node.py``) passes the builtin ``setattr``.
"""
from __future__ import annotations

import struct
import zlib

from secdogie_agent import cli_common, screen
from secdogie_agent import loop as agent_loop
from secdogie_agent.axtree import AxElement
from secdogie_agent.providers.base import Action, VisionProvider

DELETE_BUTTON = AxElement(role="Button", name="Delete", automation_id="ID_DELETE", bounds=(0, 0, 40, 20))
NAME_FIELD = AxElement(role="Edit", name="File name", automation_id="", bounds=(0, 30, 200, 50))

FILE_THE_REPORT = [
    {"action": "click_element", "element": "e2"},  # focus the file name field (not destructive)
    {"action": "key", "keys": ["delete"], "rollback": "restore it from the Trash"},  # high-risk: Gate 2
    {"action": "ask_user", "text": "Which folder should the report go to?"},
    {"action": "remember", "text": "reports go to ~/Reports", "key": "report-folder"},
    {"action": "done", "text": "filed"},
]
TIDY_UP = [{"action": "left_click", "x": 5, "y": 5}, {"action": "done", "text": "tidied"}]

# The operator's side of the scenario, as a headless App script.
OPERATOR_SCRIPT = [
    {"op": "add_goal", "title": "file the report", "goal_id": "g1"},
    {"op": "approve", "action": {"kind": "key", "text": "delete"}},
    {"op": "answer", "match": "folder", "text": "Desktop"},
    {"op": "expect_status", "match": "goal g1 finished: exit 0"},
    {"op": "confirm_memory", "key": "report-folder"},
    {"op": "expect_view", "match": 'Button "Delete"'},
    {"op": "add_goal", "title": "tidy up", "goal_id": "g2"},
    {"op": "add_goal", "title": "tidy up", "goal_id": "g3"},
    {"op": "add_goal", "title": "tidy up", "goal_id": "g4"},
    {"op": "expect_status", "match": "goal g4 finished"},
    {"op": "add_goal", "title": "tidy up", "goal_id": "g5"},
    {"op": "expect_status", "match": "goal g5 finished"},
]


def scripts() -> list:
    """One model script per goal, in goal-id order."""
    return [list(FILE_THE_REPORT), list(TIDY_UP), list(TIDY_UP), list(TIDY_UP), list(TIDY_UP)]


def _png() -> bytes:
    raw = b"\x00" + b"\x80\x80\x80" * 4
    ihdr = struct.pack(">IIBBBBB", 4, 1, 8, 2, 0, 0, 0)

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class FakeDesk:
    """A desktop with an accessibility tree; a click at (5, 5) always fails."""

    def __init__(self):
        self.done: list[str] = []

    def setup(self, logger):
        pass

    def capture(self, region=None):
        return _png(), (200, 100)

    def execute(self, action):
        if action.kind == "left_click":
            return "error: nothing to click there"
        self.done.append(action.kind)
        return "ok"

    def element_targets(self):
        return [DELETE_BUTTON, NAME_FIELD]

    def invoke_element(self, el):
        self.done.append(f"invoke:{el.name}")
        return "invoked"


class Scripted(VisionProvider):
    def __init__(self, script, seen):
        self.script, self.seen = list(script), seen

    def next_action(self, task, screenshot_png, screen_size, history):
        self.seen.append([getattr(h, "result", "") for h in history])
        return Action.from_dict(self.script.pop(0))


def install(set_attr) -> tuple[FakeDesk, list]:
    """Patch the model and the desktop into the production runner's lookups.
    Returns the desk (what it executed) and the model's view of its history."""
    queue, histories, desk = scripts(), [], FakeDesk()
    set_attr(cli_common, "resolve_provider", lambda args, prog: Scripted(queue.pop(0), histories))
    real_kwargs = cli_common.loop_config_kwargs

    def kwargs(args, *, task, backend=None):
        kw = real_kwargs(args, task=task, backend=desk)
        kw.update(action_pause=0, verify_actions=False)
        return kw

    set_attr(cli_common, "loop_config_kwargs", kwargs)
    set_attr(screen, "prepare_for_model", lambda raw, size, **kw: (raw, size, 1.0))
    set_attr(agent_loop.time, "sleep", lambda s: None)
    return desk, histories
