"""The wire contract between secdogie (Python) and a ScriptHookV plugin (C++).

secdogie can't read GTA's memory or call its natives; a small ScriptHookV `.asi`
plugin does that on the machine and speaks to secdogie over a local socket. This
module is just the message shapes both sides agree on -- pure data + JSON codec,
no I/O -- so the whole contract is unit-testable and the bridge (bridge.py) and
the C++ plugin only have to move bytes.

Two directions:
  - plugin -> secdogie: a `GameState` snapshot (player pose, speed, health, the
    map waypoint) each tick.
  - secdogie -> plugin: a `Command`. Either low-level `drive_control` (steer +
    throttle, which driving.py's control law produces and the plugin maps to the
    game's input), or a high-level `task` that hands the job to GTA's own AI
    natives (e.g. TASK_VEHICLE_DRIVE_TO_COORD) -- the plugin just forwards it.

Single-player only. ScriptHookV disables itself in GTA Online, and automating
Online is bannable and harms other players; nothing here should be pointed at it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal


class ProtocolError(ValueError):
    """A malformed message off the socket -- missing/typed-wrong fields."""


@dataclass(frozen=True)
class GameState:
    """One snapshot from the plugin. `heading` and any bearing computed from
    positions must share a frame (see driving.py) -- the plugin converts GTA's
    heading convention into that frame before sending."""

    x: float
    y: float
    heading: float  # degrees
    speed: float = 0.0  # m/s
    health: float = 1.0  # 0..1
    in_vehicle: bool = False
    waypoint: tuple[float, float] | None = None  # the map marker, if the player set one


@dataclass(frozen=True)
class Command:
    kind: Literal["drive_control", "stop", "task"]
    steer: float = 0.0  # -1..1; +1 turns toward increasing heading (plugin maps to left/right)
    throttle: float = 0.0  # 0..1
    task: str | None = None  # a GTA native task name for kind == "task"
    args: dict = field(default_factory=dict)


def drive_control(steer: float, throttle: float) -> Command:
    return Command(kind="drive_control", steer=steer, throttle=throttle)


def stop() -> Command:
    return Command(kind="stop")


def task(name: str, **args) -> Command:
    return Command(kind="task", task=name, args=args)


def _num(d: dict, key: str, *, required: bool = True, default: float = 0.0) -> float:
    if key not in d:
        if required:
            raise ProtocolError(f"game state missing required field {key!r}")
        return default
    v = d[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ProtocolError(f"game state field {key!r} must be a number, got {v!r}")
    return float(v)


def state_from_json(text: str) -> GameState:
    try:
        d = json.loads(text)
    except json.JSONDecodeError as e:
        raise ProtocolError(f"game state is not valid JSON: {e}") from e
    if not isinstance(d, dict):
        raise ProtocolError("game state must be a JSON object")

    wp = d.get("waypoint")
    waypoint: tuple[float, float] | None = None
    if wp is not None:
        if not (isinstance(wp, (list, tuple)) and len(wp) == 2):
            raise ProtocolError("waypoint must be a [x, y] pair or null")
        waypoint = (float(wp[0]), float(wp[1]))

    return GameState(
        x=_num(d, "x"),
        y=_num(d, "y"),
        heading=_num(d, "heading"),
        speed=_num(d, "speed", required=False),
        health=_num(d, "health", required=False, default=1.0),
        in_vehicle=bool(d.get("in_vehicle", False)),
        waypoint=waypoint,
    )


def command_to_json(cmd: Command) -> str:
    payload: dict = {"kind": cmd.kind}
    if cmd.kind == "drive_control":
        payload["steer"] = round(cmd.steer, 4)
        payload["throttle"] = round(cmd.throttle, 4)
    elif cmd.kind == "task":
        payload["task"] = cmd.task
        payload["args"] = cmd.args
    # "stop" carries nothing else.
    return json.dumps(payload, separators=(",", ":"))
