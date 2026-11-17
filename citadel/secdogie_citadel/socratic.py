"""Gate 1's review of how a request is put (an application-layer lint), before the cortex reads what it means.

It only looks at the shape of the instruction: is there anything in it at all, and is it so long that it is
really several goals? What the request *means* (what it would change, whom it would touch, whether to go
ahead) is the cortex's to understand (``cortex/``, ``deliberate.py``); nothing here reads words.

It returns ``revise`` with a suggestion, or ``accept``. It never touches OS security, never bypasses a
permission gate and never removes a safeguard. A caller may pass ``extra_checks`` (the operator's own signed
rules): each is a callable(instruction) -> None | reason | (reason, suggestion).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

_MAX_LEN = 600  # a single instruction longer than this is likely several goals
_MAX_STEPS = 6  # kept for the rules layer, which may only lower it


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


def _check_overlong(text: str):
    if len(text) > _MAX_LEN:
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
    checks: list[Callable[[str], object]] = [_check_empty, _check_overlong]
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
