"""Pure presentation logic for the desktop window -- no tkinter here.

Turns a fleet snapshot (from ConsoleController.state_snapshot) into the rows the
window renders, decides which actions a task offers, and prepares a command body
(signing it when an operator identity is supplied). Kept tk-free so it is unit
tested headlessly; app.py is the thin view that calls these.
"""
from __future__ import annotations

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


def prepare_command(body: dict, identity: Any | None = None) -> dict:
    """Return the command body to send. With an operator identity, sign it (a
    native app can hold the key, unlike a browser); without one, send as-is for
    the loopback-trusted path."""
    if identity is None:
        return body
    from secdogie_identity import sign_payload

    return sign_payload(identity, body)
