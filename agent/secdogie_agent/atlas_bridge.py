"""Compliant read-only Atlas perception bridge & platform privilege guidance (V1.2 Phase 4).

Connects the pure-Python twin of ``native/atlas/`` (``secdogie_agent.atlas``)
to the live agent loop and Studio Talk's "Desktop Agent" switch without ever
crossing the read-only safety boundary:

1. **Read-only memory & AX only**: ``enforce_read_only()`` refuses every write
   bit (``PROCESS_VM_WRITE``, ``PROCESS_VM_OPERATION``, ``PROCESS_CREATE_THREAD``,
   ``PROCESS_SET_INFORMATION``) and ``PROCESS_ALL_ACCESS`` outright.
2. **Honest privilege wall (no PPL / kernel bypass)**: ``allow_inspect()``
   refuses PPL protected processes (``lsass.exe``, ``csrss.exe``,
   ``MsMpEng.exe``, etc.), ``NT SERVICE\\TrustedInstaller``, and higher
   integrity targets. No token theft, no kernel HID, no anti-EDR tricks.
3. **Citadel gate before action**: ``gate_action()`` routes any desktop-agent
   action through ``secdogie_citadel.action_gate`` (and optionally ``deliberate``)
   before execution is allowed.
4. **Transparent context echo**: ``AtlasContext.to_echo_dict()`` exposes the
   exact foreground app, read-only status, fused AX/memory summary, or honest
   refusal reason so Studio Talk always shows what the agent perceived.
"""
from __future__ import annotations

import platform
import time
from dataclasses import dataclass, field

from . import atlas

SAFE_READ_ONLY_MASK = atlas.PROCESS_VM_READ | atlas.PROCESS_QUERY_LIMITED_INFORMATION


def privilege_guidance(platform_name: str | None = None, *, lang: str = "zh") -> dict:
    """Honest, platform-specific read-only authorization guidance (never bypasses OS protections)."""
    plat = (platform_name or platform.system() or "").strip().lower()
    en = lang == "en"
    if plat.startswith(("win", "nt")):
        return {
            "platform": "windows",
            "mode": "user-mode-read-only",
            "permissions": [
                "UI Automation (UIA) read-only tree",
                "OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_LIMITED_INFORMATION)",
            ],
            "title": "Windows User-Mode Read-Only Perception" if en else "Windows 用户态只读感知",
            "summary": (
                "Reads the foreground window's UIA tree and user-owned process strings "
                "using strictly read-only handles (VM_READ + QUERY_LIMITED)."
                if en else
                "仅通过 UIA 无障碍树与只读进程句柄（VM_READ + QUERY_LIMITED）读取当前前台窗口上下文。"
            ),
            "steps": (
                [
                    "Run Studio under your standard user account (no kernel driver required).",
                    "Only inspect user-level apps you actively open; elevated apps require matching privilege.",
                    "Protected processes (PPL: lsass.exe, csrss.exe, Defender), TrustedInstaller, and kernel memory are hard-refused.",
                ]
                if en else
                [
                    "以当前普通用户权限运行 Studio，无需安装内核驱动或修改系统安全策略。",
                    "仅感知您正在操作的同权限前台应用；更高完整性级别进程不会被强行提权读取。",
                    "PPL 受保护进程（lsass.exe、csrss.exe、杀毒引擎）、TrustedInstaller 与内核内存均硬编码拒绝，绝不绕过。",
                ]
            ),
            "refusedBoundary": (
                "Hard-coded refusal: PPL / lsass / csrss / TrustedInstaller / WriteProcessMemory / kernel HID."
                if en else
                "硬编码红线：严禁绕过 PPL、严禁读取 lsass/csrss、严禁 TrustedInstaller 令牌窃取、严禁写入进程内存或内核 HID。"
            ),
        }
    if plat in ("darwin", "macos", "osx"):
        return {
            "platform": "macos",
            "mode": "accessibility-read-only",
            "permissions": ["AXIsProcessTrusted (Accessibility)", "Read-only AXUIElement traversal"],
            "title": "macOS Accessibility Read-Only Authorization" if en else "macOS 辅助功能只读授权",
            "summary": (
                "Reads the foreground app's Accessibility (AX) element hierarchy after explicit user consent."
                if en else
                "经您明确授权「辅助功能」后，仅只读遍历前台应用的无障碍树（AXUIElement），不截屏、不注入。"
            ),
            "steps": (
                [
                    "Open System Settings -> Privacy & Security -> Accessibility.",
                    "Enable SecDogie Studio so AXIsProcessTrusted() returns true.",
                    "SIP-protected system processes and memory injection remain strictly off-limits.",
                ]
                if en else
                [
                    "打开「系统设置 → 隐私与安全性 → 辅助功能」。",
                    "勾选允许 SecDogie Studio，使系统授权 AXIsProcessTrusted()。",
                    "SIP 受保护系统进程与任何内存写入/注入均严格禁止。",
                ]
            ),
            "refusedBoundary": (
                "Hard-coded refusal: SIP bypass, keystroke tapping, or process memory writes."
                if en else
                "硬编码红线：严禁绕过 SIP、严禁键盘全局监听、严禁写入或注入目标进程。"
            ),
        }
    return {
        "platform": "linux",
        "mode": "atspi-read-only",
        "permissions": ["AT-SPI2 Accessibility", "Same-UID read-only inspection"],
        "title": "Linux AT-SPI2 Read-Only Perception" if en else "Linux AT-SPI2 只读感知",
        "summary": (
            "Reads foreground controls via AT-SPI2 and same-UID read-only buffers without screen capture."
            if en else
            "通过 AT-SPI2 无障碍接口与同用户只读视图读取前台上下文，不截屏、不提权。"
        ),
        "steps": (
            [
                "Ensure AT-SPI2 accessibility bus is active in your desktop session.",
                "Inspection is strictly limited to same-UID processes; root/kernel memory is refused.",
            ]
            if en else
            [
                "确保当前桌面会话已启用 AT-SPI2 无障碍总线。",
                "仅限当前同 UID 用户进程只读感知，绝不尝试提权或读取系统敏感进程。",
            ]
        ),
        "refusedBoundary": (
            "Hard-coded refusal: ptrace writes, root escalation, or /dev/input eavesdropping."
            if en else
            "硬编码红线：严禁 ptrace 内存写入、严禁提权、严禁监听 /dev/input。"
        ),
    }


