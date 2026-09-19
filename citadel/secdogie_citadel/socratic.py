"""A Socratic quality gate: an application-layer review of an instruction.

This is the "refusal right" from the spec, kept strictly at the application
layer -- like a static-analysis / lint pass over a request. It flags an
instruction that is empty, self-contradictory, a tight busy-poll, or an overlong
unstructured pile, and returns `revise` with a concrete suggestion; otherwise
`accept`. It NEVER touches OS security, never bypasses a permission gate, and
never removes a safeguard -- it only proposes a better-formed instruction, which
a human (or the supervised loop's existing gates) still acts on.

Dependency-free and deterministic (no model call here). A caller may pass
`extra_checks` -- e.g. an LLM-backed critic -- to add richer opinions; each is a
callable(instruction) -> None | reason | (reason, suggestion).
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

# Verbs that change state, and phrases that assert read-only intent. A request
# that asserts read-only AND asks for a mutation is self-contradictory.
_MUTATING = re.compile(
    r"\b(delete|remove|write|modify|overwrite|save|install|uninstall|format|drop|rm)\b"
    r"|删除|修改|覆盖|写入|保存|安装|卸载|格式化",
    re.IGNORECASE,
)
_READ_ONLY = re.compile(
    r"read[- ]?only|do\s*not\s*(modify|change|write|delete)|don'?t\s*(modify|change|write|delete)"
    r"|只读|不要(修改|改动|写|删除)|不改|别改",
    re.IGNORECASE,
)
# A fixed, tight poll: "every 1 second", "每 2 秒", or "poll ... forever".
_TIGHT_INTERVAL = re.compile(
    r"every\s*(\d+)\s*(ms|millisecond|second|sec|s)\b|每\s*(\d+)\s*(毫秒|秒)",
    re.IGNORECASE,
)
_BUSY_LOOP = re.compile(
    r"\b(poll|loop|retry)\b.*\b(forever|continuously|nonstop|constantly|indefinitely)\b"
    r"|(不停|一直|持续|不间断).*(轮询|循环|重试)|(轮询|循环|重试).*(不停|一直|持续|不间断)",
    re.IGNORECASE,
)

_MAX_LEN = 600  # a single instruction longer than this is likely several goals
_MAX_STEPS = 6  # "and then ... and then ..." past this should be decomposed


@dataclass(frozen=True)
class Review:
    verdict: str  # "accept" | "revise"
    reasons: tuple[str, ...] = ()
    suggestion: str = ""

    @property
    def accepted(self) -> bool:
        return self.verdict == "accept"


def _check_empty(text: str):
    if not text.strip():
        return ("empty instruction: nothing actionable", "State a concrete goal.")
    return None


def _check_contradiction(text: str):
    if _READ_ONLY.search(text) and _MUTATING.search(text):
        return (
            "self-contradiction: asserts read-only yet asks for a state change",
            "Split it: keep the read-only inspection separate from any change, and "
            "confirm the change explicitly.",
        )
    return None


def _check_polling(text: str):
    m = _TIGHT_INTERVAL.search(text)
    tight = False
    if m:
        num = next((g for g in m.groups() if g and g.isdigit()), None)
        unit = (m.group(2) or m.group(4) or "").lower()
        if num is not None:
            n = int(num)
            is_ms = "ms" in unit or "毫秒" in unit
            is_seconds = "秒" in unit or unit in ("second", "sec", "s")
            tight = is_ms or (is_seconds and n <= 5)
    if tight or _BUSY_LOOP.search(text):
        return (
            "inefficient polling: a tight/endless poll wastes cycles",
            "Prefer an event-driven trigger, or exponential backoff, over a fixed short interval.",
        )
    return None


def _check_overlong(text: str):
    steps = len(re.findall(r"\band then\b|然后|接着|再然后", text, re.IGNORECASE))
    if len(text) > _MAX_LEN or steps > _MAX_STEPS:
        return (
            "overlong / unstructured: many steps in one instruction",
            "Decompose into a few named sub-goals with dependencies (a goal DAG).",
        )
    return None


def _normalize(result) -> tuple[str, str] | None:
    if result is None:
        return None
    if isinstance(result, str):
        return (result, "")
    reason, suggestion = result
    return (str(reason), str(suggestion or ""))


def review(instruction: str, *, extra_checks: list[Callable[[str], object]] | None = None) -> Review:
    """Review one instruction. Returns accept, or revise with reasons + a
    combined suggestion."""
    text = instruction or ""
    reasons: list[str] = []
    suggestions: list[str] = []
    checks: list[Callable[[str], object]] = [
        _check_empty, _check_contradiction, _check_polling, _check_overlong
    ]
    checks.extend(extra_checks or [])
    for check in checks:
        norm = _normalize(check(text))
        if norm is None:
            continue
        reason, suggestion = norm
        reasons.append(reason)
        if suggestion:
            suggestions.append(suggestion)
    if reasons:
        return Review("revise", tuple(reasons), " ".join(suggestions))
    return Review("accept")


def record_review(journal, instruction: str, result: Review) -> dict:
    """Append the review to a journal as a signed `socratic` event (audit trail)."""
    return journal.append(
        "socratic",
        {
            "instruction": instruction,
            "verdict": result.verdict,
            "reasons": list(result.reasons),
            "suggestion": result.suggestion,
        },
    )
