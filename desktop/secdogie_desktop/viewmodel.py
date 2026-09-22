"""Pure presentation logic for the desktop window -- no tkinter here.

Turns a fleet snapshot (from ConsoleController.state_snapshot) into the rows the
window renders, decides which actions a task offers, and prepares a command body
(signing it when an operator identity is supplied). Kept tk-free so it is unit
tested headlessly; app.py is the thin view that calls these.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Which control actions apply to a task in a given state.
_ACTIONS_BY_STATE = {
    "running": ("stop",),
    "queued": ("stop",),
    "paused": ("resume",),
    "done": (),
    "failed": (),
    "stopped": (),
}


def available_actions(state: str) -> tuple[str, ...]:
    return _ACTIONS_BY_STATE.get(state, ())


def node_rows(snapshot: dict) -> list[tuple[str, str, str, str]]:
    """(node_id, label, capabilities, current task) per connected node."""
    rows = []
    for n in snapshot.get("nodes", []):
        rows.append(
            (
                str(n.get("node_id", "")),
                str(n.get("label", "") or ""),
                ", ".join(n.get("capabilities") or []),
                str(n.get("task_id") or "idle"),
            )
        )
    return rows


def task_rows(snapshot: dict) -> list[dict]:
    """One dict per task, with the actions it currently offers."""
    rows = []
    for t in snapshot.get("tasks", []):
        state = str(t.get("state", ""))
        rows.append(
            {
                "task_id": str(t.get("task_id", "")),
                "state": state,
                "task": str(t.get("task", "")),
                "node_id": str(t.get("node_id") or ""),
                "detail": str(t.get("detail", "") or ""),
                "actions": available_actions(state),
            }
        )
    return rows


def status_line(snapshot: dict) -> str:
    nodes = snapshot.get("nodes", [])
    tasks = snapshot.get("tasks", [])
    running = sum(1 for t in tasks if t.get("state") == "running")
    gate = "signed commands" if snapshot.get("requires_signature") else "loopback"
    return f"{len(nodes)} node(s) · {len(tasks)} task(s) · {running} running · {gate}"


# --- Chat transcript (the ChatGPT-style view) -------------------------------
#
# The window is a conversation: the operator types a task ("user" message) and
# the fleet answers with what happened to it ("system" messages). app.py holds
# the last snapshot and the running transcript; the diffing that turns one
# snapshot into new chat lines is pure and lives here, so it is unit-tested with
# no display.


@dataclass(frozen=True)
class ChatMessage:
    role: str  # "operator" | "system"
    text: str
    kind: str = "info"  # info | task | status | result | error | node


# How a task state maps to a message tone (drives the bubble colour in app.py).
_STATE_KIND = {
    "done": "result",
    "failed": "error",
    "running": "status",
    "queued": "status",
    "paused": "status",
    "stopped": "status",
}
# Which task a state-free control (stop/pause/resume) should target, best first.
_ACTIVE_PRIORITY = {"running": 0, "queued": 1, "paused": 2}


def operator_message(text: str) -> ChatMessage:
    """The operator's own line, echoed the moment they hit send."""
    return ChatMessage("operator", text.strip(), "task")


def _by_id(items, key: str) -> dict[str, dict]:
    return {str(i.get(key, "")): i for i in items if i.get(key)}


def diff_messages(prev: dict | None, curr: dict | None) -> list[ChatMessage]:
    """The new chat lines to append given the previous and current snapshots.

    Deterministic and side-effect free: a newly connected node, a newly accepted
    task, a task state transition, and a changed task detail each become one
    `system` message. Identical snapshots produce []."""
    prev = prev or {}
    curr = curr or {}
    out: list[ChatMessage] = []

    prev_nodes = _by_id(prev.get("nodes", []), "node_id")
    for nid, n in sorted(_by_id(curr.get("nodes", []), "node_id").items()):
        if nid in prev_nodes:
            continue
        label = str(n.get("label") or nid)
        caps = ", ".join(n.get("capabilities") or [])
        out.append(ChatMessage("system", f"节点上线 · {label}" + (f" [{caps}]" if caps else ""), "node"))

    prev_tasks = _by_id(prev.get("tasks", []), "task_id")
    for tid, t in sorted(_by_id(curr.get("tasks", []), "task_id").items()):
        state = str(t.get("state", ""))
        label = str(t.get("task", "") or tid)
        detail = str(t.get("detail", "") or "")
        old = prev_tasks.get(tid)
        if old is None:
            out.append(ChatMessage("system", f"受理任务 · {label} → {state}", _STATE_KIND.get(state, "status")))
            if detail:
                out.append(ChatMessage("system", detail, "status"))
            continue
        old_state = str(old.get("state", ""))
        if state != old_state:
            out.append(ChatMessage("system", f"{label} · {old_state} → {state}", _STATE_KIND.get(state, "status")))
        if detail and detail != str(old.get("detail", "") or ""):
            out.append(ChatMessage("system", detail, "status"))
    return out


def active_task_id(snapshot: dict) -> str | None:
    """The task a chat-side control should act on (there is no table to select
    from): the running one, else queued, else paused. None if nothing is live."""
    best_id: str | None = None
    best_p = 99
    for t in snapshot.get("tasks", []):
        p = _ACTIVE_PRIORITY.get(str(t.get("state", "")), 99)
        if p < best_p:
            best_p, best_id = p, str(t.get("task_id", ""))
    return best_id if best_p < 99 else None


def prepare_command(body: dict, identity: Any | None = None) -> dict:
    """Return the command body to send. With an operator identity, sign it (a
    native app can hold the key, unlike a browser); without one, send as-is for
    the loopback-trusted path."""
    if identity is None:
        return body
    from secdogie_identity import sign_payload

    return sign_payload(identity, body)
