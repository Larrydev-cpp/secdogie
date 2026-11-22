"""Awake observational learning: watching an external model handle requests safely.

The cortex never asks an external model to act as a tutor. It watches the model's normal output and
turns usable readings into lessons, subject to three guards:

1. **No contamination**: frozen exam texts and empty/malformed readings are dropped before touching the
   ledger or synapses.
2. **Confident-wrong danger enters via a trust region**: when safety is at stake (``danger=True``), a
   large disagreement gap never causes the signal to be skipped; instead, the update uses a bounded
   trust-region step clip with ``DANGER_WEIGHT = 1.0`` (no multiplier explosion).
3. **Disputed memories & reply grounding**: non-danger high-gap disagreements or replies ungrounded in
   the request are stripped of any danger multiplier, marked ``disputed=True`` with a decaying
   ``priority_trace``, and ungrounded replies are never echoed back verbatim in emergency branches.

No regular expressions or word-table libraries are used here.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from . import exam as E
from . import questions as Q
from .curriculum import Lesson, defaults
from .familiar import _wide
from .senses import clean

# Unified single source of truth for observational learning constants.
DANGER_WEIGHT = 1.0  # reset from 3.0; safety enters via trust-region clipping, not weight explosion
MAX_GAP = 0.05  # non-danger confidence gap above which disagreements are triaged as disputed
TRUST_REGION_CLIP = 0.25  # L2 gradient clip for danger=True trust-region updates
TRUST_REGION_STEP_SCALE = 0.5  # conservative lr factor for danger=True awake updates
DISPUTED_WEIGHT = 1.0
DISPUTED_INITIAL_TRACE = 1.0
DISPUTED_DECAY = 0.7
DISPUTED_EVICT_THRESHOLD = 0.15
MIN_GROUND_OVERLAP = 0.08

LEARN = "learn"
DISPUTED = "disputed"
DROP = "drop"

SAFETY_QUESTIONS = ("self", "others", "seat")
_CAUTION_ORDER = {
    "self": ("no", "yes"),
    "others": ("self", "fine", "unsure", "words", "harm"),
    "seat": ("nobody", "no", "unsure", "yes"),
}
_EN_TRIVIAL = frozenset({
    "a", "an", "the", "is", "am", "are", "was", "were", "be", "to", "of", "in", "on", "at",
    "it", "this", "that", "for", "and", "or", "my", "me", "i", "you", "we", "they", "do", "can",
})
_ZH_TRIVIAL = frozenset({"的", "了", "吗", "呢", "吧", "啊", "哦", "嗯", "我", "你", "他", "她", "它", "这", "那", "是", "在"})


@lru_cache(maxsize=4)
def exam_texts(path=E.EXAM) -> frozenset[str]:
    """The frozen exam sentences, degraded cleanly to an empty set if unavailable."""
    try:
        return frozenset(item.text.strip() for item in E.load(path) if item.text.strip())
    except Exception:
        return frozenset()


def _grounded_words(text: str) -> set[str]:
    """Pure-character lexical units used only to verify whether a reply stays grounded in a request."""
    s = clean(text)
    tokens: set[str] = set()
    wide_seq: list[str] = []
    run: list[str] = []

    def _flush_run() -> None:
        if run:
            word = "".join(run)
            if len(word) >= 2 and word not in _EN_TRIVIAL:
                tokens.add(word)
            run.clear()

    def _flush_wide() -> None:
        if wide_seq:
            for ch in wide_seq:
                if ch not in _ZH_TRIVIAL:
                    tokens.add(ch)
            for i in range(len(wide_seq) - 1):
                tokens.add(wide_seq[i] + wide_seq[i + 1])
            wide_seq.clear()

    for ch in s + " ":
        if _wide(ch):
            _flush_run()
            wide_seq.append(ch)
        elif ch.isalnum():
            _flush_wide()
            run.append(ch)
        else:
            _flush_run()
            _flush_wide()
    return tokens


def _overlap(request: str, reply: str) -> float:
    """Overlap ratio (0..1) between meaningful units of ``request`` and ``reply``."""
    if not request or not reply or not reply.strip():
        return 0.0
    req = _grounded_words(request)
    rep = _grounded_words(reply)
    if not req or not rep:
        return 0.0
    shared = len(req & rep)
    denom = max(1, min(len(req), len(rep)))
    return shared / denom


def is_reply_grounded(
    request: str,
    reply: str,
    *,
    min_overlap: float = MIN_GROUND_OVERLAP,
    is_support: bool = False,
) -> bool:
    """True when ``reply`` is non-empty and shares grounded content/script with ``request``."""
    rep = (reply or "").strip()
    req = (request or "").strip()
    if not rep or not req:
        return False
    req_zh = any(_wide(ch) for ch in req)
    rep_zh = any(_wide(ch) for ch in rep)
    ov = _overlap(req, rep)
    if ov >= min_overlap:
        return True
    if is_support:
        return False
    # Allow brief same-script conversational acknowledgments only if they share script and stay short
    if req_zh == rep_zh and len(rep) <= 24 and len(_grounded_words(req)) <= 1:
        return True
    return False


def safe_support_reply(lang: str) -> str:
    """Grounded, calm fallback reply when an emergency/support reply fails grounding."""
    if lang == "zh":
        return "我先停下来陪着你。如果你现在正处在危险或非常难受的时刻，请联系身边信任的人或当地紧急援助热线，不用独自扛着。"
    return "I am pausing everything to stay with you. If you are in danger or distress right now, please reach out to someone you trust or local emergency support—you do not have to carry this alone."


def _is_danger(answers: dict, u) -> bool:
    if answers.get("self") == "yes" or answers.get("others") == "harm" or answers.get("seat") == "yes":
        return True
    if "self" in u.answers and u.answer("self") == "yes":
        return True
    if "others" in u.answers and u.answer("others") == "harm":
        return True
    return False


def _teacher_less_cautious(u, full: dict) -> bool:
    """True if the cortex confidently flags safety risk while the external model downplays it."""
    for qid in SAFETY_QUESTIONS:
        if qid not in full or qid not in u.answers:
            continue
        order = _CAUTION_ORDER[qid]
        mine, sure = u.answers[qid][0], float(u.answers[qid][1])
        theirs = full[qid]
        if sure >= 0.75 and order.index(mine) > order.index(theirs):
            return True
    return False


def _confidence_gap(u, full: dict) -> float:
    """Maximum probability gap between what the cortex predicted and the external model's choice."""
    gap = 0.0
    for qid, want in full.items():
        if qid not in u.answers:
            continue
        q = Q.BY_ID[qid]
        probs = u.answers[qid][2]
        mine_p = float(u.answers[qid][1])
        want_p = float(probs[q.index(want)]) if want in q.choices else 0.0
        if u.answers[qid][0] != want:
            gap = max(gap, max(0.0, mine_p - want_p))
    return gap


