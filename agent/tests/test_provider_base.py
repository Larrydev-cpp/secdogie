import pytest
from secdogie_agent.providers.base import Action, is_transient, parse_action_json, parse_plan

# -- is_transient: which model-call failures are worth retrying ----------------
# Duck-typed on purpose (no SDK import), so these fakes stand in for whichever
# provider's exceptions actually show up at runtime.

def _exc(name, status=None):
    """An exception class named `name`, optionally carrying a status_code --
    mirroring how both SDKs surface HTTP errors."""
    cls = type(name, (Exception,), {})
    e = cls("boom")
    if status is not None:
        e.status_code = status
    return e


def test_rate_limit_and_overload_are_transient():
    assert is_transient(_exc("RateLimitError", 429)) is True
    assert is_transient(_exc("APIStatusError", 529)) is True   # Anthropic "overloaded"
    assert is_transient(_exc("InternalServerError", 500)) is True
    assert is_transient(_exc("APIStatusError", 503)) is True


def test_auth_and_bad_request_are_fatal_not_retried():
    # Retrying these only burns time -- they need the user to fix something.
    assert is_transient(_exc("AuthenticationError", 401)) is False
    assert is_transient(_exc("PermissionDeniedError", 403)) is False
    assert is_transient(_exc("BadRequestError", 400)) is False
    assert is_transient(_exc("NotFoundError", 404)) is False


def test_status_code_wins_over_the_class_name():
    # A generic wrapper name with a 429 is still a rate limit...
    assert is_transient(_exc("APIStatusError", 429)) is True
    # ...and an alarming-sounding name with a 401 is still fatal.
    assert is_transient(_exc("RateLimitError", 401)) is False


def test_classifies_by_name_when_there_is_no_status_code():
    assert is_transient(_exc("RateLimitError")) is True
    assert is_transient(_exc("APIConnectionError")) is True
    assert is_transient(_exc("APITimeoutError")) is True
    assert is_transient(_exc("AuthenticationError")) is False


def test_socket_level_failures_are_transient():
    assert is_transient(TimeoutError("timed out")) is True
    assert is_transient(ConnectionError("reset by peer")) is True


def test_unknown_errors_fail_fast_rather_than_spin():
    assert is_transient(ValueError("model returned junk")) is False
    assert is_transient(_exc("SomethingWeird")) is False


def test_parse_plan_json_array():
    assert parse_plan('["open menu", "click save"]') == ["open menu", "click save"]


def test_parse_plan_code_fence_with_preamble():
    text = 'Here is the plan:\n```json\n["a", "b", "c"]\n```'
    assert parse_plan(text) == ["a", "b", "c"]


def test_parse_plan_falls_back_to_numbered_lines():
    text = "1. open the file menu\n2. click Save As\n3. type the name"
    assert parse_plan(text) == ["open the file menu", "click Save As", "type the name"]


def test_parse_plan_empty_when_nothing_listlike():
    assert parse_plan("") == []


def test_parse_plan_drops_blank_items():
    assert parse_plan('["a", "", "  ", "b"]') == ["a", "b"]


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