@dataclass(frozen=True)
class AtlasContext:
    pid: int = 0
    image: str = ""
    window_title: str = ""
    ax_summary: tuple[str, ...] = ()
    memory_snippets: tuple[str, ...] = ()
    fused_count: int = 0
    read_only: bool = True
    allowed: bool = True
    refused_code: str = ""
    refused_reason: str = ""
    captured_at: float = 0.0

    def to_echo_dict(self, *, lang: str = "zh") -> dict:
        en = lang == "en"
        if not self.allowed:
            status = (
                f"Refused ({self.refused_code}): {self.refused_reason}"
                if en else
                f"已按合规红线拒绝（{self.refused_code}）：{self.refused_reason}"
            )
        else:
            app = self.window_title or self.image or ("Foreground app" if en else "前台应用")
            status = (
                f"Read-only · {app} · {len(self.ax_summary)} AX / {len(self.memory_snippets)} mem"
                if en else
                f"只读感知 · {app} · {len(self.ax_summary)} 项无障碍元素 / {len(self.memory_snippets)} 条只读内存文本"
            )
        return {
            "pid": self.pid,
            "image": self.image,
            "windowTitle": self.window_title,
            "axSummary": list(self.ax_summary),
            "memorySnippets": list(self.memory_snippets),
            "fusedCount": self.fused_count,
            "readOnly": self.read_only,
            "allowed": self.allowed,
            "refusedCode": self.refused_code,
            "refusedReason": self.refused_reason,
            "statusLine": status,
            "capturedAt": self.captured_at,
        }


@dataclass(frozen=True)
class ActionGateVerdict:
    allowed: bool
    needs_signature: bool
    verdict: str
    reason: str
    gate_reasons: tuple[str, ...] = field(default_factory=tuple)


