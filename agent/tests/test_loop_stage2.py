"""Stage-2 loop hooks: step outcomes for episodic memory, the model's Gate 1
intent fields reaching the plan gate, and staged memory (remember goes to a
quarantine hook; the prompt recalls only confirmed memory)."""
from __future__ import annotations

import pytest
from secdogie_agent import actions, loop, screen
from secdogie_agent.providers.base import Action, VisionProvider


class RecordingProvider(VisionProvider):
    """Replays actions and keeps the task text it was shown each step."""

    def __init__(self, script):
        self.script = list(script)
        self.tasks: list[str] = []
        self.results: list[list[str]] = []  # the step results in the history shown each step

    def next_action(self, task, screenshot_png, screen_size, history):
        self.tasks.append(task)
        self.results.append([h.result for h in history])
        return Action.from_dict(self.script.pop(0))


@pytest.fixture(autouse=True)
def _headless(monkeypatch):
    monkeypatch.setattr(loop.time, "sleep", lambda s: None)
    monkeypatch.setattr(screen, "capture_screenshot", lambda region=None: (b"fake-png", (1920, 1080)))
    monkeypatch.setattr(screen, "prepare_for_model", lambda raw, size, **kw: (raw, size, 1.0))
    monkeypatch.setattr(actions, "execute", lambda action, **kw: "ok")


# ---- classify_result -----------------------------------------------------------


@pytest.mark.parametrize("result,outcome", [
    ("refused by plan gate: known failure", "rejected"),
    ("skipped (user declined)", "rejected"),
    ("skipped (read-only mode)", "rejected"),
    ("refused: not in the allowlist", "rejected"),
    ("error: element vanished", "failed"),
    ("clicked (100, 200)" + loop._NO_CHANGE_NOTE, "no_change"),
    ("clicked (100, 200)", "ok"),
    ("launched: started as SYSTEM (pid 7)", "ok"),
    ("not-elevated: run the agent as admin", "failed"),
    ("failed", "failed"),
    ("", "ok"),
])
def test_classify_result(result, outcome):
    assert loop.classify_result(result) == outcome


# ---- intent fields reach the gate ------------------------------------------------


def test_the_models_rollback_and_irreversible_reach_the_plan_gate():
    seen = []
    provider = RecordingProvider([
        {"action": "open", "path": "report.pdf", "rollback": "close the viewer"},
        {"action": "key", "keys": ["ctrl", "s"], "irreversible": True},
        {"action": "key", "keys": ["ctrl", "w"], "irreversible": "yes"},  # only a literal true counts
        {"action": "done", "text": "ok"},
    ])
    config = loop.AgentConfig(task="t", auto=True, max_steps=10,
                              plan_gate=lambda view, recent: seen.append(view) or (False, "noted"),
                              approve_action=lambda prompt, high_risk: True)
    assert loop.run(provider, config) == 0
    assert [(v["rollback"], v["irreversible"]) for v in seen] == [
        ("close the viewer", False), ("", True), ("", False)]


# ---- ask_user through an operator that answers in words ----------------------------


@pytest.mark.parametrize("reply,code,expect", [
    ("the Downloads folder", 0, "the operator answered: the Downloads folder"),
    ("   ", 2, None),  # an empty answer is no answer
    (True, 0, "user confirmed, continuing"),  # the old boolean protocol still works
    (False, 2, None),
])
def test_ask_user_takes_the_operators_answer_or_a_plain_yes_no(reply, code, expect):
    seen = []
    provider = RecordingProvider([
        {"action": "ask_user", "text": "which folder?"},
        {"action": "done", "text": "ok"},
    ])
    config = loop.AgentConfig(task="t", auto=True, max_steps=10, ask_operator=lambda q: seen.append(q) or reply)
    assert loop.run(provider, config) == code
    assert seen == ["which folder?"]
    if expect:
        assert provider.results[-1] == [expect]  # the model sees the answer in its history


# ---- staged memory hooks -----------------------------------------------------------


def test_remember_goes_to_the_hook_and_the_prompt_recalls_only_what_the_hook_returns():
    noted = []
    provider = RecordingProvider([
        {"action": "remember", "text": "Save is in the toolbar", "key": "save"},
        {"action": "done", "text": "ok"},
    ])
    config = loop.AgentConfig(task="t", auto=True, max_steps=10,
                              remember_hook=lambda value, key: noted.append((value, key)) or key,
                              memory_block=lambda: "- export_format: PDF")
    assert loop.run(provider, config) == 0
    assert noted == [("Save is in the toolbar", "save")]
    assert all("export_format: PDF" in t for t in provider.tasks)
    assert all("Save is in the toolbar" not in t for t in provider.tasks)  # held, not re-injected
    assert "held for the operator to confirm" in loop._MEMORY_DIRECTIVE


def test_a_refused_remember_is_reported_and_a_broken_recall_costs_nothing():
    provider = RecordingProvider([
        {"action": "remember", "text": "hunter2", "key": "password"},
        {"action": "done", "text": "ok"},
    ])

    def refuse(value, key):
        raise ValueError("that looks like a credential")

    def broken():
        raise RuntimeError("db locked")

    config = loop.AgentConfig(task="t", auto=True, max_steps=10, remember_hook=refuse, memory_block=broken)
    assert loop.run(provider, config) == 0  # neither the refusal nor the broken recall stops the run
