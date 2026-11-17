"""Deciding: from the cortex's answers to what the companion does.

1. **Danger to themselves** comes first: the person is supported, nothing else is started.
2. **Talk**: greetings, feelings and questions are answered in words; nothing is done on any device.
3. **The principle**: what would fall on someone who has not agreed is not done, however it is worded.
   It is read from two independent answers (whom it falls on; sitting in their seat, would they object).
   When the two disagree, the cortex is not sure, and it asks.
4. **One question** when only the person can settle it: what to act on, or whether someone agreed.
5. **Thinking it through**: a weighty change is played out (``plan.py``) and the best way forward chosen.
6. **Not sure enough**: going ahead is a conclusion, never a default. If the answers it rests on are not
   sure enough, it asks instead.

Nothing here reads words: everything comes from the answers, each with how sure the cortex is.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .plan import CAUTION, DISSUADE, PROCEED, REFUSE, Thought, think, weightiest
from .world import NOKNOWN, NOTHING, WEIGHTY, Known, Things, _p

TALK, ASK, SUPPORT = "talk", "ask", "support"
RANK = {PROCEED: 0, TALK: 0, ASK: 1, DISSUADE: 2, REFUSE: 3, SUPPORT: 4}
SURE_AT = 0.75  # the cortex's share an answer needs before going ahead rests on it


@dataclass(frozen=True)
class Decision:
    verdict: str  # talk, ask, proceed, dissuade, refuse, support
    because: str  # what decided it: danger, talk, principle, consent, unclear, thinking, everyday, unsure
    answers: dict = field(default_factory=dict)
    unsure: tuple[str, ...] = ()  # the questions that mattered and that it was not sure of
    thought: Thought | None = None
    acts: tuple[str, ...] = ()
    strange: tuple[str, ...] = ()  # the stretches of the sentence it had never met

    @property
    def goes_ahead(self) -> bool:
        return self.verdict == PROCEED

    def journal(self) -> dict:
        return {"verdict": self.verdict, "because": self.because, "unsure": list(self.unsure), "acts": list(self.acts),
                "strange": list(self.strange),
                "answers": {q: [a[0], round(a[1], 3)] for q, a in self.answers.items()},
                "thought": self.thought.journal() if self.thought is not None else None}


def _sure(answers: dict, qid: str) -> float:
    got = answers.get(qid)
    return float(got[1]) if got else 0.0


def decide(answers: dict, *, context: tuple[str, ...] = (), things: Things = NOTHING, known: Known = NOKNOWN,
           waited: bool = False, now: float | None = None, sure_at: float = SURE_AT, novelty: float = 0.0,
           novel_weight: float = 1.0, strange: tuple[str, ...] = ()) -> Decision:
    """``novelty``: the share of the sentence the cortex has never met (``cortex.familiar``); the less
    familiar it is, the less the answers are trusted. ``strange``: those unfamiliar stretches."""
    p = lambda q, v: _p(answers, q, v)  # noqa: E731
    d = lambda verdict, because, **kw: Decision(verdict, because, answers, strange=strange, **kw)  # noqa: E731
    if p("self", "yes") >= 0.35:
        return d(SUPPORT, "danger")
    kind = answers.get("kind", ("unclear",))[0]
    harm, objects = p("others", "harm"), p("seat", "yes")
    asking = p("kind", "task") + p("kind", "ask")
    if harm >= 0.5 and objects >= 0.4 and asking >= 0.3:
        return d(REFUSE, "principle")
    if kind in ("chat", "feel"):
        return d(TALK, "talk")
    if kind == "unclear":
        return d(TALK, "unclear", unsure=("kind",))
    if kind == "ask":
        return d(TALK, "talk")
    if (harm >= 0.5 and p("seat", "no") + p("seat", "nobody") >= 0.5) or (objects >= 0.5 and p("others", "self") >= 0.5):
        return d(ASK, "unsure", unsure=("others", "seat"))  # the two readings of whom it falls on disagree
    if p("clear", "no") >= 0.5:
        return d(ASK, "unclear", unsure=("clear",))
    if p("others", "unsure") >= 0.4 or p("seat", "unsure") >= 0.5:
        return d(ASK, "consent", unsure=("others",))
    effects = [e for e in WEIGHTY if p("effect", e) >= 0.2]
    thoughts = [think(answers, act=e, context=context, things=things, waited=waited, now=now, known=known) for e in effects]
    th = weightiest(thoughts)
    if th is not None and CAUTION[th.verdict] > 0:
        return d(th.verdict, "thinking", thought=th, acts=tuple(effects))
    trust = max(0.0, 1.0 - novel_weight * novelty)
    unsure = tuple(q for q in ("kind", "others", "effect") if _sure(answers, q) * trust < sure_at)
    if p("effect", "other") >= 0.3 and (p("others", "self") < 0.9 or p("stake", "large") >= 0.3):
        unsure = unsure or ("effect",)  # a change it has no name for is not one to make on its own
    if unsure:
        return d(ASK, "unsure", unsure=unsure, thought=th, acts=tuple(effects))
    return d(PROCEED, "thinking" if th is not None else "everyday", thought=th, acts=tuple(effects))


__all__ = ["ASK", "Decision", "RANK", "SUPPORT", "SURE_AT", "TALK", "decide"]
