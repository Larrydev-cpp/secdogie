import logging

import pytest
from secdogie_agent import actions, axtree, loop, screen
from secdogie_agent.backend import DesktopBackend, ElementSelector
from secdogie_agent.macro import Macro, MacroStep
from secdogie_agent.providers.base import Action, VisionProvider


class ScriptedProvider(VisionProvider):
    """Replays a fixed list of actions, ignoring the screenshot/history --
    stands in for a real vision LLM in tests."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def next_action(self, task, screenshot_png, screen_size, history):
        self.calls += 1
        d = self.script.pop(0)
        return Action.from_dict(d)
