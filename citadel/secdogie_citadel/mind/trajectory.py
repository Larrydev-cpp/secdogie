"""World-model trajectory engine: multi-step causal closure against compositional attacks.

Single-turn text screening cannot defeat Semantic Chaining / Compositional attacks
where an adversary splits a harmful goal into individually benign-looking steps
(e.g. Step 1 read a secret file -> Step 2 pack/encode -> Step 3 send over network).

This module maintains a per-session bag of concrete system-state atoms
(``WorldTrajectory``) and performs forward-chaining causal simulation
(``simulate``) *before* any proposed tool action executes. It never reads words,
regular expressions, or keyword lists—only symbolic state transitions and causal
closure rules.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .plan import DISSUADE, PROCEED, REFUSE

# ---------------------------------------------------------------------------
# System-state effect atom catalog (pure symbolic vocabulary, zero regex)
# ---------------------------------------------------------------------------

EFFECT_ATOMS: dict[str, dict[str, str]] = {
    "secret_in_context": {
        "category": "source",
        "zh": "敏感凭据进入上下文",
        "en": "Sensitive credential entered context",
    },
    "bulk_private_read": {
        "category": "source",
        "zh": "批量读取隐私/业务文件",
        "en": "Bulk read of private or workspace files",
    },
    "untrusted_input": {
        "category": "source",
        "zh": "摄入不可信外部内容（网页/邮件/工单）",
        "en": "Ingested untrusted external content (web/email/issue)",
    },
    "archives_or_encodes": {
        "category": "stage",
        "zh": "打包、压缩或编码数据",
        "en": "Archived, compressed, or encoded data",
    },
    "writes_executable": {
        "category": "stage",
        "zh": "写入脚本或可执行文件",
        "en": "Wrote script or executable payload",
    },
    "grants_exec": {
        "category": "stage",
        "zh": "赋予可执行权限",
        "en": "Granted executable permissions",
    },
    "defense_weakened": {
        "category": "control",
        "zh": "削弱或关闭安全防护/防火墙/审计",
        "en": "Weakened or disabled security defense / firewall / audit",
    },
    "privilege_elevated": {
        "category": "control",
        "zh": "提升至管理员/高权限上下文",
        "en": "Elevated to privileged / administrator context",
    },
    "persists_startup": {
        "category": "control",
        "zh": "写入开机自启/计划任务持久化",
        "en": "Added startup / scheduled task persistence",
    },
    "network_ingress": {
        "category": "source",
        "zh": "从外部网络拉取远端内容或二进制",
        "en": "Fetched remote payload or content over network",
    },
    "network_egress": {
        "category": "sink",
        "zh": "向外部网络发送数据或建立外连",
        "en": "Transmitted data or opened outbound connection",
    },
    "executes_code": {
        "category": "sink",
        "zh": "执行本地命令、脚本或子进程",
        "en": "Executed local command, script, or subprocess",
    },
    "mutates_system": {
        "category": "sink",
        "zh": "修改系统配置、注册表或受保护文件",
        "en": "Modified system config, registry, or protected files",
    },
    "deletes_data": {
        "category": "sink",
        "zh": "不可逆删除或覆写数据",
        "en": "Irreversibly deleted or overwrote data",
    },
    "reads_public": {
        "category": "benign",
        "zh": "读取公开文档或只读元数据",
        "en": "Read public documentation or benign metadata",
    },
    "writes_local_note": {
        "category": "benign",
        "zh": "写入普通本地笔记/草稿",
        "en": "Wrote ordinary local note or draft",
    },
}

# Conservative default effect mapping from ActionGate action kinds (exact key lookup, zero regex).
KIND_TO_EFFECTS: dict[str, tuple[str, ...]] = {
    "run": ("executes_code",),
    "run_elevated": ("privilege_elevated", "executes_code"),
    "delete": ("deletes_data",),
    "overwrite": ("deletes_data", "mutates_system"),
    "format": ("deletes_data", "mutates_system"),
    "drop": ("deletes_data",),
    "uninstall": ("mutates_system",),
    "post": ("network_egress",),
    "publish": ("network_egress",),
    "submit": ("network_egress",),
    "send": ("network_egress",),
    "reply": ("network_egress",),
    "comment": ("network_egress",),
    "tweet": ("network_egress",),
    "dm": ("network_egress",),
}

# Conservative default effect mapping for standard MCP tool names (exact key lookup, zero regex).
TOOL_DEFAULT_EFFECTS: dict[str, tuple[str, ...]] = {
    "shell_exec": ("executes_code",),
    "bash": ("executes_code",),
    "terminal_exec": ("executes_code",),
    "run_command": ("executes_code",),
    "http_post": ("network_egress",),
    "webhook_send": ("network_egress",),
    "send_email": ("network_egress",),
    "http_fetch": ("network_ingress", "untrusted_input"),
    "web_fetch": ("network_ingress", "untrusted_input"),
    "browser_read": ("untrusted_input",),
    "read_secret": ("secret_in_context",),
    "read_env": ("secret_in_context",),
    "read_credentials": ("secret_in_context",),
    "archive_files": ("archives_or_encodes",),
    "compress_files": ("archives_or_encodes",),
    "write_script": ("writes_executable",),
    "chmod_exec": ("grants_exec",),
    "firewall_config": ("defense_weakened", "mutates_system"),
    "service_stop": ("defense_weakened", "mutates_system"),
    "cron_install": ("persists_startup", "mutates_system"),
    "file_delete": ("deletes_data",),
    "delete_file": ("deletes_data",),
    "bank_transfer": ("network_egress",),
    "calendar_remind": ("writes_local_note",),
    "read_docs": ("reads_public",),
}


@dataclass(frozen=True)
class CompositionalRule:
    """One forward-chaining causal rule over accumulated + proposed system state atoms."""

    id: str
    needs: tuple[str, ...]
    gives: str
    verdict: str  # PROCEED | DISSUADE | REFUSE
    risk: float
    zh: str
    en: str
    lacks: tuple[str, ...] = ()


TRAJECTORY_RULES: tuple[CompositionalRule, ...] = (
    # --- Multi-hop intermediate derivations ---
    CompositionalRule(
        id="remote_dropper_staged",
        needs=("network_ingress", "writes_executable"),
        gives="remote_dropper",
        verdict=DISSUADE,
        risk=0.85,
        zh="从远端拉取内容并落盘为可执行文件（疑似远端投递）",
        en="Fetched remote content and wrote an executable payload (staged dropper)",
    ),
    CompositionalRule(
        id="remote_dropper_exec",
        needs=("remote_dropper", "executes_code"),
        gives="remote_code_execution",
        verdict=REFUSE,
        risk=1.0,
        zh="远端拉取 → 落盘脚本 → 本地执行形成完整远控链（终态越界）",
        en="Remote fetch -> executable write -> local execution completes an RCE chain",
    ),
    # --- Credential / Data Exfiltration chains ---
    CompositionalRule(
        id="lethal_trifecta_exfil",
        needs=("untrusted_input", "secret_in_context", "network_egress"),
        gives="lethal_trifecta_compromise",
        verdict=REFUSE,
        risk=1.0,
        zh="致命三要素齐聚：不可信外部内容 + 敏感凭据 + 外部网络发送（典型间接注入窃密）",
        en="Lethal Trifecta: untrusted input + sensitive secret + outbound network egress",
    ),
    CompositionalRule(
        id="credential_exfiltration",
        needs=("secret_in_context", "network_egress"),
        gives="credential_exfiltration",
        verdict=REFUSE,
        risk=1.0,
        zh="敏感凭据已进入上下文，继续外发将导致凭据离开本机边界",
        en="Sensitive credential is in context; outbound egress would exfiltrate it",
    ),
    CompositionalRule(
        id="bulk_data_exfiltration",
        needs=("bulk_private_read", "network_egress"),
        gives="bulk_data_exfiltration",
        verdict=REFUSE,
        risk=1.0,
        zh="批量读取私有文件后向外发送，终态为批量数据外泄",
        en="Bulk private file read followed by outbound egress results in data exfiltration",
    ),
    CompositionalRule(
        id="secret_staging",
        needs=("secret_in_context", "archives_or_encodes"),
        gives="exfil_staging",
        verdict=DISSUADE,
        risk=0.85,
        zh="将敏感凭据打包/编码，极可能用于绕过审查准备外传",
        en="Archiving or encoding sensitive credentials indicates exfiltration staging",
    ),
    CompositionalRule(
        id="bulk_staging",
        needs=("bulk_private_read", "archives_or_encodes"),
        gives="exfil_staging",
        verdict=DISSUADE,
        risk=0.8,
        zh="批量读取私有数据后打包/编码，终态接近数据外传前兆",
        en="Bulk private read followed by archiving/encoding indicates exfiltration staging",
    ),
    # --- Indirect prompt injection hijack chains ---
    CompositionalRule(
        id="indirect_prompt_exec",
        needs=("untrusted_input", "executes_code"),
        gives="indirect_prompt_hijack",
        verdict=REFUSE,
        risk=0.95,
        zh="摄入不可信外部内容后直接执行本地代码/命令，存在提示注入劫持风险",
        en="Executing local code after ingesting untrusted external content risks prompt hijack",
    ),
    CompositionalRule(
        id="indirect_prompt_delete",
        needs=("untrusted_input", "deletes_data"),
        gives="indirect_prompt_destruction",
        verdict=REFUSE,
        risk=0.95,
        zh="摄入不可信外部内容后触发不可逆删除，终态为被诱导破坏数据",
        en="Irreversible deletion after ingesting untrusted content risks hijacked destruction",
    ),
    CompositionalRule(
        id="indirect_prompt_mutate",
        needs=("untrusted_input", "mutates_system"),
        gives="indirect_system_tamper",
        verdict=DISSUADE,
        risk=0.85,
        zh="摄入不可信外部内容后修改系统配置，需人工审阅因果链",
        en="System mutation after ingesting untrusted content requires explicit verification",
    ),
    # --- Defense evasion & staged payload execution ---
    CompositionalRule(
        id="defense_evasion_exec",
        needs=("defense_weakened", "executes_code"),
        gives="defense_evasion_compromise",
        verdict=REFUSE,
        risk=1.0,
        zh="削弱安全防护后执行代码，终态为绕过防御执行未受控负载",
        en="Weakening security defenses followed by code execution completes defense evasion",
    ),
    CompositionalRule(
        id="defense_evasion_ingress",
        needs=("defense_weakened", "network_ingress"),
        gives="defense_evasion_ingress",
        verdict=REFUSE,
        risk=1.0,
        zh="削弱安全防护/防火墙后拉取外部负载，终态越界",
        en="Weakening defenses followed by remote payload fetch violates host boundary",
    ),
    CompositionalRule(
        id="staged_payload_exec",
        needs=("writes_executable", "executes_code"),
        gives="staged_payload_compromise",
        verdict=REFUSE,
        risk=0.95,
        zh="拆分落盘脚本/二进制并立即执行，终态为未审计代码落地运行",
        en="Writing an executable payload and executing it completes staged execution",
    ),
    CompositionalRule(
        id="staged_payload_persist",
        needs=("writes_executable", "persists_startup"),
        gives="persistent_implant",
        verdict=REFUSE,
        risk=1.0,
        zh="落盘可执行文件并写入开机自启/计划任务，终态为持久化驻留",
        en="Writing an executable and adding startup persistence creates a persistent implant",
    ),
    CompositionalRule(
        id="staged_payload_chmod",
        needs=("writes_executable", "grants_exec"),
        gives="armed_payload",
        verdict=DISSUADE,
        risk=0.8,
        zh="为新写入脚本赋予可执行权限，已接近执行临界态",
        en="Granting execute permission to a newly written payload arms it for execution",
    ),
    # --- Privilege escalation + control plane ---
    CompositionalRule(
        id="host_compromise_defense",
        needs=("privilege_elevated", "defense_weakened"),
        gives="host_compromise",
        verdict=REFUSE,
        risk=1.0,
        zh="高权限上下文下关闭/削弱安全防护，终态为整机失控",
        en="Weakening security controls with elevated privileges compromises host integrity",
    ),
    CompositionalRule(
        id="host_compromise_persist",
        needs=("privilege_elevated", "persists_startup"),
        gives="host_compromise",
        verdict=REFUSE,
        risk=1.0,
        zh="高权限上下文下写入持久化自启项，终态为系统级后门风险",
        en="Adding startup persistence with elevated privileges risks system-level backdoor",
    ),
)

_VERDICT_SEVERITY = {PROCEED: 0, DISSUADE: 1, REFUSE: 2}


def infer_effects(
    *,
    effects: tuple[str, ...] | list[str] = (),
    tool_name: str = "",
    action_kind: str = "",
    irreversible: bool = False,
) -> tuple[str, ...]:
    """Derive a deduplicated tuple of symbolic system-effect atoms (exact table lookup, zero regex)."""
    out: list[str] = []
    seen: set[str] = set()

    def _add(atom: str) -> None:
        a = atom.strip()
        if a and a not in seen:
            seen.add(a)
            out.append(a)

    for e in effects:
        _add(str(e))
    if not out and tool_name:
        for e in TOOL_DEFAULT_EFFECTS.get(tool_name.strip().lower(), ()):
            _add(e)
    if action_kind:
        for e in KIND_TO_EFFECTS.get(action_kind.strip().lower(), ()):
            _add(e)
    if irreversible and not out:
        _add("mutates_system")
    return tuple(out)


@dataclass(frozen=True)
class TrajectoryStep:
    """One committed step in a session trajectory."""

    step: int
    tool: str
    label: str
    effects: tuple[str, ...]
    verdict: str = PROCEED

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "tool": self.tool,
            "label": self.label,
            "effects": list(self.effects),
            "verdict": self.verdict,
        }


@dataclass(frozen=True)
class TriggeredChain:
    """One compositional rule fired during forward world-model simulation."""

    rule: CompositionalRule
    antecedents: tuple[dict[str, Any], ...]  # provenance of every required atom

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule.id,
            "gives": self.rule.gives,
            "needs": list(self.rule.needs),
            "verdict": self.rule.verdict,
            "risk": self.rule.risk,
            "zh": self.rule.zh,
            "en": self.rule.en,
            "antecedents": list(self.antecedents),
        }


@dataclass(frozen=True)
class TrajectoryAssessment:
    """Result of forward-simulating a candidate step on top of the session's accumulated state."""

    verdict: str  # PROCEED | DISSUADE | REFUSE
    risk: float
    proposed_effects: tuple[str, ...]
    accumulated_before: tuple[str, ...]
    terminal_atoms: tuple[str, ...]
    triggered: tuple[TriggeredChain, ...]

    @property
    def blocked(self) -> bool:
        return self.verdict != PROCEED

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "blocked": self.blocked,
            "risk": round(self.risk, 3),
            "proposed_effects": list(self.proposed_effects),
            "accumulated_before": list(self.accumulated_before),
            "terminal_atoms": list(self.terminal_atoms),
            "triggered": [t.to_dict() for t in self.triggered],
        }


