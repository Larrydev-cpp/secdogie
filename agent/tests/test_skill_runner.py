"""skill_runner wiring tests -- with a fake backend + provider, no display."""
import json

from secdogie_agent import screen
from secdogie_agent.skill_runner import _to_action, run_skill_file


def test_to_action_coerces_parameterized_numeric_fields():
    a = _to_action({"action": "left_click", "x": "10", "y": "20"})
    assert a.kind == "left_click" and a.x == 10 and a.y == 20
    b = _to_action({"action": "wait", "seconds": "2"})
    assert b.seconds == 2.0


class FakeBackend:
    def __init__(self):
        self.executed = []

    def setup(self, logger):
        pass

    def capture(self, region):
        return b"png", (100, 100)

    def execute(self, action):
        self.executed.append(action.kind)
        return "ok"


class FakeProvider:
    def __init__(self, answers):
        self.answers = list(answers)
        self.checked = []

    def check_condition(self, question, screenshot_png, screen_size):
        self.checked.append(question)
        return self.answers.pop(0)


def test_run_skill_file_executes_actions_and_checks_conditions(tmp_path, monkeypatch):
    monkeypatch.setattr(screen, "prepare_for_model", lambda raw, size, **kw: (raw, size, 1.0))
    lib = {"skills": {"main": {"body": [
        {"op": "if", "cond": {"kind": "screen", "description": "is it ready?"},
         "then": [{"op": "action", "action": "left_click", "x": 1, "y": 1}],
         "else": [{"op": "action", "action": "key", "keys": ["esc"]}]},
    ]}}}
    path = tmp_path / "s.json"
    path.write_text(json.dumps(lib))

    backend = FakeBackend()
    provider = FakeProvider([True])
    rc = run_skill_file(provider, str(path), "main", {}, backend=backend, auto=True)
    assert rc == 0
    assert backend.executed == ["left_click"]  # took the then-branch
    assert provider.checked == ["is it ready?"]


def test_run_skill_file_reports_bad_program(tmp_path, monkeypatch):
    monkeypatch.setattr(screen, "prepare_for_model", lambda raw, size, **kw: (raw, size, 1.0))
    lib = {"skills": {"main": {"body": [{"op": "call", "skill": "ghost"}]}}}
    path = tmp_path / "s.json"
    path.write_text(json.dumps(lib))
    rc = run_skill_file(FakeProvider([]), str(path), "main", {}, backend=FakeBackend(), auto=True)
    assert rc == 2  # SkillError (unknown skill) -> exit 2


def test_run_skill_file_missing_file_returns_2(tmp_path):
    rc = run_skill_file(FakeProvider([]), str(tmp_path / "nope.json"), "main", {}, backend=FakeBackend(), auto=True)
    assert rc == 2
