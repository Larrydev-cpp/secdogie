"""Tests for the fleet wire contract: every message round-trips, and malformed
input raises ProtocolError instead of crashing a receive loop."""
import pytest
from secdogie_fleet import protocol as p


def _round(msg, expect=None):
    return p.from_json(p.to_json(msg), expect=expect)


# -- round trips ---------------------------------------------------------------

def test_hello_round_trips_with_everything():
    msg = p.Hello(node_id="n1", label="win11-vm-2", screen=(1920, 1080), capabilities=("desktop-ax",))
    assert _round(msg) == msg


def test_hello_round_trips_minimal():
    msg = p.Hello(node_id="n1")
    got = _round(msg)
    assert got == msg
    assert got.screen is None and got.capabilities == ()


def test_status_and_result_round_trip():
    s = p.Status(node_id="n1", state="running", task_id="t7", step=12, detail="clicked Save")
    assert _round(s) == s
    r = p.Result(node_id="n1", task_id="t7", code=0, summary="all done")
    assert _round(r) == r


def test_assign_and_stop_round_trip():
    a = p.Assign(task_id="t7", task="open notepad", options={"auto": True, "max_steps": 40})
    assert _round(a) == a
    assert _round(p.Stop()) == p.Stop()
    assert _round(p.Stop(task_id="t7")) == p.Stop(task_id="t7")


def test_encoding_is_a_single_line():
    # The transport is newline-delimited, so no message may contain a newline.
    line = p.to_json(p.Status(node_id="n1", state="running", detail="line one\nline two"))
    assert "\n" not in line
    assert p.from_json(line).detail == "line one\nline two"  # ...but it survives the trip


# -- direction gating ----------------------------------------------------------

def test_expect_rejects_a_message_from_the_wrong_side():
    # A coordinator only ever accepts node messages, and vice versa.
    assign = p.to_json(p.Assign(task_id="t1", task="x"))
    with pytest.raises(p.ProtocolError, match="unexpected message kind"):
        p.from_json(assign, expect=p.NODE_KINDS)

    hello = p.to_json(p.Hello(node_id="n1"))
    with pytest.raises(p.ProtocolError, match="unexpected message kind"):
        p.from_json(hello, expect=p.COORDINATOR_KINDS)

    # ...and accepts them on the correct side.
    assert isinstance(p.from_json(hello, expect=p.NODE_KINDS), p.Hello)
    assert isinstance(p.from_json(assign, expect=p.COORDINATOR_KINDS), p.Assign)


# -- malformed input -----------------------------------------------------------

def test_bad_json_and_non_objects_raise():
    with pytest.raises(p.ProtocolError, match="not valid JSON"):
        p.from_json("{not json")
    with pytest.raises(p.ProtocolError, match="must be a JSON object"):
        p.from_json("[1, 2, 3]")


def test_unknown_kind_raises():
    with pytest.raises(p.ProtocolError, match="unknown message kind"):
        p.from_json('{"kind": "launch_missiles"}')


def test_missing_and_wrong_typed_fields_raise():
    with pytest.raises(p.ProtocolError, match="missing required field 'node_id'"):
        p.from_json('{"kind": "hello"}')
    with pytest.raises(p.ProtocolError, match="must be a string"):
        p.from_json('{"kind": "hello", "node_id": 42}')
    with pytest.raises(p.ProtocolError, match="must be an integer"):
        p.from_json('{"kind": "result", "node_id": "n", "task_id": "t", "code": "zero"}')


def test_booleans_are_not_accepted_as_integers():
    # bool is an int subclass in Python; a stray True in a numeric field is a bug.
    with pytest.raises(p.ProtocolError, match="must be an integer"):
        p.from_json('{"kind": "result", "node_id": "n", "task_id": "t", "code": true}')


def test_unknown_state_is_rejected():
    with pytest.raises(p.ProtocolError, match="unknown node state"):
        p.from_json('{"kind": "status", "node_id": "n", "state": "vibing"}')


def test_malformed_screen_and_capabilities_are_rejected():
    with pytest.raises(p.ProtocolError, match="screen must be"):
        p.from_json('{"kind": "hello", "node_id": "n", "screen": [1920]}')
    with pytest.raises(p.ProtocolError, match="screen dimensions must be integers"):
        p.from_json('{"kind": "hello", "node_id": "n", "screen": ["a", "b"]}')
    with pytest.raises(p.ProtocolError, match="capabilities must be"):
        p.from_json('{"kind": "hello", "node_id": "n", "capabilities": "desktop-ax"}')


def test_options_must_be_an_object():
    with pytest.raises(p.ProtocolError, match="options must be"):
        p.from_json('{"kind": "assign", "task_id": "t", "task": "x", "options": []}')
