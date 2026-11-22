"""Phase 4 unit tests: compliant read-only AtlasBridge, honest OS privilege wall,
PPL/kernel hard refusal, citadel action_gate enforcement, and agent loop hook.
"""
from __future__ import annotations

import pytest
from secdogie_agent import atlas
from secdogie_agent.atlas_bridge import (
    SAFE_READ_ONLY_MASK,
    AtlasBridge,
    privilege_guidance,
)
from secdogie_agent.loop import AgentConfig, capture_atlas_context


def _user_token(pid: int = 1001, image: str = "notepad.exe", *, integrity: str = "medium",
                ppl: bool = False, ti: bool = False, elevated: bool = False, system: bool = False) -> atlas.TokenSnapshot:
    return atlas.TokenSnapshot(
        pid=pid,
        image=image,
        integrity=integrity,
        elevated=elevated,
        system=system,
        protected_process=ppl,
        trusted_installer=ti,
    )


def test_atlas_bridge_allows_same_integrity_read_only_and_fuses_ax_and_memory():
    bridge = AtlasBridge(platform_name="Windows", clock=lambda: 1234.0)
    me = _user_token(pid=100, image="secdogie-studio.exe")
    target = _user_token(pid=200, image="notepad.exe")
    ax_nodes = [
        atlas.ControlNode(role="window", name="Notes.txt - Notepad"),
        atlas.ControlNode(role="edit", name="Editor"),
    ]
    raw_mem = b"\x00\x00" + "Quarterly summary draft".encode("utf-16-le") + b"\x00\x00"

    ctx = bridge.inspect_foreground(
        me,
        target,
        window_title="Notes.txt - Notepad",
        ax_nodes=ax_nodes,
        raw_mem=raw_mem,
        desired_access=SAFE_READ_ONLY_MASK,
    )
    assert ctx.allowed is True
    assert ctx.read_only is True
    assert ctx.fused_count >= 2
    assert any("Quarterly summary draft" in s for s in ctx.memory_snippets)
    echo = ctx.to_echo_dict(lang="zh")
    assert echo["readOnly"] is True
    assert echo["allowed"] is True
    assert "只读感知" in echo["statusLine"]
    assert "Notes.txt - Notepad" in echo["statusLine"]


@pytest.mark.parametrize(
    "bad_mask",
    [
        atlas.PROCESS_VM_WRITE,
        atlas.PROCESS_VM_READ | atlas.PROCESS_VM_WRITE,
        atlas.PROCESS_ALL_ACCESS,
        atlas.PROCESS_CREATE_THREAD,
    ],
)
def test_atlas_bridge_refuses_any_write_or_mutation_access_mask(bad_mask):
    bridge = AtlasBridge(platform_name="Windows")
    me = _user_token(pid=100, image="secdogie-studio.exe")
    target = _user_token(pid=200, image="notepad.exe")

    ctx = bridge.inspect_foreground(me, target, desired_access=bad_mask)
    assert ctx.allowed is False
    assert ctx.read_only is True
    assert ctx.refused_code != ""
    assert ctx.memory_snippets == ()


@pytest.mark.parametrize(
    "target",
    [
        _user_token(pid=4, image="lsass.exe", integrity="system", system=True, ppl=True),
        _user_token(pid=5, image="csrss.exe", integrity="system", system=True, ppl=True),
        _user_token(pid=6, image="MsMpEng.exe", integrity="system", system=True, ppl=True),
        _user_token(pid=7, image="protected.exe", integrity="medium", ppl=True),
        _user_token(pid=8, image="svc.exe", integrity="system", system=True, ti=True),
        _user_token(pid=9, image="admin_tool.exe", integrity="high", elevated=True),
    ],
)
def test_atlas_bridge_honest_privilege_wall_refuses_ppl_ti_and_higher_integrity(target):
    bridge = AtlasBridge(platform_name="Windows")
    me = _user_token(pid=100, image="secdogie-studio.exe", integrity="medium")

    ctx = bridge.inspect_foreground(
        me,
        target,
        raw_mem="top secret".encode("utf-16-le"),
    )
    assert ctx.allowed is False
    assert ctx.memory_snippets == ()
    assert ctx.ax_summary == ()
    assert "拒绝" in ctx.to_echo_dict(lang="zh")["statusLine"]


