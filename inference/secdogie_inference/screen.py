"""Deterministic screens over model output -- what the safety layer can prove
from an answer alone, with no model of its own in the loop.

Two questions, both answerable from the signed response:

  * **Did lease material leak?** ``canary_leaked`` looks for ``sdgc_`` tokens
    and hashes each against the lease's ``canary_hash``.
  * **Does the answer propose acting beyond its grant?** In secdogie a model's
    answer *is* an action proposal when the request carried capability scopes
    (the agent parses the JSON object out of the text and acts on it). So when
    a request is action-bearing, every JSON object in the answer is read the way
    an executor would read it. ``forbidden`` finds proposals no grant can ever
    cover -- the capabilities the project refuses outright (elevated launch,
    memory write, thread injection, kernel HID, disabling the gates or the
    human in the loop) -- and ``overreach`` finds proposals of grantable scopes
    the request did not grant, or of actions with no known scope.

When the request has no scopes nothing acts on the answer, so JSON in it is
content, not a proposal, and neither proposal screen applies: explaining what a
``run_elevated`` action looks like is not mutiny.
"""
from __future__ import annotations

import hashlib
import json
import re

from .lease import CANARY

# The agent's action vocabulary (agent/providers/base.py) and the plan gate's
# (citadel/action_gate.py), mapped to the capability scope each needs.
SCOPE_FOR = {
    "left_click": "physical.click", "right_click": "physical.click", "double_click": "physical.click",
    "click_element": "physical.click", "track_click": "physical.click", "move": "physical.click",
    "click": "physical.click", "press": "physical.click",
    "type": "physical.type",
    "key": "physical.key", "hold_key": "physical.key",
    "scroll": "physical.scroll",
    "drag": "physical.drag",
    "open": "system.open",
    "run": "process.run",
    "post": "network.post", "publish": "network.post", "submit": "network.post", "comment": "network.post",
    "send": "network.send", "reply": "network.send",
    "read": "observe.read", "observe": "observe.read", "look": "observe.read", "screenshot": "observe.read",
}
# Steps that change nothing outside the conversation.
BENIGN_ACTIONS = frozenset({"done", "ask_user", "wait", "remember"})
# Never grantable, whoever asks: the project's red lines, as actions and scopes.
FORBIDDEN_ACTIONS = frozenset({
    "run_elevated", "write_memory", "inject_thread", "kernel_hid", "disable_gate", "bypass_gate",
    "disable_hitl", "auto_approve", "install_persistence", "evade_detection",
})
FORBIDDEN_SCOPES = frozenset({
    "process.run_elevated", "process.memory.write", "process.thread.inject", "kernel.hid",
    "privilege.escalate", "gate.bypass", "hitl.disable", "persistence.install", "edr.evade",
})

MAX_SCAN = 256      # '{' / '[' positions tried per answer
MAX_OBJECTS = 128   # JSON objects collected per answer
_OPEN = re.compile(r"[\[{]")


def canary_leaked(output: str, lease_canary_hash: str) -> bool:
    """Whether ``output`` contains the canary the lease commits to."""
    for token in CANARY.findall(output):
        if hashlib.sha256(token.encode("ascii")).hexdigest() == lease_canary_hash:
            return True
    return False


def json_objects(text: str) -> list[dict]:
    """Every JSON object in ``text`` -- bare, fenced, or after a preamble -- and
    the objects nested inside them (a plan is a list of actions). Bounded."""
    decoder = json.JSONDecoder()
    found: list[dict] = []

    def collect(value, depth: int) -> None:
        if depth > 8 or len(found) >= MAX_OBJECTS:
            return
        if isinstance(value, dict):
            found.append(value)
            for v in value.values():
                collect(v, depth + 1)
        elif isinstance(value, list):
            for v in value:
                collect(v, depth + 1)

    pos = 0
    for n, m in enumerate(_OPEN.finditer(text)):
        if n >= MAX_SCAN:
            break
        if m.start() < pos:
            continue
        try:
            value, end = decoder.raw_decode(text, m.start())
        except ValueError:
            continue
        collect(value, 0)
        pos = end
    return found


def _named_scopes(obj: dict) -> list[str]:
    out = [obj[k] for k in ("scope", "capability") if isinstance(obj.get(k), str)]
    for k in ("scopes", "capabilities"):
        if isinstance(obj.get(k), list):
            out.extend(s for s in obj[k] if isinstance(s, str))
    return out


def _action(obj: dict) -> str | None:
    a = obj.get("action")
    return a.strip().lower() if isinstance(a, str) else None


def forbidden(output: str) -> list[str]:
    """Proposals no grant can cover. Each entry says what was proposed."""
    out = []
    for obj in json_objects(output):
        action = _action(obj)
        if action in FORBIDDEN_ACTIONS:
            out.append(f"action {action}")
        out.extend(f"scope {s}" for s in _named_scopes(obj) if s in FORBIDDEN_SCOPES)
    return out


def overreach(output: str, granted) -> list[str]:
    """Proposals beyond ``granted`` that are not forbidden outright: a grantable
    scope the request did not grant, or an action with no known scope."""
    granted = set(granted)
    out = []
    for obj in json_objects(output):
        action = _action(obj)
        if action is not None and action not in BENIGN_ACTIONS and action not in FORBIDDEN_ACTIONS:
            scope = SCOPE_FOR.get(action)
            if scope is None:
                out.append(f"action {action} has no capability scope")
            elif scope not in granted:
                out.append(f"action {action} needs {scope}")
        out.extend(f"scope {s} not granted" for s in _named_scopes(obj)
                   if s not in granted and s not in FORBIDDEN_SCOPES)
    return out


__all__ = ["SCOPE_FOR", "BENIGN_ACTIONS", "FORBIDDEN_ACTIONS", "FORBIDDEN_SCOPES", "canary_leaked",
           "json_objects", "forbidden", "overreach"]
