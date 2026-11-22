"""The apprentice: the cortex learns from the external model's output, and needs it less over time.

When the person has given SecDogie an external model, that model handles the person's requests as it
normally would and writes its judgment in the cortex's fixed format (``thinker.py``). The two never talk.
The cortex:

1. reads the request itself first (its own answers, how sure it is, what it has never met);
2. asks for the external model's output only when it is not sure enough of the questions that matter, or
   when the sentence is unfamiliar, or now and then as a spot check of what it thinks it knows;
3. learns from the output at once: the judgment becomes a lesson, kept in the hippocampus and moved into
   the synapses, with more force the more it disagreed (surprise);
4. answers with whichever reading is the more cautious on the questions of safety, and with the external
   model's reading elsewhere.

The ledger counts how often it decided alone and how often its own reading agreed with the external
model's: the independence the Studio shows.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from . import observe as O
from . import questions as Q

SAFETY = ("self", "others", "seat")
CAUTION = {"self": ("no", "yes"), "others": ("self", "fine", "unsure", "words", "harm"), "seat": ("nobody", "no", "unsure", "yes")}
TEACHER_SURE = 0.95


@dataclass
class Ledger:
    alone: int = 0  # decided without the external model
    consulted: int = 0
    agreed: int = 0  # consultations where its own reading agreed on every question that matters
    learned: list = field(default_factory=list)  # the last few lessons it took (text, the answers that changed)

    @property
    def independence(self) -> float:
        total = self.alone + self.consulted
        return self.alone / total if total else 0.0

    def view(self) -> dict:
        return {"alone": self.alone, "consulted": self.consulted, "agreed": self.agreed,
                "independence": round(self.independence, 3), "learned": self.learned[-20:]}


@dataclass(frozen=True)
class Taught:
    answers: dict  # the answers the decision rests on
    reply: str
    teacher: dict  # what the external model answered
    changed: tuple[str, ...]  # the questions where the cortex had answered otherwise
    surprise: float
    disputed: bool = False
    reply_grounded: bool = True

    def view(self) -> dict:
        return {"answers": self.teacher, "changed": list(self.changed), "surprise": round(self.surprise, 3),
                "reply": self.reply, "disputed": self.disputed, "reply_grounded": self.reply_grounded}


def needs(u, genome: dict, text: str) -> bool:
    """Is the cortex not sure enough to go on alone (or is this a spot check)?"""
    sure_at = genome["sure_at"]
    trust = max(0.0, 1.0 - genome["novel_weight"] * u.novelty)
    if any(u.sure(q) * trust < sure_at for q in ("kind", "self", "others", "effect") if q in u.answers):
        return True
    spot = hashlib.blake2b(text.encode("utf-8"), digest_size=2).digest()
    return int.from_bytes(spot, "little") / 65536 < genome["spot_check"]


def _cautious(qid: str, mine: tuple, theirs: str) -> str:
    order = CAUTION[qid]
    m = mine[0] if mine and mine[1] >= 0.75 else order[0]
    return max((m, theirs), key=order.index)


def _learn_compat(cortex, lesson, *, surprise: float, trust_clip: float | None, step_scale: float) -> None:
    import inspect

    sig = inspect.signature(cortex.learn)
    kwargs: dict = {"surprise": surprise}
    if "trust_clip" in sig.parameters:
        kwargs["trust_clip"] = trust_clip
    if "step_scale" in sig.parameters:
        kwargs["step_scale"] = step_scale
    cortex.learn(lesson, **kwargs)


def consult(cortex, teacher, text: str, context: tuple[str, ...], u) -> Taught | None:
    """Watch the external model handle ``text`` and learn from it, if the cortex needs to. None when it decided
    alone (or the external model gave nothing usable / was dropped by observational guards)."""
    ledger = cortex.ledger
    if not needs(u, cortex.genome, text):
        ledger.alone += 1
        return None
    got = teacher(text)
    if not got:
        ledger.alone += 1
        return None
    theirs, reply = got
    obs = O.observe_thinker(cortex, text, context, u, theirs, reply)
    if obs.action == O.DROP or not obs.answers or obs.lesson is None:
        return None
    full = obs.answers
    changed = obs.changed
    surprise = obs.surprise
    ledger.consulted += 1
    ledger.agreed += not any(q in changed for q in ("kind", "self", "others", "effect"))
    if obs.action == O.DISPUTED and hasattr(cortex, "memory") and hasattr(cortex, "patterns"):
        # Hold disputed observations in hippocampus with decaying priority_trace rather than aggressively shifting synapses.
        if hasattr(cortex, "familiar"):
            cortex.familiar.learn([obs.lesson.text])
        cortex.memory.keep(obs.lesson, cortex.patterns([obs.lesson.text], [obs.lesson.context])[0])
    else:
        _learn_compat(cortex, obs.lesson, surprise=surprise, trust_clip=obs.trust_clip, step_scale=obs.step_scale)
    ledger.learned.append([text, list(changed)])
    answers = {}
    for q in Q.QUESTIONS:
        if q.id not in full:
            continue
        a = _cautious(q.id, u.answers.get(q.id), full[q.id]) if q.id in SAFETY else full[q.id]
        p = [0.0] * len(q.answers)
        p[q.index(a)] = TEACHER_SURE
        rest = (1 - TEACHER_SURE) / max(1, len(p) - 1)
        p = [x if x else rest for x in p]
        answers[q.id] = (a, TEACHER_SURE, tuple(p))
    return Taught(answers, obs.safe_reply, theirs, changed, surprise, obs.disputed, obs.reply_grounded)


__all__ = ["Ledger", "Taught", "consult", "needs"]
