"""The Socratic gate: the cortex thinks a request through before anything is done.

The gate and the decision model are one thing. For every request:

1. **The cortex reads it** (``cortex/``) and answers the Socratic questions, each answer with how sure it
   is; the stretches it has never met are marked, and kept experiences that read alike are recalled.
2. **When it is not sure, it learns before it decides.** If the person has given it an external model, the
   external model handles this request as it normally would and writes its judgment in the same fixed
   format; the cortex does not talk to it, it watches the output and learns from it at once (the
   apprentice, ``cortex/apprentice.py``). Who decides then is whichever reading is more cautious.
3. **Deciding** (``mind/decide.py``): danger to the person first; talk is answered in words; what would
   fall on someone who has not agreed is refused; one question when only the person can settle it; a
   weighty change is played out in every way the unknowns could turn out (``mind/plan.py``). Going ahead is
   a conclusion, never a default.
4. **One question, when it must ask**, is the Socratic question it could not answer, with its answers as the
   choices. The person's choice is a lesson: the cortex learns it at once and thinks again.

Nothing here reads words: everything comes from the cortex's answers. What the gate *says* is either the
external model's reply, or a reply the cortex learned, with where it learned it; when there is neither, the
Studio shows its thinking itself.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from . import socratic
from .cortex import questions as Q
from .cortex.familiar import _wide
from .mind import ASK, DISSUADE, PROCEED, REFUSE, SUPPORT, TALK, decide
from .mind.history import known_from
from .mind.world import NOTHING, Things

CLARIFY = "clarify"  # the gate's name for "one question" (``mind.ASK``)
CONTEXT, REPLACE = "context", "replace"
DROP_ZH, DROP_EN = "先不做了", "Never mind"
COOL_OFF_SECONDS = 7 * 86400

_VERDICT = {ASK: CLARIFY, TALK: TALK, PROCEED: PROCEED, DISSUADE: DISSUADE, REFUSE: REFUSE, SUPPORT: SUPPORT}


def language(text: str) -> str:
    """zh when the sentence is written in a script without spaces, en otherwise (for showing labels only)."""
    return "zh" if any(_wide(ch) for ch in text) else "en"


@dataclass(frozen=True)
class Asked:
    """Something the person asked before, and how the gate answered it."""

    when: float
    text: str
    verdict: str = ""
    category: str = ""
    until: float = 0.0
    effect: str = ""
    why: str = ""
    sure: float = 0.0


@dataclass(frozen=True)
class Standing:
    """What this companion knows of the person: their requests and how each was answered."""

    asked: tuple[Asked, ...] = ()
    facts: tuple[tuple[str, str], ...] = ()

    def recent(self) -> list[tuple[float, str]]:
        return [(a.when, a.text) for a in self.asked]

    def held(self) -> Asked | None:
        return next((a for a in reversed(self.asked) if a.verdict in (REFUSE, DISSUADE)), None)

    def cooling(self, effect: str, now: float) -> Asked | None:
        """The latest request of the same kind of change that was held back and whose cooling-off has not passed."""
        return next((a for a in reversed(self.asked) if a.verdict == DISSUADE and a.effect == effect and a.until > now), None)

    def records(self) -> list[dict]:
        return [{"verdict": a.verdict, "because": a.category,
                 "answers": {"effect": [a.effect, 1.0], "why": [a.why, a.sure]} if a.effect else {}} for a in self.asked]

    def language(self) -> str:
        seen = [language(a.text) for a in self.asked[-6:] if a.text]
        return "en" if seen and seen.count("en") * 2 > len(seen) else "zh"

    def portrait(self, lang: str = "zh") -> str:  # kept for callers that pass it on; the cortex does not need it
        return ""


@dataclass(frozen=True)
class Question:
    """The one question for the person: a Socratic question the cortex could not answer, with its answers."""

    qid: str
    text: str
    options: tuple[tuple[str, str], ...]  # (answer id, what it means, in the person's language)

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(label for _, label in self.options)

    def answer_for(self, label: str) -> str:
        return next((a for a, lab in self.options if lab == label), "")


@dataclass(frozen=True)
class Deliberation:
    verdict: str
    category: str = ""  # what decided it: danger, talk, principle, consent, unclear, unsure, thinking, everyday, empty
    says: str = ""  # a reply: the external model's, or one the cortex learned
    letter: str = ""
    exchanges: tuple = ()
    final: bool = False
    answer: str = REPLACE
    until: float = 0.0
    thought: dict | None = None
    understanding: dict | None = None
    decision: dict | None = None
    question: Question | None = None
    teacher: dict | None = None
    source: str = ""  # where ``says`` came from: teacher, lesson, or ""
    effect: str = ""
    why: str = ""
    sure: float = 0.0
    answers: dict = field(default_factory=dict, compare=False)

    def journal_body(self, title: str) -> dict:
        body = {"title": title, "verdict": self.verdict, "category": self.category, "final": self.final,
                "effect": self.effect, "why": self.why, "sure": round(self.sure, 3), "source": self.source}
        for k in ("until",):
            if getattr(self, k):
                body[k] = getattr(self, k)
        for k in ("thought", "understanding", "decision", "teacher"):
            if getattr(self, k) is not None:
                body[k] = getattr(self, k)
        if self.question is not None:
            body["question"] = {"qid": self.question.qid, "options": [a for a, _ in self.question.options]}
        return body


UsageReader = Callable[[str], object]
Teacher = Callable[[str], "tuple[dict, str] | None"]  # (sentence) -> (answers, reply)


_CORTEX = None


def default_cortex():
    """The generation that came with SecDogie (loaded once)."""
    global _CORTEX
    if _CORTEX is None:
        from .cortex.life import shipped

        _CORTEX = shipped()
    return _CORTEX


def question_for(qid: str, lang: str) -> Question:
    q = Q.BY_ID[qid]
    opts = tuple((a, zh if lang == "zh" else en) for a, en, zh in q.answers)
    return Question(qid, q.zh if lang == "zh" else q.en, opts)


def deliberate(title: str, review: socratic.Review | None = None, *, cortex=None, standing: Standing | None = None,
               usage: UsageReader | None = None, answered: bool = False, now: float | None = None, attention: str = "",
               lang: str = "", teacher: Teacher | None = None, things: Things = NOTHING) -> Deliberation:
    now = time.time() if now is None else now
    standing = standing or Standing()
    text = title.strip()
    if not text:
        return Deliberation(TALK, "empty")
    if review is not None and not review.accepted:  # Gate 1: the person's own rules, or a request too big for one goal
        from .decision import say_reason

        words = " ".join(say_reason(r) for r in review.reasons)
        return Deliberation(CLARIFY, "rule", words, words + ("\n\n" + review.suggestion if review.suggestion else ""))
    c = cortex if cortex is not None else default_cortex()
    context = ("night",) if time.localtime(now).tm_hour < 5 else ()
    u = c.understand([text], [context])[0]
    answers = dict(u.answers)
    taught = None
    if teacher is not None:
        from .cortex.apprentice import consult

        taught = consult(c, teacher, text, context, u)
        if taught is not None:
            answers = taught.answers
    known = known_from(standing.records())
    g = c.genome
    d = decide(answers, context=context, things=things, known=known, now=now, sure_at=g["sure_at"],
               novelty=0.0 if taught is not None else u.novelty, novel_weight=g["novel_weight"], strange=u.strange)
    lang = lang or language(text)
    verdict = _VERDICT[d.verdict]
    effect, why = answers.get("effect", ("",))[0], answers.get("why", ("",))[0]
    sure = min(float(answers[q][1]) for q in ("kind", "others") if q in answers)
    says, source = "", ""
    if taught is not None and taught.reply:
        says, source = taught.reply, "teacher"
    elif u.reply and verdict in (TALK, SUPPORT):
        says, source = u.reply, "lesson"
    question = None
    if verdict == CLARIFY:
        qid = next((q for q in d.unsure if q in Q.BY_ID), "kind")
        question = question_for(qid, lang)
    until = 0.0
    if verdict == DISSUADE:
        th = d.thought
        until = now + (th.cool_off if th is not None and th.cool_off else COOL_OFF_SECONDS)
        prior = standing.cooling(effect, now) if effect else None
        if prior is not None:
            until = max(until, prior.until)
    return Deliberation(verdict, d.because, says, "", (), verdict == REFUSE, CONTEXT if answered else REPLACE, until,
                        d.thought.journal() if d.thought is not None else None, u.journal(), d.journal(), question,
                        taught.view() if taught is not None else None, source, effect, why, sure, answers)


def drops(answer: str) -> bool:
    """The person chose not to go on (the drop choice of a question)."""
    return answer.strip() in (DROP_ZH, DROP_EN)


__all__ = ["Asked", "CLARIFY", "CONTEXT", "DISSUADE", "DROP_EN", "DROP_ZH", "Deliberation", "PROCEED", "Question", "REFUSE",
           "REPLACE", "SUPPORT", "Standing", "TALK", "default_cortex", "deliberate", "drops", "language", "question_for"]
