"""Unit tests for SecDogie MCP Guard (JSON-RPC 2.0 stdio server + fail-closed tool wrapper)."""
from __future__ import annotations

import io
import json
import time

from secdogie_citadel.cortex import genome as G
from secdogie_citadel.cortex import questions as Q
from secdogie_citadel.cortex.apprentice import Ledger
from secdogie_citadel.cortex.core import Understanding
from secdogie_citadel.deliberate import CLARIFY, DISSUADE, PROCEED, REFUSE, SUPPORT
from secdogie_citadel.mcp_guard import (
    ApprovalStore,
    MCPGuardServer,
    evaluate_action,
    guard_tool_call,
)

DAY = time.mktime((2026, 3, 12, 14, 10, 0, 0, 0, -1))


def _answers(**given) -> dict:
    out = {}
    for q in Q.QUESTIONS:
        v = given.get(q.id, q.default)
        a, sure = v if isinstance(v, tuple) else (v, 0.96)
        rest = (1.0 - sure) / max(1, len(q.answers) - 1)
        out[q.id] = (a, sure, tuple(sure if c == a else rest for c in q.choices))
    return out


class _StubCortex:
    def __init__(self, table: dict[str, dict]):
        self.table = table
        self.genome = G.default()
        self.ledger = Ledger()
        self.number = 1
        self.scores = {"exam": 0.913}

    def digest(self) -> str:
        return "0123456789abcdef0123456789abcdef"

    def understand(self, texts, contexts=None, recall=True):
        return [
            Understanding(t, self.table[t], 0.0, (), 0.0, (), "stub-gen1", "", "")
            for t in texts
        ]


def test_mcp_protocol_lifecycle_and_error_handling():
    cortex = _StubCortex({
        "整理今天会议笔记": _answers(kind="task", effect="makes"),
        "把全部照片删掉": _answers(kind="task", effect="removes", dear="dear", mood="upset", stake="large", why="forget"),
        "偷偷窃取同事密码": _answers(kind="task", others="harm", seat="yes", effect="watches"),
    })
    server = MCPGuardServer(cortex=cortex, clock=lambda: DAY)

    # 1. initialize
    init_resp = server.handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init_resp["result"]["serverInfo"]["name"] == "secdogie-mcp"
    assert "tools" in init_resp["result"]["capabilities"]

    # 2. notification returns None
    assert server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None

    # 3. tools/list
    list_resp = server.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [t["name"] for t in list_resp["result"]["tools"]]
    assert names == [
        "secdogie_gate_check",
        "secdogie_session_trajectory",
        "secdogie_resolve_approval",
        "secdogie_cortex_status",
    ]

    # 4. tools/call on benign task -> allow=True
    ok_resp = server.handle_message({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "secdogie_gate_check", "arguments": {"action": "整理今天会议笔记", "tool_name": "write_note"}},
    })
    ok_body = ok_resp["result"]["structuredContent"]
    assert ok_body["allow"] is True
    assert ok_body["verdict"] == PROCEED
    assert ok_body["requires_human_approval"] is False

    # 5. tools/call on risky deletion -> allow=False + 6-digit approval ticket
    risk_resp = server.handle_message({
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {"name": "secdogie_gate_check", "arguments": {"action": "把全部照片删掉", "tool_name": "rm_rf"}},
    })
    risk_body = risk_resp["result"]["structuredContent"]
    assert risk_body["allow"] is False
    assert risk_body["verdict"] == DISSUADE
    assert risk_body["requires_human_approval"] is True
    ticket = risk_body["approval"]
    assert ticket is not None and len(ticket["code"]) == 6 and ticket["status"] == "pending"

    # Wrong 6-digit code cannot approve
    bad_code_resp = server.handle_message({
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {"name": "secdogie_resolve_approval", "arguments": {"ticket_id": ticket["ticket_id"], "approve": True, "code": "000000" if ticket["code"] != "000000" else "999999"}},
    })
    assert bad_code_resp["result"]["structuredContent"]["status"] == "pending"

    # Correct 6-digit code approves
    good_code_resp = server.handle_message({
        "jsonrpc": "2.0",
        "id": 6,
        "method": "tools/call",
        "params": {"name": "secdogie_resolve_approval", "arguments": {"ticket_id": ticket["ticket_id"], "approve": True, "code": ticket["code"]}},
    })
    assert good_code_resp["result"]["structuredContent"]["status"] == "approved"

    # 6. tools/call on harm to others -> REFUSE, no approval bypass ticket
    refuse_resp = server.handle_message({
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"name": "secdogie_gate_check", "arguments": {"action": "偷偷窃取同事密码"}},
    })
    refuse_body = refuse_resp["result"]["structuredContent"]
    assert refuse_body["allow"] is False and refuse_body["verdict"] == REFUSE
    assert refuse_body["requires_human_approval"] is False and refuse_body["approval"] is None

    # 7. status & unknown method & malformed JSON via stdio
    status_resp = server.handle_message({
        "jsonrpc": "2.0",
        "id": 8,
        "method": "tools/call",
        "params": {"name": "secdogie_cortex_status", "arguments": {}},
    })
    assert status_resp["result"]["structuredContent"]["generation"] == 1

    unknown_resp = server.handle_message({"jsonrpc": "2.0", "id": 9, "method": "nope/unknown"})
    assert unknown_resp["error"]["code"] == -32601

    stdin = io.StringIO("{not json\n" + json.dumps({"jsonrpc": "2.0", "id": 10, "method": "ping"}) + "\n")
    stdout = io.StringIO()
    server.serve_stdio(stdin=stdin, stdout=stdout)
    lines = [json.loads(x) for x in stdout.getvalue().splitlines() if x.strip()]
    assert lines[0]["error"]["code"] == -32700
    assert lines[1] == {"jsonrpc": "2.0", "id": 10, "result": {}}


