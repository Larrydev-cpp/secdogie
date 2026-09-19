from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from secdogie_console.controller import ConsoleController  # noqa: E402
from secdogie_identity import Allowlist, Identity, sign_payload  # noqa: E402


class FakeFleet:
    """Duck-typed stand-in for FleetServer: records calls, returns simple values."""

    def __init__(self):
        self.calls = []
        self._snap = {"nodes": [], "tasks": [], "settled": True, "paused": 0}

    def snapshot(self):
        return self._snap

    def submit(self, task, options=None, *, priority=0):
        self.calls.append(("submit", task, options, priority))
        return "t-1"

    def stop_task(self, task_id):
        self.calls.append(("stop", task_id))
        return True

    def pause_task(self, task_id, reason="operator"):
        self.calls.append(("pause", task_id))
        return True

    def resume_task(self, task_id):
        self.calls.append(("resume", task_id))
        return True


def test_state_snapshot_flags_signature_requirement():
    c = ConsoleController(FakeFleet())
    assert c.state_snapshot()["requires_signature"] is False
    c2 = ConsoleController(FakeFleet(), operator_allowlist=Allowlist({Identity.generate().did}))
    assert c2.state_snapshot()["requires_signature"] is True


def test_submit_and_control_ops():
    fleet = FakeFleet()
    c = ConsoleController(fleet)
    assert c.command({"op": "submit", "task": "tidy", "options": {"auto": True}}) == {"op": "submit", "task_id": "t-1"}
    assert c.command({"op": "stop", "task_id": "t-1"}) == {"op": "stop", "ok": True}
    assert c.command({"op": "pause", "task_id": "t-1"})["op"] == "pause"
    assert c.command({"op": "resume", "task_id": "t-1"})["op"] == "resume"
    assert ("submit", "tidy", {"auto": True}, 0) in fleet.calls


def test_bad_commands_raise():
    c = ConsoleController(FakeFleet())
    with pytest.raises(ValueError):
        c.command({"op": "explode"})
    with pytest.raises(ValueError):
        c.command({"op": "submit", "task": "   "})
    with pytest.raises(ValueError):
        c.command({"op": "stop"})


def test_authorize_loopback_dev_mode_allows_all():
    c = ConsoleController(FakeFleet())  # no allowlist
    ok, signer = c.authorize({"op": "submit", "task": "x"})
    assert ok and signer is None


def test_authorize_requires_signature_when_allowlisted():
    operator = Identity.generate()
    c = ConsoleController(FakeFleet(), operator_allowlist=Allowlist({operator.did}))

    # unsigned -> rejected
    ok, signer = c.authorize({"op": "submit", "task": "x"})
    assert not ok

    # signed by an authorized operator -> accepted
    signed = sign_payload(operator, {"op": "submit", "task": "x"})
    ok, signer = c.authorize(signed)
    assert ok and signer == operator.did

    # signed by a stranger -> rejected (authentic but not authorized)
    stranger = Identity.generate()
    ok, signer = c.authorize(sign_payload(stranger, {"op": "submit", "task": "x"}))
    assert not ok and signer == stranger.did
