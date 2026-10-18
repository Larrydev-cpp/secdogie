"""The messages that cross the core <-> node link, as canonical JSON.

This formalizes what the coordinator and a node actually exchange on the wire
(the transport itself -- WebRTC data channel in production -- is elsewhere):

  core -> node:  DISPATCH (a signed task, optionally resuming from a checkpoint),
                 PROBE (liveness ping), CANCEL (stop; work was migrated away).
  node -> core:  RESULT (a signed result), HEARTBEAT (unsolicited liveness),
                 PONG (answer to a PROBE), CHECKPOINT (public partial progress).

Everything a node receives is public: a DISPATCH carries only a public task and
a public checkpoint, never a credential. `decode` is strict -- it refuses a
wrong protocol, an unknown type, NaN/Infinity, and duplicate keys.
"""

from __future__ import annotations

import json
from typing import Any

from secdogie_identity import canonical

WIRE_PROTOCOL = "secdogie/swarm-wire/v1"

DISPATCH = "DISPATCH"
RESULT = "RESULT"
HEARTBEAT = "HEARTBEAT"
PROBE = "PROBE"
PONG = "PONG"
CHECKPOINT = "CHECKPOINT"
CANCEL = "CANCEL"

_REQUIRED: dict[str, tuple[str, ...]] = {
    DISPATCH: ("task",),  # resume_from is optional
    RESULT: ("result",),
    HEARTBEAT: ("node_did",),
    PROBE: ("nonce", "sent_at_ms"),
    PONG: ("node_did", "nonce"),
    CHECKPOINT: ("task_id", "node_did", "checkpoint"),
    CANCEL: ("task_id", "reason"),
}


class WireError(ValueError):
    pass


def _msg(type_: str, **fields: Any) -> dict[str, Any]:
    return {"v": WIRE_PROTOCOL, "type": type_, **fields}


def dispatch(signed_task: dict[str, Any], resume_from: dict[str, Any] | None = None) -> dict[str, Any]:
    m = _msg(DISPATCH, task=signed_task)
    if resume_from is not None:
        m["resume_from"] = resume_from
    return m


def result(signed_result: dict[str, Any]) -> dict[str, Any]:
    return _msg(RESULT, result=signed_result)


def heartbeat(node_did: str) -> dict[str, Any]:
    return _msg(HEARTBEAT, node_did=node_did)


def probe(nonce: str, sent_at_ms: int) -> dict[str, Any]:
    return _msg(PROBE, nonce=nonce, sent_at_ms=sent_at_ms)


def pong(node_did: str, nonce: str) -> dict[str, Any]:
    return _msg(PONG, node_did=node_did, nonce=nonce)


def checkpoint(task_id: str, node_did: str, blob: dict[str, Any]) -> dict[str, Any]:
    return _msg(CHECKPOINT, task_id=task_id, node_did=node_did, checkpoint=blob)


def cancel(task_id: str, reason: str) -> dict[str, Any]:
    return _msg(CANCEL, task_id=task_id, reason=reason)


def validate(msg: Any) -> None:
    if not isinstance(msg, dict):
        raise WireError("message must be an object")
    if msg.get("v") != WIRE_PROTOCOL:
        raise WireError("wrong or missing wire protocol")
    t = msg.get("type")
    if t not in _REQUIRED:
        raise WireError(f"unknown message type {t!r}")
    for field in _REQUIRED[t]:
        if field not in msg:
            raise WireError(f"{t} missing {field}")


def encode(msg: dict[str, Any]) -> bytes:
    validate(msg)
    return canonical(msg)


def _no_dupes(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for k, v in pairs:
        if k in seen:
            raise WireError(f"duplicate key {k!r}")
        seen[k] = v
    return seen


def _no_nan(x: str) -> Any:
    raise WireError(f"{x} is not allowed")


def decode(data: bytes | str) -> dict[str, Any]:
    try:
        msg = json.loads(data, object_pairs_hook=_no_dupes, parse_constant=_no_nan)
    except WireError:
        raise
    except (ValueError, TypeError) as e:
        raise WireError(f"not valid JSON: {e}") from e
    validate(msg)
    return msg