def test_atlas_bridge_hard_refuses_trusted_installer_and_edr_evasion():
    bridge = AtlasBridge(platform_name="Windows")
    ti = bridge.refuse_trusted_installer()
    edr = bridge.refuse_edr_evasion()
    assert not ti.ok and ti.code
    assert not edr.ok and edr.code


def test_privilege_guidance_covers_windows_macos_linux_honestly():
    for plat, expected in [("Windows", "windows"), ("Darwin", "macos"), ("Linux", "linux")]:
        zh = privilege_guidance(plat, lang="zh")
        en = privilege_guidance(plat, lang="en")
        assert zh["platform"] == expected
        assert en["platform"] == expected
        assert zh["refusedBoundary"]
        assert en["refusedBoundary"]
        assert len(zh["steps"]) >= 2


def test_atlas_bridge_gates_actions_through_citadel_action_gate():
    action_gate = pytest.importorskip("secdogie_citadel.action_gate")
    IntentContract = action_gate.IntentContract
    PlannedAction = action_gate.PlannedAction
    bridge = AtlasBridge(platform_name="Windows")

    safe_action = PlannedAction(
        kind="read",
        target_id="/tmp/notes.txt",
        intent=IntentContract(purpose="read user notes", rollback="read-only, no mutation", irreversible=False),
    )
    ok_verdict = bridge.gate_action("Read /tmp/notes.txt and show the first 5 lines", safe_action)
    assert ok_verdict.allowed is True
    assert ok_verdict.needs_signature is False

    destructive_action = PlannedAction(
        kind="delete",
        target_id="/home/user/tax-2026.pdf",
        high_risk=True,
        expected_observation="file removed from directory listing",
        intent=IntentContract(purpose="remove file", rollback="cannot be undone", irreversible=True),
    )
    sign_verdict = bridge.gate_action("Delete /home/user/tax-2026.pdf", destructive_action)
    assert sign_verdict.allowed is False
    assert sign_verdict.needs_signature is True

    from secdogie_citadel.cortex.familiar import Familiarity
    from secdogie_citadel.cortex.life import shipped

    cortex = shipped()
    # Mount Phase 1 Gen 2 Sememe-VSA familiarity so Gen 1's famous miss ("关掉杀毒软件")
    # triggers high-novelty clarify and halts unattended desktop execution.
    cortex.familiar = Familiarity.with_sememe(["帮我整理下载文件夹", "关掉客厅的灯"])

    harmful_verdict = bridge.gate_action("关掉杀毒软件", safe_action, cortex=cortex)
    assert harmful_verdict.allowed is False
    assert harmful_verdict.verdict in ("refuse", "dissuade", "clarify", "support")


def test_agent_loop_capture_atlas_context_emits_transparent_event():
    events: list[tuple[str, dict]] = []
    bridge = AtlasBridge(platform_name="Linux", clock=lambda: 500.0)
    cfg = AgentConfig(
        task="read foreground title",
        atlas_bridge=bridge,
        on_event=lambda name, payload: events.append((name, payload)),
    )
    me = _user_token(pid=10, image="secdogie")
    target = _user_token(pid=20, image="gedit")
    ctx = capture_atlas_context(
        cfg,
        me,
        target,
        window_title="todo.md - gedit",
        ax_nodes=[atlas.ControlNode(role="document", name="todo.md")],
    )
    assert ctx is not None and ctx.allowed is True
    assert len(events) == 1
    assert events[0][0] == "atlas_context"
    assert events[0][1]["context"]["windowTitle"] == "todo.md - gedit"
    assert events[0][1]["context"]["readOnly"] is True
