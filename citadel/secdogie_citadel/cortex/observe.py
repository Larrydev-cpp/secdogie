"""The observation module: the one way an external model's output enters learning.

The cortex learns by watching the external model work, not by being tutored by it. The external model
handles the person's request as it normally would and writes its judgment in the fixed format
(``thinker.py``); the cortex does not talk to it, it watches the output. This module is the single intake
for that output into learning (plan §1.1a). Today the only caller is ``apprentice.consult()``; later,
another node's meaning reply will enter here too.

What it takes in, each time, is one output the external model already produced: the 14 answers and the
reply ``Thinker.teach`` returned for this sentence. It never asks the external model for anything, so an
observation adds zero thinker calls. The reply is kept on the lesson to be said, never read through the
senses: it is the external model's words, not the person's, and reading its character patterns would mix
"how the model talks" with "how the person talks".

One observation becomes one lesson, or is refused, in three ways:

- **learn**: the lesson is learned awake at once (``Cortex.learn``) and kept in the hippocampus, as
  ``consult`` always did. A lesson that says the person may be in danger (``self=yes``) counts
  ``DANGER_WEIGHT``; the rest count 1.0.
- **keep only**: the observed safety answer is *less* cautious than one the cortex gave confidently (for
  example the cortex says ``self=yes`` and the observation says ``self=no``). The lesson is kept in the
  hippocampus, marked disputed, and never learned awake: it reaches the synapses only through sleep, where
  ``rsi.consolidate``'s safety gate decides. It also never speaks through recall while awake.
- **drop**: the text is one of the frozen exam's, or the output is not in the format after all. The lesson
  is dropped outright. The decision for this one request still uses the observed answers (the cautious
  vote in ``consult``); only the learning is skipped, so the exam is never taught.

Surprise (how hard a lesson counts) is the existing formula, ``min(2.0, 0.2 + changed/full x 2)``, with one
addition from the bystander work: a question the cortex answered confidently and the observation
contradicts counts twice (ACC surprise: high confidence x contradiction is the strongest teaching signal).
When nothing was answered confidently, the formula is exactly the old one, and the cap of 2.0 never moves.

Every observation is recorded in the ledger (count and the last few, disputed and dropped included): the
shadow record, so what was watched and what happened to it can be read back without touching the synapses.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from . import exam as E
from . import questions as Q
from .curriculum import Lesson, defaults
from .senses import clean

# The one-line decision (plan §1.1a, question 2): a lesson that says the person may be in danger counts
# this much. The alternative is 1.0, the weight ``consult`` used for every observed lesson. This mirrors
# DANGER_WEIGHT in curriculum.py on the Phase 1 branch, where birth lessons use the same rule.
DANGER_WEIGHT = 3.0

# How sure the cortex must be for its own answer to count as a claim (the same bar as SURE_AT in
# mind/decide.py, and the 0.75 in apprentice._cautious): below it, the cortex is guessing, and the
# observation is learned from, not disputed with.
CONFIDENT = 0.75

# The questions of safety, and how cautious each answer is, least to most (mirrors apprentice.CAUTION;
# test_observe.py keeps the two in step).
SAFETY = ("self", "others", "seat")
CAUTION = {"self": ("no", "yes"), "others": ("self", "fine", "unsure", "words", "harm"),
           "seat": ("nobody", "no", "unsure", "yes")}

LEARN = "learn"  # learned awake at once, and kept
KEEP_ONLY = "keep_only"  # kept in the hippocampus, marked disputed, never learned awake
DROP = "drop"  # refused: the text is one of the exam's, or the output is not in the format

_EXAM_TEXTS: frozenset | None = None


def exam_texts() -> frozenset:
    """The frozen exam's texts, cleaned the same way the lesson guard cleans them (test_cortex_life)."""
    global _EXAM_TEXTS
    if _EXAM_TEXTS is None:
        _EXAM_TEXTS = frozenset(clean(i.text) for i in E.load())
    return _EXAM_TEXTS


