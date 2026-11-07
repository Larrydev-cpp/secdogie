"""Presentation logic, Qt-free: labels, rows and validation the window shows.

The window's text is Chinese; identifiers and logs stay English. Pages that
later slices build are listed now, each saying which slice brings it, so the
skeleton shows the whole shape without pretending any of it works yet.
"""
from __future__ import annotations

from dataclasses import dataclass

from .controller import EXITED, RUNNING, STARTING, STOPPED, Snapshot
from .profile import MIN_PASSPHRASE, ROLES


@dataclass(frozen=True)
class NavItem:
    key: str
    label: str
    planned: str = ""  # the slice that builds this page; empty when it works now


NAV = (
    NavItem("node", "节点"),
    NavItem("dialogue", "对话 / Gate 2", "G2"),
    NavItem("mesh", "网格", "G3"),
    NavItem("discover", "发现", "G3"),
    NavItem("rules", "规则", "G4"),
    NavItem("decision", "决策模型", "G4"),
    NavItem("ingest", "采集", "G5"),
    NavItem("incidents", "事件 / 锁定", "G6"),
    NavItem("keys", "密钥 / 租约", "G6"),
)

ROLE_LABELS = {
    "forward": "转发（forward）：只转发，不做决定",
    "serve": "决策（serve）：接请求、选路、裁决",
    "model": "模型（model）：用租约密钥作答",
    "ingest": "采集（ingest）：按 origin 抓取并提取结构",
    "relay": "中继（relay）：为打不通的节点转发密文",
}
assert set(ROLE_LABELS) == set(ROLES)

_STATE_TEXT = {STOPPED: "未启动", STARTING: "启动中…", RUNNING: "运行中"}


def planned_text(item: NavItem) -> str:
    return f"「{item.label}」将在切片 {item.planned} 中实现。见 docs/PLAN-STUDIO-INGEST.zh.md 第 9 节。"


def first_run_problems(passphrase: str, confirm: str, roles) -> list[str]:
    problems = []
    if len(passphrase) < MIN_PASSPHRASE:
        problems.append(f"操作员口令至少 {MIN_PASSPHRASE} 个字符")
    if passphrase != confirm:
        problems.append("两次输入的口令不一致")
    if not roles:
        problems.append("至少选择一个角色")
    return problems


def node_state_text(s: Snapshot) -> str:
    if s.node_state == EXITED:
        return f"已退出（代码 {s.returncode}）"
    return _STATE_TEXT.get(s.node_state, s.node_state)


def node_rows(s: Snapshot) -> list[tuple[str, str]]:
    rows = [
        ("状态", node_state_text(s)),
        ("节点 DID", s.node_did),
        ("监听", s.listen),
        ("角色", "、".join(s.roles) or "—"),
        ("App DID", s.app_did),
        ("操作员 DID", s.operator_did),
    ]
    if s.pid is not None and s.node_state in (STARTING, RUNNING):
        rows.append(("进程", str(s.pid)))
    if s.link:
        rows.append(("连接检查", link_text(s.link)))
    return rows


def link_text(reply: str) -> str:
    """The link check asks the node to retract a memory it does not hold; the
    expected refusal is the success signal, so say what it proves."""
    if reply.startswith("refused: no active memory"):
        return "已连通：双向 DID 认证、对话会话、节点处理均正常（探测请求按预期被拒绝）"
    return f"节点回复：{reply}"


def can_start(s: Snapshot) -> bool:
    return s.initialized and not s.busy and s.node_state in (STOPPED, EXITED)


def can_stop(s: Snapshot) -> bool:
    return s.node_state in (STARTING, RUNNING)


def can_check_link(s: Snapshot) -> bool:
    return s.node_state == RUNNING and not s.busy
