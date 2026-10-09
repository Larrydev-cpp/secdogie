"""The packets of the browser link (``secdogie/symbiont-link/v2``): what rides
inside each record once the handshake is done.

Every packet is canonical JSON (sorted keys, tight separators, UTF-8 -- the
same bytes the operator page produces), validated field by field before it is
acted on or sent. The spec's own fields are all here; what this module adds
(``action``, ``action_hash``, ``subject`` on a Gate 2 challenge, ``about`` /
``step`` on a Gate 1 probe, and the reply packets) is what the
action-authorization/v1 contract and plain-language wording need.

Lengths are counted in code points, the same in Python and TypeScript. A
validation error names the field and the rule, never the value: values are the
operator's words or text from the screen.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata

from secdogie_identity import canonical

MAX_PACKET_BYTES = 65511  # one record's plaintext (linkcipher.MAX_PLAINTEXT)

ID = re.compile(r"^[a-z0-9-]{1,40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ACTION_TYPE = re.compile(r"^[A-Z_]{1,40}$")
STEP_KIND = re.compile(r"^[a-z_]{1,40}$")

DESTRUCTIVE_UNBIND = "DESTRUCTIVE_UNBIND"
AUTHORIZATION_TYPE = "secdogie/action-authorization/v1"
TOKEN_FIELDS = frozenset({"type", "action_hash", "subject", "valid_from", "expires_at", "signer", "sig"})
CONFIRM_ABOUTS = frozenset({"step", "plan", "continue"})
CONFIRM_IDS = ("approve", "deny")

LIMITS = {
    "intent": 2000,
    "reply_text": 2000,
    "prompt": 500,
    "label": 80,
    "candidates": 8,
    "emotional_memory": 160,
    "target_summary": 200,
    "action.kind": 40,
    "action.target_id": 200,
    "action.target_role": 80,
    "action.target_name": 2000,
    "action.text": 16000,
    "step.keys": 8,
    "step.key": 40,
    "step.text": 2000,
    "step.path": 1000,
    "did": 200,
    "sig": 200,
}

PAGE_TO_NODE = frozenset({"ADD_GOAL", "GATE_1_REPLY", "GATE_2_REPLY", "STOP"})
NODE_TO_PAGE = frozenset({"CURRENT_STATUS", "GOAL_UPDATE", "GATE_1_PROBE", "GATE_2_CHALLENGE", "GATE_CLOSED"})


class PacketError(ValueError):
    """``path`` is where (``"candidates[2].label"``), ``rule`` what was broken."""

    def __init__(self, path: str, rule: str):
        super().__init__(f"{path}: {rule}")
        self.path = path
        self.rule = rule


# ---- field checks ---------------------------------------------------------------------


def _fields(obj, path: str, required: set[str], optional: set[str] = frozenset()) -> None:
    if not isinstance(obj, dict):
        raise PacketError(path, "not an object")
    keys = set(obj)
    missing = required - keys
    if missing:
        raise PacketError(f"{path}.{sorted(missing)[0]}" if path else sorted(missing)[0], "missing")
    extra = keys - required - optional
    if extra:
        raise PacketError(f"{path}.{sorted(extra)[0]}" if path else sorted(extra)[0], "unexpected field")


def _str(v, path: str, *, max_len: int, min_len: int = 0, single_line: bool = False) -> str:
    if not isinstance(v, str):
        raise PacketError(path, "not a string")
    if not min_len <= len(v) <= max_len:
        raise PacketError(path, f"length outside {min_len}..{max_len}")
    if single_line and any(unicodedata.category(c) == "Cc" for c in v):
        raise PacketError(path, "control characters")
    return v


def _id(v, path: str) -> str:
    if not isinstance(v, str) or not ID.match(v):
        raise PacketError(path, "not an id")
    return v


def _enum(v, path: str, allowed) -> str:
    if v not in allowed or not isinstance(v, str):
        raise PacketError(path, "not an allowed value")
    return v


def _number(v, path: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise PacketError(path, "not a number")
    return v


def _bool(v, path: str) -> bool:
    if not isinstance(v, bool):
        raise PacketError(path, "not a boolean")
    return v


def _check_token(tok, path: str) -> None:
    _fields(tok, path, set(TOKEN_FIELDS))
    if tok["type"] != AUTHORIZATION_TYPE:
        raise PacketError(f"{path}.type", "not an action authorization")
    if not isinstance(tok["action_hash"], str) or not HEX64.match(tok["action_hash"]):
        raise PacketError(f"{path}.action_hash", "not a hash")
    _str(tok["subject"], f"{path}.subject", max_len=LIMITS["did"], min_len=1)
    _str(tok["signer"], f"{path}.signer", max_len=LIMITS["did"], min_len=1)
    _str(tok["sig"], f"{path}.sig", max_len=LIMITS["sig"], min_len=1)
    _number(tok["valid_from"], f"{path}.valid_from")
    _number(tok["expires_at"], f"{path}.expires_at")


def _check_add_goal(p) -> None:
    _fields(p, "", {"type", "goal_id", "intent"})
    _id(p["goal_id"], "goal_id")
    _str(p["intent"], "intent", max_len=LIMITS["intent"], min_len=1)


def _check_gate1_reply(p) -> None:
    has_c, has_t = "candidate_id" in p, "text" in p
    if has_c == has_t:
        raise PacketError("candidate_id", "exactly one of candidate_id or text")
    _fields(p, "", {"type", "probe_id", "candidate_id" if has_c else "text"})
    _id(p["probe_id"], "probe_id")
    if has_c:
        _id(p["candidate_id"], "candidate_id")
    else:
        _str(p["text"], "text", max_len=LIMITS["reply_text"], min_len=1)


def _check_gate2_reply(p) -> None:
    decision = p.get("decision")
    if decision == "approve":
        _fields(p, "", {"type", "challenge_id", "decision", "authorization"})
        _check_token(p["authorization"], "authorization")
    elif decision == "cancel":
        _fields(p, "", {"type", "challenge_id", "decision"})
    else:
        raise PacketError("decision", "not an allowed value")
    _id(p["challenge_id"], "challenge_id")


def _check_stop(p) -> None:
    _fields(p, "", {"type", "goal_id"})
    _id(p["goal_id"], "goal_id")


def _check_status(p) -> None:
    _fields(p, "", {"type", "status", "goal_id"})
    status = _enum(p["status"], "status", ("idle", "running", "waiting"))
    if status == "idle" and p["goal_id"] == "":
        return
    _id(p["goal_id"], "goal_id")


def _check_goal_update(p) -> None:
    _fields(p, "", {"type", "goal_id", "state"})
    _id(p["goal_id"], "goal_id")
    _enum(p["state"], "state", ("accepted", "refused", "done", "failed", "stopped", "paused"))


def _check_step(step, path: str) -> None:
    _fields(step, path, {"kind"}, {"keys", "text", "path"})
    if not isinstance(step["kind"], str) or not STEP_KIND.match(step["kind"]):
        raise PacketError(f"{path}.kind", "not a step kind")
    if "keys" in step:
        keys = step["keys"]
        if not isinstance(keys, list) or not 1 <= len(keys) <= LIMITS["step.keys"]:
            raise PacketError(f"{path}.keys", "not a short list")
        for i, k in enumerate(keys):
            _str(k, f"{path}.keys[{i}]", max_len=LIMITS["step.key"], min_len=1, single_line=True)
    if "text" in step:
        _str(step["text"], f"{path}.text", max_len=LIMITS["step.text"])
    if "path" in step:
        _str(step["path"], f"{path}.path", max_len=LIMITS["step.path"], min_len=1)


def _check_probe(p) -> None:
    _fields(p, "", {"type", "probe_id", "prompt", "candidates", "about", "expires_at"}, {"step", "goal_id"})
    _id(p["probe_id"], "probe_id")
    _str(p["prompt"], "prompt", max_len=LIMITS["prompt"])
    about = _enum(p["about"], "about", ("step", "plan", "continue", "question", "other"))
    _number(p["expires_at"], "expires_at")
    if "goal_id" in p:
        _id(p["goal_id"], "goal_id")
    if "step" in p:
        if about != "step":
            raise PacketError("step", "only for a step")
        _check_step(p["step"], "step")
    cands = p["candidates"]
    if not isinstance(cands, list) or len(cands) > LIMITS["candidates"]:
        raise PacketError("candidates", "not a short list")
    seen = []
    for i, c in enumerate(cands):
        _fields(c, f"candidates[{i}]", {"id", "label"})
        _id(c["id"], f"candidates[{i}].id")
        _str(c["label"], f"candidates[{i}].label", max_len=LIMITS["label"], min_len=1, single_line=True)
        if c["id"] in seen:
            raise PacketError(f"candidates[{i}].id", "repeated")
        seen.append(c["id"])
    if about in CONFIRM_ABOUTS and tuple(seen) != CONFIRM_IDS:
        raise PacketError("candidates", "a confirmation offers approve and deny")


def _check_action(a, path: str) -> None:
    _fields(a, path, {"kind", "target_id", "target_role", "target_name", "text", "high_risk"})
    for k in ("kind", "target_id", "target_role", "target_name", "text"):
        _str(a[k], f"{path}.{k}", max_len=LIMITS[f"action.{k}"], min_len=1 if k == "kind" else 0)
    _bool(a["high_risk"], f"{path}.high_risk")


def _check_challenge(p) -> None:
    _fields(p, "", {"type", "challenge_id", "emotional_memory", "target_summary", "action_type", "action",
                    "action_hash", "subject", "expires_at"}, {"goal_id"})
    _id(p["challenge_id"], "challenge_id")
    _str(p["emotional_memory"], "emotional_memory", max_len=LIMITS["emotional_memory"], min_len=1,
         single_line=True)
    _str(p["target_summary"], "target_summary", max_len=LIMITS["target_summary"])
    if not isinstance(p["action_type"], str) or not ACTION_TYPE.match(p["action_type"]):
        raise PacketError("action_type", "not an action type")
    _check_action(p["action"], "action")
    if not isinstance(p["action_hash"], str) or not HEX64.match(p["action_hash"]):
        raise PacketError("action_hash", "not a hash")
    _str(p["subject"], "subject", max_len=LIMITS["did"], min_len=1)
    _number(p["expires_at"], "expires_at")
    if "goal_id" in p:
        _id(p["goal_id"], "goal_id")


def _check_closed(p) -> None:
    _fields(p, "", {"type", "kind", "id", "reason"})
    _enum(p["kind"], "kind", ("probe", "challenge"))
    _id(p["id"], "id")
    _enum(p["reason"], "reason", ("answered", "expired"))


_CHECKS = {
    "ADD_GOAL": _check_add_goal,
    "GATE_1_REPLY": _check_gate1_reply,
    "GATE_2_REPLY": _check_gate2_reply,
    "STOP": _check_stop,
    "CURRENT_STATUS": _check_status,
    "GOAL_UPDATE": _check_goal_update,
    "GATE_1_PROBE": _check_probe,
    "GATE_2_CHALLENGE": _check_challenge,
    "GATE_CLOSED": _check_closed,
}


def validate(packet, *, direction: str | None = None) -> dict:
    """Raise PacketError unless ``packet`` is a well-formed packet (of the
    given ``direction``: "to-node" or "to-page")."""
    if not isinstance(packet, dict):
        raise PacketError("", "not an object")
    t = packet.get("type")
    if not isinstance(t, str) or t not in _CHECKS:
        raise PacketError("type", "unknown packet type")
    if direction == "to-node" and t not in PAGE_TO_NODE or direction == "to-page" and t not in NODE_TO_PAGE:
        raise PacketError("type", "wrong direction")
    _CHECKS[t](packet)
    return packet


def encode(packet: dict, *, direction: str | None = None) -> bytes:
    """Validate, then canonical bytes. Raises PacketError (a packet this side
    would refuse is never sent)."""
    validate(packet, direction=direction)
    data = canonical(packet)
    if len(data) > MAX_PACKET_BYTES:
        raise PacketError("", "too large")
    return data


def _no_duplicates(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise PacketError(k, "duplicate key")
        out[k] = v
    return out


def _no_constants(name):
    raise PacketError("", "not JSON")


def decode(data: bytes, *, direction: str | None = None) -> dict:
    """Parse and validate one record's plaintext. It must be exactly the
    canonical encoding of what it parses to: no duplicate keys, no NaN, no
    other spelling of the same packet."""
    if not isinstance(data, (bytes, bytearray)) or len(data) > MAX_PACKET_BYTES:
        raise PacketError("", "too large")
    try:
        text = bytes(data).decode("utf-8")
        obj = json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constants)
    except PacketError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError) as e:
        raise PacketError("", "not JSON") from e
    validate(obj, direction=direction)
    try:
        same = canonical(obj) == bytes(data)
    except (UnicodeEncodeError, ValueError):
        same = False
    if not same:
        raise PacketError("", "not canonical")
    return obj


__all__ = ["PacketError", "validate", "encode", "decode", "LIMITS", "MAX_PACKET_BYTES", "PAGE_TO_NODE",
           "NODE_TO_PAGE", "DESTRUCTIVE_UNBIND", "AUTHORIZATION_TYPE", "TOKEN_FIELDS", "CONFIRM_IDS",
           "CONFIRM_ABOUTS", "ID"]