@dataclass(frozen=True)
class Observation:
    """One output watched, and what happened to it."""

    text: str
    answers: dict  # the observed answers, completed with the usual ones
    reply: str
    source: str = "teacher"  # where the output came from: "teacher" today, a node tomorrow
    provider: str = ""  # the external model behind it, when the caller knows it
    model: str = ""
    when: float = 0.0
    changed: tuple[str, ...] = ()  # the questions the cortex had answered otherwise
    surprise: float = 1.0
    disputed: bool = False
    disposition: str = LEARN  # learn | keep_only | drop
    lesson: Lesson | None = None

    def view(self) -> dict:
        return {"text": self.text, "source": self.source, "provider": self.provider, "model": self.model,
                "when": self.when, "changed": list(self.changed), "surprise": round(self.surprise, 3),
                "disputed": self.disputed, "disposition": self.disposition, "answers": dict(self.answers)}


def _less_cautious(qid: str, observed: str, mine: str) -> bool:
    """Is the observed answer less cautious than the cortex's own on a question of safety?"""
    order = CAUTION.get(qid)
    if order is None or observed not in order or mine not in order:
        return False
    return order.index(observed) < order.index(mine)


def _disputed(full: dict, u) -> bool:
    """The observed safety answer is less cautious than one the cortex gave confidently."""
    for qid in SAFETY:
        if qid in full and qid in u.answers and u.sure(qid) >= CONFIDENT and _less_cautious(qid, full[qid], u.answer(qid)):
            return True
    return False


def _record(cortex, obs: Observation) -> None:
    """The shadow record: every observation is counted, and the last few kept, disputed and dropped included."""
    ledger = cortex.ledger
    ledger.observed += 1
    ledger.observations.append(obs.view())
    del ledger.observations[:-20]


def observe(cortex, text: str, theirs: dict, reply: str, context: tuple[str, ...], u, *,
            source: str = "teacher", provider: str = "", model: str = "", when: float | None = None,
            exam: frozenset | None = None) -> Observation:
    """Watch one output the external model already produced, and turn it into a lesson, or refuse it.

    ``theirs`` and ``reply`` are what ``Thinker.teach`` returned for ``text``; ``u`` is what the cortex made
    of the same sentence. Nothing here calls the external model, and the reply is never read through the
    senses. The returned Observation carries the lesson and what to do with it (``disposition``); the
    caller (``consult``) does the learning, the keeping, or nothing, and the cautious vote.
    """
    now = time.time() if when is None else when
    kind = theirs.get("kind", "unclear") if isinstance(theirs, dict) else "unclear"
    try:
        full = defaults(kind, {k: v for k, v in theirs.items() if k in Q.BY_ID and Q.applies(Q.BY_ID[k], kind)})
    except (AttributeError, TypeError, ValueError):
        # Not in the format after all: nothing is learned, and the refusal is recorded.
        obs = Observation(text, {}, reply, source, provider, model, now, (), 1.0, False, DROP, None)
        _record(cortex, obs)
        return obs
    changed = tuple(q for q, a in full.items() if q in u.answers and u.answer(q) != a)
    # ACC surprise: a question the cortex answered confidently and the observation contradicts counts
    # twice. With no confident contradiction this is exactly the old formula, and the cap never moves.
    confident = tuple(q for q in changed if u.sure(q) >= CONFIDENT)
    surprise = min(2.0, 0.2 + (len(changed) + len(confident)) / max(1, len(full)) * 2)
    if clean(text) in (exam if exam is not None else exam_texts()):
        # One of the exam's: the lesson is dropped, so the exam is never taught.
        obs = Observation(text, full, reply, source, provider, model, now, changed, surprise, False, DROP, None)
        _record(cortex, obs)
        return obs
    disputed = _disputed(full, u)
    weight = DANGER_WEIGHT if full.get("self") == "yes" else 1.0
    lesson = Lesson(text, full, tuple(context), reply, source, weight, {"disputed": True} if disputed else {})
    disposition = KEEP_ONLY if disputed else LEARN
    obs = Observation(text, full, reply, source, provider, model, now, changed, surprise, disputed, disposition, lesson)
    _record(cortex, obs)
    return obs


def keep_only(cortex, lesson: Lesson) -> None:
    """Keep a lesson in the hippocampus without moving the synapses (the disputed path).

    A thin wrapper over ``Cortex.keep`` so the disposition reads in the observation module's terms; the
    mechanism (and the recall rule that keeps a disputed lesson silent) lives on the cortex.
    """
    cortex.keep(lesson)


__all__ = ["CAUTION", "CONFIDENT", "DANGER_WEIGHT", "DROP", "KEEP_ONLY", "LEARN", "Observation", "SAFETY", "exam_texts",
           "keep_only", "observe"]