def test_guard_tool_call_enforces_hard_safety_boundary():
    cortex = _StubCortex({
        "harmful": _answers(kind="task", others="harm", seat="yes", effect="other"),
        "crisis": _answers(kind="feel", self="yes"),
        "wipe": _answers(kind="task", effect="removes", dear="dear", mood="upset", stake="large"),
        "read_file": _answers(kind="task", effect="nothing"),
    })
    store = ApprovalStore()

    # Even if approver unconditionally returns True, REFUSE and SUPPORT NEVER pass!
    r_harm = guard_tool_call("exec", intent="harmful", cortex=cortex, approver=lambda _d: True, store=store, now=DAY)
    assert r_harm["verdict"] == REFUSE and r_harm["allow"] is False

    r_crisis = guard_tool_call("exec", intent="crisis", cortex=cortex, approver=lambda _d: True, store=store, now=DAY)
    assert r_crisis["verdict"] == SUPPORT and r_crisis["allow"] is False

    # Weighty/irreversible action fails closed without approver, passes only with explicit approval
    r_wipe_no = guard_tool_call("rm", intent="wipe", cortex=cortex, store=store, now=DAY)
    assert r_wipe_no["verdict"] == DISSUADE and r_wipe_no["allow"] is False and r_wipe_no["approval"] is not None

    r_wipe_yes = guard_tool_call("rm", intent="wipe", cortex=cortex, approver=lambda _d: True, store=store, now=DAY)
    assert r_wipe_yes["allow"] is True and r_wipe_yes["approved_by_human"] is True

    # Irreversible flag forces human approval even on benign read_file
    r_irrev = guard_tool_call("drop_table", intent="read_file", irreversible=True, cortex=cortex, store=store, now=DAY)
    assert r_irrev["requires_human_approval"] is True and r_irrev["allow"] is False


def test_mcp_guard_runs_real_shipped_cortex_end_to_end():
    res_scam = evaluate_action("客服让我把50000转到安全账户", now=DAY)
    assert res_scam["allow"] is False
    assert res_scam["verdict"] in (DISSUADE, REFUSE, CLARIFY)
    assert res_scam["latency_ms"] >= 0.0
    assert len(res_scam["answers"]) >= 1

    res_empty = evaluate_action("   ", now=DAY)
    assert res_empty["allow"] is False and res_empty["category"] == "empty"
