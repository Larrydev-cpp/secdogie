"""The wire contract between a fleet coordinator and its nodes.

One node runs inside each isolated desktop -- a Windows VM or its own user
session -- where it owns that desktop's mouse, keyboard and foreground window
outright. The coordinator, on the host, hands out tasks and collects status.

Two directions, newline-delimited JSON over TCP (the same shape gta/protocol.py
uses, for the same reason: the whole contract is pure data + codec, so it is
unit-testable and both ends only have to move bytes).

  node -> coordinator:
    `hello`   register: who I am, my screen size, what I can do
    `status`  progress on the current assignment (running/step/detail)
    `result`  the assignment finished: exit code + summary

  coordinator -> node:
    `assign`  run this task, with the agent loop flags to run it under
    `stop`    abandon the current assignment

Screenshots never cross this wire. Each node captures, calls the model, and acts
entirely on its own machine, and only text comes back -- so adding nodes costs
the host nothing in bandwidth. What it does multiply is API quota; see
fleet/README.md.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# Message kinds, split by direction so a receiver can reject one that arrived on
# the wrong side of the connection rather than acting on it.
NODE_KINDS = frozenset({"hello", "status", "result"})
COORDINATOR_KINDS = frozenset({"assign", "stop"})

# Assignment states a node reports. Terminal states arrive via `result`.
NODE_STATES = frozenset({"idle", "running", "done", "failed", "stopped"})


class ProtocolError(ValueError):
    """A malformed message off the socket -- unknown kind, missing or
    wrong-typed fields. Raised instead of crashing the receive loop."""


@dataclass(frozen=True)
class Hello:
    """A node announcing itself. `node_id` is stable across reconnects so the
    coordinator can recognize a node that dropped and came back."""

    node_id: str
    label: str = ""  # human-readable, for the operator ("win11-vm-2")
    screen: tuple[int, int] | None = None  # (width, height) of that desktop
    capabilities: tuple[str, ...] = ()  # e.g. ("desktop-ax",) -- what this node's OS/libs support


@dataclass(frozen=True)
class Status:
    """Progress on the current assignment."""

    node_id: str
    state: str  # one of NODE_STATES
    task_id: str | None = None
    step: int = 0
    detail: str = ""


@dataclass(frozen=True)
class Result:
    """An assignment reached a terminal state. `code` is the agent loop's exit
    code (0 done, 1 provider error, 2 declined, 3 steps exhausted, ...)."""

    node_id: str
    task_id: str
    code: int
    summary: str = ""


@dataclass(frozen=True)
class Assign:
    """Run `task` on this node. `options` carries the agent loop flags (auto,
    dry_run, max_steps, desktop_ax, window, ...) -- kept as an open dict so
    adding a loop flag doesn't require a protocol change; the node validates
    them against AgentConfig, which is the real schema."""

    task_id: str
    task: str
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Stop:
    """Abandon the current assignment on this node."""

    task_id: str | None = None  # None = whatever is running


Message = Hello | Status | Result | Assign | Stop


# -- encoding -----------------------------------------------------------------


def to_json(msg: Message) -> str:
    """Encode one message as a single line (no embedded newline), ready to be
    written with a trailing '\\n'."""
    if isinstance(msg, Hello):
        payload: dict[str, Any] = {"kind": "hello", "node_id": msg.node_id}
        if msg.label:
            payload["label"] = msg.label
        if msg.screen is not None:
            payload["screen"] = [msg.screen[0], msg.screen[1]]
        if msg.capabilities:
            payload["capabilities"] = list(msg.capabilities)
    elif isinstance(msg, Status):
        payload = {"kind": "status", "node_id": msg.node_id, "state": msg.state, "step": msg.step}
        if msg.task_id is not None:
            payload["task_id"] = msg.task_id
        if msg.detail:
            payload["detail"] = msg.detail
    elif isinstance(msg, Result):
        payload = {"kind": "result", "node_id": msg.node_id, "task_id": msg.task_id, "code": msg.code}
        if msg.summary:
            payload["summary"] = msg.summary
    elif isinstance(msg, Assign):
        payload = {"kind": "assign", "task_id": msg.task_id, "task": msg.task}
        if msg.options:
            payload["options"] = msg.options
    elif isinstance(msg, Stop):
        payload = {"kind": "stop"}
        if msg.task_id is not None:
            payload["task_id"] = msg.task_id
    else:
        raise ProtocolError(f"cannot encode {type(msg).__name__}")
    return json.dumps(payload, separators=(",", ":"))


# -- decoding -----------------------------------------------------------------


def _obj(text: str) -> dict:
    try:
        d = json.loads(text)
    except json.JSONDecodeError as e:
        raise ProtocolError(f"message is not valid JSON: {e}") from e
    if not isinstance(d, dict):
        raise ProtocolError("message must be a JSON object")
    return d


def _str(d: dict, key: str, *, required: bool = True, default: str = "") -> str:
    if key not in d:
        if required:
            raise ProtocolError(f"message missing required field {key!r}")
        return default
    v = d[key]
    if not isinstance(v, str):
        raise ProtocolError(f"field {key!r} must be a string, got {v!r}")
    return v


def _int(d: dict, key: str, *, required: bool = True, default: int = 0) -> int:
    if key not in d:
        if required:
            raise ProtocolError(f"message missing required field {key!r}")
        return default
    v = d[key]
    # bool is an int subclass; a stray True here is a bug, not a number.
    if isinstance(v, bool) or not isinstance(v, int):
        raise ProtocolError(f"field {key!r} must be an integer, got {v!r}")
    return v


def from_json(text: str, *, expect: frozenset[str] | None = None) -> Message:
    """Decode one line into a message. `expect` restricts which kinds are
    allowed -- pass NODE_KINDS on the coordinator and COORDINATOR_KINDS on the
    node, so a message arriving on the wrong side is rejected rather than acted
    on."""
    d = _obj(text)
    kind = _str(d, "kind")
    if expect is not None and kind not in expect:
        raise ProtocolError(f"unexpected message kind {kind!r} on this connection")

    if kind == "hello":
        screen = d.get("screen")
        size: tuple[int, int] | None = None
        if screen is not None:
            if not (isinstance(screen, (list, tuple)) and len(screen) == 2):
                raise ProtocolError("screen must be a [width, height] pair or null")
            if any(isinstance(v, bool) or not isinstance(v, int) for v in screen):
                raise ProtocolError("screen dimensions must be integers")
            size = (int(screen[0]), int(screen[1]))
        caps = d.get("capabilities", [])
        if not isinstance(caps, (list, tuple)) or any(not isinstance(c, str) for c in caps):
            raise ProtocolError("capabilities must be a list of strings")
        return Hello(
            node_id=_str(d, "node_id"),
            label=_str(d, "label", required=False),
            screen=size,
            capabilities=tuple(caps),
        )

    if kind == "status":
        state = _str(d, "state")
        if state not in NODE_STATES:
            raise ProtocolError(f"unknown node state {state!r}")
        task_id = d.get("task_id")
        if task_id is not None and not isinstance(task_id, str):
            raise ProtocolError("task_id must be a string or null")
        return Status(
            node_id=_str(d, "node_id"),
            state=state,
            task_id=task_id,
            step=_int(d, "step", required=False),
            detail=_str(d, "detail", required=False),
        )

    if kind == "result":
        return Result(
            node_id=_str(d, "node_id"),
            task_id=_str(d, "task_id"),
            code=_int(d, "code"),
            summary=_str(d, "summary", required=False),
        )

    if kind == "assign":
        options = d.get("options", {})
        if not isinstance(options, dict):
            raise ProtocolError("options must be a JSON object")
        return Assign(task_id=_str(d, "task_id"), task=_str(d, "task"), options=options)

    if kind == "stop":
        task_id = d.get("task_id")
        if task_id is not None and not isinstance(task_id, str):
            raise ProtocolError("task_id must be a string or null")
        return Stop(task_id=task_id)

    raise ProtocolError(f"unknown message kind {kind!r}")
