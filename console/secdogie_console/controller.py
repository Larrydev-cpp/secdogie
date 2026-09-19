"""The console's brain: a thin, transport-free wrapper over a fleet coordinator.

It exposes exactly what the web UI needs -- a state snapshot, and the four
control operations (submit / stop / pause / resume) -- and enforces operator
authorization on the mutating ones. `fleet` is duck-typed (anything with
snapshot / submit / stop_task / pause_task / resume_task), so tests drive it
with a fake and never open a socket.

Authorization: when an operator Allowlist is configured, every command body must
carry a valid operator-DID signature (secdogie-identity). With no allowlist the
console is loopback-trusted, the same model secdogie-open uses -- fine for a
single operator on their own machine, and the path to gate for remote exposure.
"""
from __future__ import annotations

from typing import Any

from secdogie_identity import Allowlist, verify_payload

_OPS = frozenset({"submit", "stop", "pause", "resume"})


class ConsoleController:
    def __init__(self, fleet: Any, *, operator_allowlist: Allowlist | None = None):
        self._fleet = fleet
        self._operator_allowlist = operator_allowlist

    @property
    def requires_signature(self) -> bool:
        return self._operator_allowlist is not None

    def state_snapshot(self) -> dict:
        """The fleet's nodes/tasks view, plus whether commands need signing."""
        snap = self._fleet.snapshot()
        return {"requires_signature": self.requires_signature, **snap}

    def authorize(self, body: dict) -> tuple[bool, str | None]:
        """(ok, signer_did). Loopback dev mode (no allowlist) is always ok;
        otherwise the body must be operator-DID-signed and on the allowlist."""
        if self._operator_allowlist is None:
            return True, None
        return verify_payload(body, self._operator_allowlist)

    def command(self, body: dict) -> dict:
        """Dispatch one control op. Raises ValueError on a malformed request;
        callers authorize() first."""
        op = body.get("op")
        if op not in _OPS:
            raise ValueError(f"unknown op {op!r}")
        if op == "submit":
            task = (body.get("task") or "").strip()
            if not task:
                raise ValueError("submit requires a non-empty task")
            options = body.get("options") or {}
            if not isinstance(options, dict):
                raise ValueError("options must be an object")
            tid = self._fleet.submit(task, options, priority=int(body.get("priority") or 0))
            return {"op": "submit", "task_id": tid}
        task_id = body.get("task_id")
        if not task_id:
            raise ValueError(f"{op} requires task_id")
        if op == "stop":
            return {"op": "stop", "ok": self._fleet.stop_task(task_id)}
        if op == "pause":
            return {"op": "pause", "ok": self._fleet.pause_task(task_id)}
        return {"op": "resume", "ok": self._fleet.resume_task(task_id)}