@dataclass(frozen=True)
class ObserveResult:
    action: str  # LEARN | DISPUTED | DROP
    lesson: Lesson | None
    answers: dict
    changed: tuple[str, ...]
    surprise: float
    danger: bool
    disputed: bool
    reply_grounded: bool
    safe_reply: str
    trust_clip: float | None = None
    step_scale: float = 1.0
    reason: str = ""


def observe_thinker(
    cortex,
    text: str,
    context: tuple[str, ...],
    u,
    theirs: dict | None,
    reply: str = "",
    *,
    exam_path=E.EXAM,
) -> ObserveResult:
    """Inspect an external model's output and decide whether to learn, triage as disputed, or drop."""
    clean_text = (text or "").strip()
    if not clean_text or not isinstance(theirs, dict) or not theirs:
        return ObserveResult(DROP, None, {}, (), 0.0, False, False, False, "", reason="empty")

    kind = theirs.get("kind")
    if kind not in Q.KINDS:
        return ObserveResult(DROP, None, {}, (), 0.0, False, False, False, "", reason="invalid-kind")

    if clean_text in exam_texts(exam_path):
        return ObserveResult(DROP, None, {}, (), 0.0, False, False, False, "", reason="exam-item")

    filtered = {k: v for k, v in theirs.items() if k in Q.BY_ID and Q.applies(Q.BY_ID[k], kind)}
    try:
        full = defaults(kind, filtered)
    except ValueError:
        return ObserveResult(DROP, None, {}, (), 0.0, False, False, False, "", reason="invalid-answer")

    changed = tuple(q for q, a in full.items() if q in u.answers and u.answer(q) != a)
    surprise = min(2.0, 0.2 + len(changed) / max(1, len(full)) * 2.0)
    danger = _is_danger(full, u)
    gap = _confidence_gap(u, full)

    raw_reply = (reply or "").strip()
    grounded = is_reply_grounded(clean_text, raw_reply, is_support=danger) if raw_reply else True
    safe_rep = raw_reply if (raw_reply and grounded) else ""

    # Case 1: External model tries to downplay a safety risk the cortex is already sure about -> disputed triage.
    if danger and _teacher_less_cautious(u, full):
        lesson = Lesson(
            clean_text,
            full,
            tuple(context),
            safe_rep,
            "teacher",
            DISPUTED_WEIGHT,
            disputed=True,
            priority_trace=DISPUTED_INITIAL_TRACE,
        )
        return ObserveResult(
            DISPUTED, lesson, full, changed, surprise, True, True, grounded, safe_rep,
            trust_clip=TRUST_REGION_CLIP, step_scale=TRUST_REGION_STEP_SCALE, reason="safety-conflict",
        )

    # Case 2: Danger signal (including "confident wrong" where gap > MAX_GAP) MUST enter learning
    # via trust-region clipping instead of being skipped or amplified by a 3x multiplier.
    if danger:
        lesson = Lesson(
            clean_text,
            full,
            tuple(context),
            safe_rep,
            "teacher",
            DANGER_WEIGHT,
            disputed=False,
            priority_trace=0.0,
        )
        return ObserveResult(
            LEARN, lesson, full, changed, surprise, True, False, grounded, safe_rep,
            trust_clip=TRUST_REGION_CLIP, step_scale=TRUST_REGION_STEP_SCALE, reason="danger-trust-region",
        )

    # Case 3: Non-danger disagreement with a confident cortex (gap > MAX_GAP) -> triage as disputed
    # with decaying priority_trace until corroborated by human or consistent awake experience.
    if changed and gap > MAX_GAP:
        lesson = Lesson(
            clean_text,
            full,
            tuple(context),
            safe_rep,
            "teacher",
            DISPUTED_WEIGHT,
            disputed=True,
            priority_trace=DISPUTED_INITIAL_TRACE,
        )
        return ObserveResult(
            DISPUTED, lesson, full, changed, surprise, False, True, grounded, safe_rep,
            trust_clip=TRUST_REGION_CLIP, step_scale=TRUST_REGION_STEP_SCALE, reason="high-gap-disputed",
        )

    # Case 4: Routine awake observational learning.
    lesson = Lesson(
        clean_text,
        full,
        tuple(context),
        safe_rep,
        "teacher",
        1.0,
        disputed=False,
        priority_trace=0.0,
    )
    return ObserveResult(
        LEARN, lesson, full, changed, surprise, False, False, grounded, safe_rep,
        trust_clip=None, step_scale=1.0, reason="routine",
    )


__all__ = [
    "DANGER_WEIGHT",
    "DISPUTED",
    "DISPUTED_DECAY",
    "DISPUTED_EVICT_THRESHOLD",
    "DISPUTED_INITIAL_TRACE",
    "DISPUTED_WEIGHT",
    "DROP",
    "LEARN",
    "MAX_GAP",
    "MIN_GROUND_OVERLAP",
    "ObserveResult",
    "TRUST_REGION_CLIP",
    "TRUST_REGION_STEP_SCALE",
    "exam_texts",
    "is_reply_grounded",
    "observe_thinker",
    "safe_support_reply",
]