class AtlasBridge:
    """Live compliant read-only bridge over ``secdogie_agent.atlas``."""

    def __init__(self, *, platform_name: str | None = None, clock=time.time):
        self.platform_name = platform_name or platform.system()
        self._clock = clock
        self._last_context: AtlasContext | None = None
        self._last_tree: list[atlas.ControlNode] = []

    @property
    def last_context(self) -> AtlasContext | None:
        return self._last_context

    def guidance(self, *, lang: str = "zh") -> dict:
        return privilege_guidance(self.platform_name, lang=lang)

    def refuse_trusted_installer(self) -> atlas.PrivilegeResult:
        return atlas.try_impersonate_trusted_installer()

    def refuse_edr_evasion(self) -> atlas.PrivilegeResult:
        return atlas.try_edr_evasion()

    def inspect_foreground(
        self,
        self_tok: atlas.TokenSnapshot,
        target_tok: atlas.TokenSnapshot,
        *,
        window_title: str = "",
        ax_nodes: list[atlas.ControlNode] | tuple[atlas.ControlNode, ...] = (),
        raw_mem: bytes = b"",
        desired_access: int = SAFE_READ_ONLY_MASK,
        max_items: int = 12,
    ) -> AtlasContext:
        """Perceive foreground process strictly inside the read-only + privilege wall.

        Always checks ``enforce_read_only(desired_access)`` AND
        ``allow_inspect(self_tok, target_tok)`` BEFORE extracting any AX or memory text.
        """
        now = float(self._clock())
        access = atlas.enforce_read_only(desired_access)
        if access.refused:
            ctx = AtlasContext(
                pid=target_tok.pid,
                image=target_tok.image,
                window_title=window_title,
                read_only=True,
                allowed=False,
                refused_code=access.code,
                refused_reason=(
                    "Write/mutation process access bits or PROCESS_ALL_ACCESS are strictly refused."
                ),
                captured_at=now,
            )
            self._last_context = ctx
            return ctx

        priv = atlas.allow_inspect(self_tok, target_tok)
        if not priv.ok:
            ctx = AtlasContext(
                pid=target_tok.pid,
                image=target_tok.image,
                window_title=window_title,
                read_only=True,
                allowed=False,
                refused_code=priv.code,
                refused_reason=priv.detail,
                captured_at=now,
            )
            self._last_context = ctx
            return ctx

        kept_tree = atlas.keep_tree(list(ax_nodes), self._last_tree)
        self._last_tree = kept_tree
        hits = atlas.extract_utf16le(raw_mem, base=0, min_len=4) if raw_mem else []
        fused = atlas.fuse_tree(kept_tree, hits, max_memory_only=max_items)
        ax_summary = tuple(
            f"{n.role}:{n.name}" if n.role else n.name
            for n in atlas.flatten(kept_tree)
            if n.name
        )[:max_items]
        mem_snippets = tuple(h.text for h in hits[:max_items])
        ctx = AtlasContext(
            pid=target_tok.pid,
            image=target_tok.image,
            window_title=window_title,
            ax_summary=ax_summary,
            memory_snippets=mem_snippets,
            fused_count=len(fused),
            read_only=True,
            allowed=True,
            captured_at=now,
        )
        self._last_context = ctx
        return ctx

    def gate_action(
        self,
        intent_text: str,
        planned_action,
        *,
        gate_ctx=None,
        cortex=None,
    ) -> ActionGateVerdict:
        """Run citadel deliberation + 14-check action_gate before executing any desktop action."""
        from secdogie_citadel.action_gate import (
            ALLOW,
            UNAUTHORIZED_ACTION,
            GateContext,
        )
        from secdogie_citadel.action_gate import gate as run_action_gate
        from secdogie_citadel.deliberate import deliberate
        from secdogie_citadel.rules import GateRules

        rules = GateRules()
        review = rules.review(intent_text)
        thought = deliberate(intent_text, review, cortex=cortex)
        blocking = ("refuse", "dissuade", "support", "clarify") if cortex is not None else ("refuse", "dissuade", "support")
        if thought.verdict in blocking:
            return ActionGateVerdict(
                allowed=False,
                needs_signature=False,
                verdict=thought.verdict,
                reason=thought.says or f"citadel blocked ({thought.verdict})",
            )

        ctx = gate_ctx if gate_ctx is not None else GateContext(require_authorization=True, require_intent=True)
        decision = run_action_gate(planned_action, ctx)
        if decision.verdict == ALLOW:
            return ActionGateVerdict(
                allowed=True,
                needs_signature=False,
                verdict="allow",
                reason="passed citadel action_gate",
            )
        needs_sig = UNAUTHORIZED_ACTION in decision.findings
        return ActionGateVerdict(
            allowed=False,
            needs_signature=needs_sig,
            verdict="require_signature" if needs_sig else decision.verdict,
            reason=decision.reason or "; ".join(decision.findings) or "blocked by citadel action_gate",
            gate_reasons=tuple(decision.findings),
        )


__all__ = [
    "ActionGateVerdict",
    "AtlasBridge",
    "AtlasContext",
    "SAFE_READ_ONLY_MASK",
    "privilege_guidance",
]
