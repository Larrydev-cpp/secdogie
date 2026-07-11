"""Wire-protocol codec tests -- pure, no socket."""
import json

import pytest
from secdogie_gta.protocol import (
    Command,
    ProtocolError,
    command_to_json,
    drive_control,
    state_from_json,
    stop,
    task,
)


def test_state_from_json_parses_all_fields():
    s = state_from_json(json.dumps({
        "x": 1.5, "y": -2.0, "heading": 90.0, "speed": 12.0,
        "health": 0.8, "in_vehicle": True, "waypoint": [100.0, 200.0],
    }))
    assert (s.x, s.y, s.heading, s.speed) == (1.5, -2.0, 90.0, 12.0)
    assert s.health == 0.8 and s.in_vehicle is True and s.waypoint == (100.0, 200.0)


def test_state_defaults_when_optional_fields_absent():
    s = state_from_json(json.dumps({"x": 0, "y": 0, "heading": 0}))
    assert s.speed == 0.0 and s.health == 1.0 and s.in_vehicle is False and s.waypoint is None


def test_state_missing_required_field_errors():
    with pytest.raises(ProtocolError, match="missing required field 'heading'"):
        state_from_json(json.dumps({"x": 0, "y": 0}))


def test_state_wrong_type_errors():
    with pytest.raises(ProtocolError, match="must be a number"):
        state_from_json(json.dumps({"x": "nope", "y": 0, "heading": 0}))


def test_state_bad_waypoint_errors():
    with pytest.raises(ProtocolError, match="waypoint"):
        state_from_json(json.dumps({"x": 0, "y": 0, "heading": 0, "waypoint": [1]}))


def test_state_invalid_json_errors():
    with pytest.raises(ProtocolError, match="not valid JSON"):
        state_from_json("{not json")


def test_command_constructors_and_encoding():
    assert command_to_json(stop()) == '{"kind":"stop"}'

    dc = json.loads(command_to_json(drive_control(0.512345, 0.9)))
    assert dc["kind"] == "drive_control" and dc["steer"] == 0.5123 and dc["throttle"] == 0.9

    tk = json.loads(command_to_json(task("TASK_VEHICLE_DRIVE_TO_COORD", x=1, y=2, speed=20)))
    assert tk["kind"] == "task" and tk["task"] == "TASK_VEHICLE_DRIVE_TO_COORD"
    assert tk["args"] == {"x": 1, "y": 2, "speed": 20}


def test_drive_control_command_shape():
    c = drive_control(0.1, 0.2)
    assert isinstance(c, Command) and c.kind == "drive_control"
