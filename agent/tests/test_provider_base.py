import pytest

from secdogie_agent.providers.base import Action, parse_action_json


def test_parse_action_json_bare():
    assert parse_action_json('{"action": "wait", "seconds": 1}') == {"action": "wait", "seconds": 1}


def test_parse_action_json_code_fence():
    text = '```json\n{"action": "left_click", "x": 10, "y": 20}\n```'
    assert parse_action_json(text) == {"action": "left_click", "x": 10, "y": 20}


def test_parse_action_json_with_preamble():
    text = 'Sure, here is the action:\n{"action": "done", "text": "finished"}\nHope that helps.'
    assert parse_action_json(text) == {"action": "done", "text": "finished"}


def test_parse_action_json_no_json_raises():
    with pytest.raises(ValueError):
        parse_action_json("no json here at all")


def test_action_from_dict_rejects_unknown_kind():
    with pytest.raises(ValueError):
        Action.from_dict({"action": "format_hard_drive"})


def test_action_from_dict_roundtrip():
    d = {"action": "left_click", "x": 5, "y": 7, "reasoning": "click the button"}
    a = Action.from_dict(d)
    assert a.kind == "left_click"
    assert a.x == 5 and a.y == 7
    assert a.reasoning == "click the button"
    assert a.raw == d