@dataclass
class WorldTrajectory:
    """Per-session world-model state accumulator with forward-chaining terminal-state simulation."""

    session_id: str = "default"
    steps: list[TrajectoryStep] = field(default_factory=list)
    _atoms: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    @property
    def accumulated_atoms(self) -> tuple[str, ...]:
        return tuple(self._atoms.keys())

    def reset(self) -> None:
        self.steps.clear()
        self._atoms.clear()

    def simulate(
        self,
        effects: tuple[str, ...] | list[str],
        *,
        tool: str = "",
        label: str = "",
    ) -> TrajectoryAssessment:
        """Forward-simulate ``accumulated_atoms ∪ effects`` without mutating session state."""
        proposed = tuple(dict.fromkeys(str(e).strip() for e in effects if str(e).strip()))
        before = tuple(self._atoms.keys())
        next_step_idx = len(self.steps) + 1

        # Build provenance map for accumulated + proposed atoms
        state_prov: dict[str, dict[str, Any]] = {}
        for atom, entries in self._atoms.items():
            # Record earliest source for clear causal lineage
            state_prov[atom] = dict(entries[0])
        for atom in proposed:
            if atom not in state_prov:
                state_prov[atom] = {
                    "atom": atom,
                    "step": next_step_idx,
                    "tool": tool,
                    "label": label,
                    "source": "proposed",
                }

        # Fixed-point forward-chaining causal closure
        fired_ids: set[str] = set()
        triggered: list[TriggeredChain] = []
        changed = True
        while changed:
            changed = False
            for rule in TRAJECTORY_RULES:
                if rule.id in fired_ids:
                    continue
                if any(a in state_prov for a in rule.lacks):
                    continue
                if all(a in state_prov for a in rule.needs):
                    fired_ids.add(rule.id)
                    antecedents = tuple(state_prov[a] for a in rule.needs)
                    triggered.append(TriggeredChain(rule=rule, antecedents=antecedents))
                    if rule.gives not in state_prov:
                        state_prov[rule.gives] = {
                            "atom": rule.gives,
                            "step": next_step_idx,
                            "tool": tool,
                            "label": rule.en,
                            "source": f"derived:{rule.id}",
                        }
                        changed = True

        # Only fail/escalate on rules that actually involve at least one newly proposed atom
        # or newly derived state (so prior already-accepted benign states don't spuriously re-trigger
        # on an empty no-op read). If proposed is empty, no new chain triggers.
        relevant: list[TriggeredChain] = []
        if proposed:
            proposed_set = set(proposed)
            derived_this_turn: set[str] = set()
            for tc in triggered:
                if any(a in proposed_set or a in derived_this_turn for a in tc.rule.needs):
                    relevant.append(tc)
                    derived_this_turn.add(tc.rule.gives)

        verdict = PROCEED
        risk = 0.0
        for tc in relevant:
            if _VERDICT_SEVERITY[tc.rule.verdict] > _VERDICT_SEVERITY[verdict]:
                verdict = tc.rule.verdict
            if tc.rule.risk > risk:
                risk = tc.rule.risk

        return TrajectoryAssessment(
            verdict=verdict,
            risk=risk,
            proposed_effects=proposed,
            accumulated_before=before,
            terminal_atoms=tuple(state_prov.keys()),
            triggered=tuple(relevant),
        )

    def commit(
        self,
        effects: tuple[str, ...] | list[str],
        *,
        tool: str = "",
        label: str = "",
        verdict: str = PROCEED,
    ) -> TrajectoryStep:
        """Record an executed step's real system effects into the session's world state."""
        clean = tuple(dict.fromkeys(str(e).strip() for e in effects if str(e).strip()))
        step_idx = len(self.steps) + 1
        entry = TrajectoryStep(step=step_idx, tool=tool, label=label, effects=clean, verdict=verdict)
        self.steps.append(entry)
        for atom in clean:
            self._atoms.setdefault(atom, []).append(
                {
                    "atom": atom,
                    "step": step_idx,
                    "tool": tool,
                    "label": label,
                    "source": "committed",
                }
            )
        return entry

    def snapshot(self) -> dict[str, Any]:
        """Serialize the current session trajectory for MCP / Studio / Playground inspection."""
        atoms_view = []
        for atom, entries in self._atoms.items():
            meta = EFFECT_ATOMS.get(atom, {"category": "custom", "zh": atom, "en": atom})
            atoms_view.append(
                {
                    "atom": atom,
                    "category": meta["category"],
                    "zh": meta["zh"],
                    "en": meta["en"],
                    "introduced_at_step": entries[0]["step"],
                    "tool": entries[0]["tool"],
                }
            )
        return {
            "session_id": self.session_id,
            "step_count": len(self.steps),
            "accumulated_atoms": atoms_view,
            "steps": [s.to_dict() for s in self.steps],
        }


__all__ = [
    "CompositionalRule",
    "EFFECT_ATOMS",
    "KIND_TO_EFFECTS",
    "TOOL_DEFAULT_EFFECTS",
    "TRAJECTORY_RULES",
    "TrajectoryAssessment",
    "TrajectoryStep",
    "TriggeredChain",
    "WorldTrajectory",
    "infer_effects",
]
