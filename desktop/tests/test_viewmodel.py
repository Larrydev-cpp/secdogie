"""Headless tests for the desktop window's pure presentation logic. No tkinter
and no display needed (the tk view in app.py is validated where a display exists).
"""
from __future__ import annotations

import pytest
from secdogie_desktop import viewmodel


def _snap(nodes=None, tasks=None, requires_signature=False):
    return {"nodes": nodes or [], "tasks": tasks or [], "requires_signature": requires_signature}


def test_available_actions_by_state():
    assert viewmodel.available_actions("running") == ("stop",)
    assert viewmodel.available_actions("queued") == ("stop",)
    assert viewmodel.available_actions("paused") == ("resume",)
    assert viewmodel.available_actions("done") == ()
    assert viewmodel.available_actions("weird") == ()


def test_node_rows_shape():
    snap = _snap(nodes=[
        {"node_id": "n1", "label": "vm-1", "capabilities": ["desktop-ax"], "task_id": "t1"},
        {"node_id": "n2", "label": "", "capabilities": [], "task_id": None},
    ])
    rows = viewmodel.node_rows(snap)
    assert rows[0] == ("n1", "vm-1", "desktop-ax", "t1")
    assert rows[1] == ("n2", "", "", "idle")  # no task -> "idle"


def test_task_rows_carry_actions():
    snap = _snap(tasks=[
        {"task_id": "t1", "state": "running", "task": "tidy", "node_id": "n1", "detail": "step 3"},
        {"task_id": "t2", "state": "paused", "task": "build", "node_id": None},
    ])
    rows = viewmodel.task_rows(snap)
    assert rows[0]["actions"] == ("stop",)
    assert rows[0]["node_id"] == "n1"
    assert rows[1]["actions"] == ("resume",)
    assert rows[1]["node_id"] == ""


def test_status_line():
    snap = _snap(
        nodes=[{"node_id": "n1"}],
        tasks=[{"state": "running"}, {"state": "done"}],
    )
    line = viewmodel.status_line(snap)
    assert "1 node(s)" in line and "2 task(s)" in line and "1 running" in line and "loopback" in line
    assert "signed commands" in viewmodel.status_line(_snap(requires_signature=True))


def test_prepare_command_without_identity_is_passthrough():
    body = {"op": "submit", "task": "x"}
    assert viewmodel.prepare_command(body) is body


def test_prepare_command_signs_with_identity():
    pytest.importorskip("nacl")
    from secdogie_identity import Identity, verify_payload

    me = Identity.generate()
    signed = viewmodel.prepare_command({"op": "stop", "task_id": "t1"}, me)
    assert signed["signer"] == me.did
    ok, signer = verify_payload(signed)
    assert ok and signer == me.did


# --- chat transcript --------------------------------------------------------


def test_operator_message_is_a_user_line():
    m = viewmodel.operator_message("  tidy my desktop  ")
    assert m.role == "operator" and m.text == "tidy my desktop" and m.kind == "task"


def test_diff_messages_announces_a_new_task_and_its_detail():
    prev = _snap()
    curr = _snap(tasks=[{"task_id": "t1", "state": "running", "task": "tidy", "detail": "step 1"}])
    msgs = viewmodel.diff_messages(prev, curr)
    assert [m.role for m in msgs] == ["system", "system"]
    assert "受理任务" in msgs[0].text and "tidy" in msgs[0].text and msgs[0].kind == "status"
    assert msgs[1].text == "step 1"


def test_diff_messages_reports_state_transitions_with_tone():
    prev = _snap(tasks=[{"task_id": "t1", "state": "running", "task": "tidy"}])
    done = viewmodel.diff_messages(prev, _snap(tasks=[{"task_id": "t1", "state": "done", "task": "tidy"}]))
    assert done[0].kind == "result" and "running → done" in done[0].text
    failed = viewmodel.diff_messages(prev, _snap(tasks=[{"task_id": "t1", "state": "failed", "task": "tidy"}]))
    assert failed[0].kind == "error"


def test_diff_messages_new_node_and_no_change():
    prev = _snap()
    curr = _snap(nodes=[{"node_id": "n1", "label": "vm-1", "capabilities": ["desktop-ax"]}])
    node_msgs = viewmodel.diff_messages(prev, curr)
    assert node_msgs[0].kind == "node" and "vm-1" in node_msgs[0].text and "desktop-ax" in node_msgs[0].text
    assert viewmodel.diff_messages(curr, curr) == []  # identical snapshots -> nothing


def test_diff_messages_detail_change_only():
    prev = _snap(tasks=[{"task_id": "t1", "state": "running", "task": "tidy", "detail": "step 1"}])
    curr = _snap(tasks=[{"task_id": "t1", "state": "running", "task": "tidy", "detail": "step 2"}])
    msgs = viewmodel.diff_messages(prev, curr)
    assert len(msgs) == 1 and msgs[0].text == "step 2"


def test_active_task_id_prefers_running_then_queued_then_paused():
    snap = _snap(tasks=[
        {"task_id": "t1", "state": "paused"},
        {"task_id": "t2", "state": "queued"},
        {"task_id": "t3", "state": "running"},
    ])
    assert viewmodel.active_task_id(snap) == "t3"
    assert viewmodel.active_task_id(_snap(tasks=[{"task_id": "t9", "state": "done"}])) is None
    assert viewmodel.active_task_id(_snap()) is None
