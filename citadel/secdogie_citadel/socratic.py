"""A Socratic quality gate: an application-layer review of an instruction.

Kept strictly at the application layer -- like a static-analysis / lint pass
over a request. `review` flags an instruction that is empty, self-contradictory,
a tight busy-poll, unattended posting, or an overlong unstructured pile, and
returns `revise` with a concrete suggestion; otherwise `accept`.

The Socratic step does not stop at the question: `revise` rewrites the
instruction the way each finding suggests (a poll becomes a backoff, unattended
posting gains an explicit confirmation step, a read-only-plus-change request is
sequenced with the change confirmed, an overlong pile is split into ordered
sub-goals), and `deliberate` runs review -> revise -> review until the
instruction is accepted. The checks are never loosened to get there: a revision
is accepted only because the revised text genuinely no longer has the problem.
Only what cannot be rewritten without guessing the user's intent (an empty
instruction, an opinion from an extra check) is handed back as `needs_input`.
It never touches OS security, never bypasses a permission gate, and never
removes a safeguard.

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
    r"\b(delete|remove|write|modify|overwrite|save|install|uninstall|format|drop|rm"
    r"|post|publish|submit|send|reply|comment|tweet)\b"  # posting/sending changes external state too
    r"|删除|修改|覆盖|写入|保存|安装|卸载|格式化|发帖|发布|发送|回复|评论|提交",
    re.IGNORECASE,
)
_READ_ONLY = re.compile(
    r"read[- ]?only|do\s*not\s*(modify|change|write|delete)|don'?t\s*(modify|change|write|delete)"
    r"|只读|不要(修改|改动|写|删除)|不改|别改",
    re.IGNORECASE,
)
# An explicit sequencing that resolves "read-only yet change": the read-only part
# first, the change only after explicit confirmation (what `revise` writes).
_RESOLVED_CHANGE = re.compile(
    r"only after the user explicitly confirms|明确确认后再",
    re.IGNORECASE,
)
# A fixed, tight poll: "every 1 second", "每 2 秒", or "poll ... forever".
_TIGHT_INTERVAL = re.compile(
    r"every\s*(\d+)\s*(ms|milliseconds?|seconds?|secs?|s)\b|每\s*(\d+)\s*(毫秒|秒)",
    re.IGNORECASE,
)
_BUSY_LOOP = re.compile(
    r"\b(poll|loop|retry)\b.*\b(forever|continuously|nonstop|constantly|indefinitely)\b"
    r"|(不停|一直|持续|不间断).*(轮询|循环|重试)|(轮询|循环|重试).*(不停|一直|持续|不间断)",
    re.IGNORECASE,
)

_MAX_LEN = 600  # a single instruction longer than this is likely several goals
_MAX_STEPS = 6  # "and then ... and then ..." past this should be decomposed

# Posting/sending content on someone's behalf, paired with an "unattended" /
# "don't ask" qualifier. Both must be present, so an ordinary "fill the form and
# click submit" instruction is NOT flagged -- only one that asks to publish/send
# without a human in the loop.
_PUBLISH_VERB = re.compile(
    r"\b(post|publish|submit|send|reply|comment|tweet|dm|message)\b"
    r"|发帖|发布|发送|回复|评论|提交|推送|群发",
    re.IGNORECASE,
)
_UNATTENDED = re.compile(
    r"automatically|unattended|autonomous(ly)?|silently|"
    r"without\s+(asking|confirmation|confirming|review|approval|a human)|"
    r"no\s+confirmation|on\s+(my|the user'?s)\s+behalf|"
    r"自动|自主|擅自|静默|不(询问|确认|经确认|经审核)|无需确认|替(我|用户)",
    re.IGNORECASE,
)


# Finding codes (Review.codes).
EMPTY = "empty"
CONTRADICTION = "contradiction"
UNATTENDED_POSTING = "unattended_posting"
POLLING = "polling"
OVERLONG = "overlong"
EXTRA = "extra"


@dataclass(frozen=True)
class Review:
    verdict: str  # "accept" | "revise"
    reasons: tuple[str, ...] = ()
    suggestion: str = ""
    codes: tuple[str, ...] = ()  # which checks fired (EMPTY, CONTRADICTION, ...)

    @property
    def accepted(self) -> bool:
        return self.verdict == "accept"


def _check_empty(text: str):
    if not text.strip():
        return ("empty instruction: nothing actionable", "State a concrete goal.")
    return None


def _check_contradiction(text: str):
    if _READ_ONLY.search(text) and _MUTATING.search(text) and not _RESOLVED_CHANGE.search(text):
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
            is_ms = unit == "ms" or unit.startswith("milli") or "毫秒" in unit
            is_seconds = not is_ms and ("秒" in unit or unit.startswith("sec") or unit == "s")
            tight = is_ms or (is_seconds and n <= 5)
    if tight or _BUSY_LOOP.search(text):
        return (
            "inefficient polling: a tight/endless poll wastes cycles",
            "Prefer an event-driven trigger, or exponential backoff, over a fixed short interval.",
        )
    return None


def _check_unattended_posting(text: str):
    if _PUBLISH_VERB.search(text) and _UNATTENDED.search(text):
        return (
            "unattended posting/sending: publishing or sending content on the "
            "user's behalf without a human in the loop",
            "Real-world posting/sending should go through explicit human "
            "confirmation, not run unattended.",
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
    codes: list[str] = []
    checks: list[tuple[str, Callable[[str], object]]] = [
        (EMPTY, _check_empty), (CONTRADICTION, _check_contradiction),
        (UNATTENDED_POSTING, _check_unattended_posting), (POLLING, _check_polling),
        (OVERLONG, _check_overlong),
    ]
    checks.extend((EXTRA, c) for c in (extra_checks or []))
    for code, check in checks:
        norm = _normalize(check(text))
        if norm is None:
            continue
        reason, suggestion = norm
        reasons.append(reason)
        codes.append(code)
        if suggestion:
            suggestions.append(suggestion)
    if reasons:
        return Review("revise", tuple(reasons), " ".join(suggestions), tuple(codes))
    return Review("accept")


def record_review(journal, instruction: str, result: Review, *, goal_id: str | None = None,
                  round_no: int | None = None) -> dict:
    """Append the review to a journal as a signed `socratic` event (audit trail)."""
    body = {
        "instruction": instruction,
        "verdict": result.verdict,
        "reasons": list(result.reasons),
        "suggestion": result.suggestion,
        "codes": list(result.codes),
    }
    if goal_id is not None:
        body["goal_id"] = goal_id
    if round_no is not None:
        body["round"] = round_no
    return journal.append("socratic", body)


# ---------------------------------------------------------------------------
# Revise: rewrite the instruction the way each finding suggests
# ---------------------------------------------------------------------------

_CJK = re.compile(r"[\u4e00-\u9fff]")
_ENDLESS = re.compile(r"\b(forever|continuously|nonstop|constantly|indefinitely)\b|不停地?|一直|持续地?|不间断地?",
                      re.IGNORECASE)
_STEP_SPLIT = re.compile(r"\band then\b|再然后|然后|接着", re.IGNORECASE)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;。！？；])\s*|\n+")


def _say(text: str, en: str, zh: str) -> str:
    return zh if _CJK.search(text) else en


def _tidy(text: str) -> str:
    return re.sub(r"[ \t]{2,}", " ", text).strip(" ,，")


def _append(text: str, clause: str) -> str:
    base = text.rstrip()
    if base and base[-1] not in ".!?。！？;；":
        base += "。" if _CJK.search(base) else "."
    return f"{base} {clause}".strip()


def _fix_polling(text: str) -> str:
    text = _TIGHT_INTERVAL.sub(lambda m: _say(m.group(0), "with backoff", "按退避间隔"), text)
    text = _tidy(_ENDLESS.sub("", text))
    return _append(text, _say(
        text,
        "Check with backoff: start at 30 seconds, at most 10 minutes apart, and stop after 1 hour.",
        "以退避方式检查:从 30 秒起,间隔最多 10 分钟,1 小时后停止。",
    ))


def _fix_unattended(text: str) -> str:
    text = _tidy(_UNATTENDED.sub("", text))
    return _append(text, _say(
        text,
        "Before posting or sending anything, ask the user to confirm it.",
        "发布或发送前,先请用户确认。",
    ))


def _fix_contradiction(text: str) -> str:
    return _append(text, _say(
        text,
        "Do the read-only part first; make any change only after the user explicitly confirms it.",
        "先完成只读部分;任何修改都在用户明确确认后再做。",
    ))


def _split_steps(text: str) -> tuple[str, ...]:
    parts = [p.strip(" ,，.。;；") for p in _STEP_SPLIT.split(text)]
    parts = [p for p in parts if p]
    if len(parts) >= 2 and all(len(p) <= _MAX_LEN for p in parts):
        return tuple(parts)
    # No step markers (or still-long steps): group sentences into chunks.
    chunks: list[str] = []
    current = ""
    for sentence in (x.strip() for x in _SENTENCE_SPLIT.split(text)):
        if not sentence:
            continue
        if current and len(current) + 1 + len(sentence) > _MAX_LEN:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    if len(chunks) >= 2 and all(len(c) <= _MAX_LEN for c in chunks):
        return tuple(chunks)
    return ()


@dataclass(frozen=True)
class Revision:
    """The result of rewriting an instruction. `text` is the revised instruction
    (None when nothing could be rewritten); `subgoals` is set when the fix is to
    split it; `unresolved` lists the findings that need the operator."""

    text: str | None
    subgoals: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()


def revise(instruction: str, result: Review) -> Revision:
    """Rewrite `instruction` according to each finding in `result`."""
    text = instruction or ""
    codes = set(result.codes)
    unresolved = tuple(c for c in result.codes if c in (EMPTY, EXTRA))
    if EMPTY in codes:
        return Revision(None, (), unresolved)
    changed = text
    if POLLING in codes:
        changed = _fix_polling(changed)
    if UNATTENDED_POSTING in codes:
        changed = _fix_unattended(changed)
    if CONTRADICTION in codes:
        changed = _fix_contradiction(changed)
    if OVERLONG in codes:
        steps = _split_steps(changed)
        if steps:
            return Revision(None, steps, unresolved)
        unresolved = (*unresolved, OVERLONG)
    if changed == text:
        return Revision(None, (), unresolved)
    return Revision(changed, (), unresolved)


@dataclass(frozen=True)
class Round:
    text: str
    review: Review


@dataclass(frozen=True)
class Deliberation:
    """The outcome of review -> revise -> review.

    * ``accept``: run ``text`` (the original, or its accepted revision).
    * ``decompose``: split into ``subgoals`` (each is deliberated when it runs).
    * ``needs_input``: nothing more can be rewritten without the operator;
      ``reasons`` says what is missing."""

    outcome: str
    text: str
    subgoals: tuple[str, ...] = ()
    rounds: tuple[Round, ...] = ()
    reasons: tuple[str, ...] = ()

    @property
    def revised(self) -> bool:
        return len(self.rounds) > 1


def deliberate(instruction: str, *, max_rounds: int = 3,
               extra_checks: list[Callable[[str], object]] | None = None) -> Deliberation:
    """Review, and while the review asks for changes, revise and review again."""
    text = instruction or ""
    rounds: list[Round] = []
    for _ in range(max(1, max_rounds)):
        result = review(text, extra_checks=extra_checks)
        rounds.append(Round(text, result))
        if result.accepted:
            return Deliberation("accept", text, (), tuple(rounds))
        rev = revise(text, result)
        if rev.subgoals:
            return Deliberation("decompose", text, rev.subgoals, tuple(rounds))
        if rev.text is None or rev.text == text:
            return Deliberation("needs_input", text, (), tuple(rounds), result.reasons)
        text = rev.text
    final = review(text, extra_checks=extra_checks)
    rounds.append(Round(text, final))
    if final.accepted:
        return Deliberation("accept", text, (), tuple(rounds))
    return Deliberation("needs_input", text, (), tuple(rounds), final.reasons)
