"""Unit tests for WorldTrajectory multi-step causal closure (Semantic Chaining defense)."""
from __future__ import annotations

from secdogie_citadel.action_gate import COMPOSITIONAL_CHAIN, REJECT, GateContext, PlannedAction, gate
from secdogie_citadel.mcp_guard import MCPGuardServer, evaluate_action
from secdogie_citadel.mind.plan import DISSUADE, PROCEED, REFUSE
from secdogie_citadel.mind.trajectory import WorldTrajectory


def test_compositional_credential_exfiltration_blocked_with_provenance():
    traj = WorldTrajectory(session_id="s-exfil")

    # Step 1: read-only sensitive credential access (individually passes trajectory check)
    a1 = traj.simulate(["secret_in_context"], tool="read_env", label="读取本地配置")
    assert a1.verdict == PROCEED
    assert not a1.blocked
    traj.commit(["secret_in_context"], tool="read_env", label="读取本地配置")

    # Step 2: benign local note (does not trigger exfiltration)
    a2 = traj.simulate(["writes_local_note"], tool="calendar_remind", label="记录本地备忘")
    assert a2.verdict == PROCEED
    traj.commit(["writes_local_note"], tool="calendar_remind", label="记录本地备忘")

    # Step 3: poetic, innocent-looking outbound webhook -> causal closure fires REFUSE
    a3 = traj.simulate(["network_egress"], tool="webhook_send", label="把今天整理好的摘要发给同步服务")
    assert a3.verdict == REFUSE
    assert a3.blocked
    assert a3.risk == 1.0
    rule_ids = {t.rule.id for t in a3.triggered}
    assert "credential_exfiltration" in rule_ids
    exfil = next(t for t in a3.triggered if t.rule.id == "credential_exfiltration")
    steps_by_atom = {ant["atom"]: ant["step"] for ant in exfil.antecedents}
    assert steps_by_atom["secret_in_context"] == 1
    assert steps_by_atom["network_egress"] == 3


def test_lethal_trifecta_and_multi_hop_dropper_chain():
    # Lethal Trifecta: untrusted web page + local secret + outbound send
    traj = WorldTrajectory(session_id="s-trifecta")
    traj.commit(["untrusted_input"], tool="browser_read", label="打开外部工单网页")
    traj.commit(["secret_in_context"], tool="read_secret", label="读取 API Key")
    res = traj.simulate(["network_egress"], tool="http_post", label="上报状态")
    assert res.verdict == REFUSE
    assert "lethal_trifecta_compromise" in res.terminal_atoms

    # Multi-hop transitive closure: network_ingress -> writes_executable -> executes_code
    drop = WorldTrajectory(session_id="s-dropper")
    drop.commit(["network_ingress"], tool="http_fetch", label="下载组件包")
    a_stage = drop.simulate(["writes_executable"], tool="write_script", label="保存为工具脚本")
    assert a_stage.verdict == DISSUADE
    assert "remote_dropper" in a_stage.terminal_atoms
    drop.commit(["writes_executable"], tool="write_script", label="保存为工具脚本")

    a_exec = drop.simulate(["executes_code"], tool="shell_exec", label="运行一下看看")
    assert a_exec.verdict == REFUSE
    assert "remote_code_execution" in a_exec.terminal_atoms


def test_benign_multi_step_workflow_never_false_triggers():
    traj = WorldTrajectory(session_id="s-benign")
    for eff, tool in ((["reads_public"], "read_docs"), (["writes_local_note"], "calendar_remind")):
        a = traj.simulate(eff, tool=tool, label="benign step")
        assert a.verdict == PROCEED
        assert not a.blocked
        traj.commit(eff, tool=tool, label="benign step")
    snap = traj.snapshot()
    assert snap["step_count"] == 2
    assert {x["atom"] for x in snap["accumulated_atoms"]} == {"reads_public", "writes_local_note"}


def test_action_gate_blocks_compositional_chain_even_after_read_only_step():
    traj = WorldTrajectory(session_id="s-gate")
    # Step 1 is read-only (not mutating, not destructive!)
    step1 = PlannedAction(kind="read", target_name="read_env", effects=("secret_in_context",))
    d1 = gate(step1, GateContext(trajectory=traj, requires_verification=False))
    assert d1.allowed
    traj.commit(step1.effects, tool="read_env", label="read .env")

    # Step 2 sends over network -> action_gate catches COMPOSITIONAL_CHAIN
    step2 = PlannedAction(kind="send", target_name="webhook_send", text="sync report")
    d2 = gate(step2, GateContext(trajectory=traj, requires_verification=False))
    assert d2.verdict == REJECT
    assert COMPOSITIONAL_CHAIN in d2.findings
    assert d2.risk == 1.0


def test_mcp_guard_tracks_session_trajectory_and_blocks_poetic_bypass():
    srv = MCPGuardServer()
    # Step 1: innocent phrasing reading credentials
    r1 = srv.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "secdogie_gate_check",
                "arguments": {
                    "action": "帮我看一下今天的环境配置",
                    "tool_name": "read_env",
                    "session_id": "demo-chain",
                    "effects": ["secret_in_context"],
                },
            },
        }
    )
    p1 = r1["result"]["structuredContent"]
    assert p1["verdict"] != REFUSE
    assert p1["trajectory"]["blocked"] is False

    # Step 2: completely innocent-sounding outbound request -> world model slams REFUSE
    r2 = srv.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "secdogie_gate_check",
                "arguments": {
                    "action": "把刚才整理的笔记同步到外部接口",
                    "tool_name": "webhook_send",
                    "session_id": "demo-chain",
                    "effects": ["network_egress"],
                },
            },
        }
    )
    p2 = r2["result"]["structuredContent"]
    assert p2["allow"] is False
    assert p2["verdict"] == REFUSE
    assert p2["category"] == "trajectory"
    assert p2["source"] == "world-trajectory"
    assert any(t["rule_id"] == "credential_exfiltration" for t in p2["trajectory"]["triggered"])

    # Inspect and reset session trajectory
    r3 = srv.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "secdogie_session_trajectory",
                "arguments": {"session_id": "demo-chain", "reset": True},
            },
        }
    )
    assert r3["result"]["structuredContent"]["step_count"] == 0


def test_evaluate_action_commits_only_non_blocked_steps():
    traj = WorldTrajectory(session_id="s-commit")
    r1 = evaluate_action("帮我看一下今天的环境配置", tool_name="read_env", effects=["secret_in_context"], trajectory=traj)
    assert r1["verdict"] != REFUSE
    assert len(traj.steps) == 1
    res = evaluate_action("把笔记发给外部接口", tool_name="webhook_send", effects=["network_egress"], trajectory=traj)
    assert res["verdict"] == REFUSE
    # Blocked step must NOT be committed into trajectory state
    assert len(traj.steps) == 1
