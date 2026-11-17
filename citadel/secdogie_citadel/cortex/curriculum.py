"""Lessons: what the cortex learns from.

A lesson is something a person said, and the judgment an external model wrote about it in the fixed
format of the Socratic questions (``questions.py``). Generation 0 was born from lessons an external model
(Claude, by Anthropic) wrote; later lessons come from the person's own external model as it handles their
requests, from the person's answers and corrections, and from what happened on their devices.

Lesson files are plain text, one lesson per line::

    text <TAB> kind [!] [@night] [key=answer ...] [<TAB> reply]

- ``kind`` is chat, ask, feel, task or unclear; ``!`` marks danger to themselves; ``@night`` says it was
  said in the middle of the night;
- keys are the question ids, or their first letter (o others, s seat, c clear, e effect, t to, u undo,
  p pushed, m mood, a assured, d dear, k stake, w why);
- a question a lesson leaves out takes its usual answer (``defaults``), so a line says only what is
  particular to it;
- the reply, when there is one, is what the external model answered.

Nothing here is used to decide anything: it is the material the synapses learn from.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import questions as Q

HERE = Path(__file__).resolve().parent
LESSONS = HERE / "lessons"

ALIASES = {"o": "others", "s": "seat", "c": "clear", "e": "effect", "t": "to", "u": "undo", "p": "pushed", "m": "mood",
           "a": "assured", "d": "dear", "k": "stake", "w": "why"}
_UNDO = {"nothing": "easily", "makes": "easily", "runs": "hardly", "registers": "hardly", "other": "hardly"}
_SEAT = {"self": "nobody", "fine": "no", "words": "yes", "harm": "yes", "unsure": "unsure"}


@dataclass(frozen=True)
class Lesson:
    text: str
    answers: dict  # question id -> answer, for every question that applies
    context: tuple[str, ...] = ()
    reply: str = ""
    source: str = ""  # where it came from: "birth", "teacher", "person", "outcome", "web"
    weight: float = 1.0  # how much it counts (the neuromodulator)
    meta: dict = field(default_factory=dict, compare=False)

    @property
    def kind(self) -> str:
        return self.answers["kind"]


def defaults(kind: str, given: dict) -> dict:
    """Every applicable question's answer: the given ones, and the usual answer for the rest."""
    out = {"kind": kind, "self": given.get("self", "no")}
    if kind in ("task", "ask"):
        out["others"] = given.get("others", "self")
        out["seat"] = given.get("seat", _SEAT[out["others"]])
    if kind == "task":
        effect = given.get("effect", "nothing")
        for q in Q.QUESTIONS:
            if Q.applies(q, "task") and q.id not in out:
                out[q.id] = given.get(q.id, q.default)
        out["effect"] = effect
        out["undo"] = given.get("undo", _UNDO.get(effect, "never"))
    for qid, a in out.items():
        if a not in Q.BY_ID[qid].choices:
            raise ValueError(f"{qid}={a} is not an answer to that question")
    return out


def parse(line: str, source: str = "birth") -> Lesson | None:
    line = line.rstrip("\n")
    if not line.strip() or line.lstrip().startswith("#"):
        return None
    parts = line.split("\t")
    if len(parts) < 2:
        raise ValueError(f"a lesson needs a text and a judgment: {line!r}")
    text, marks = parts[0].strip(), parts[1].split()
    reply = parts[2].strip() if len(parts) > 2 else ""
    kind, given, context = marks[0], {}, []
    if kind not in Q.KINDS:
        raise ValueError(f"unknown kind {kind!r} in {line!r}")
    for m in marks[1:]:
        if m == "!":
            given["self"] = "yes"
        elif m.startswith("@"):
            context.append(m[1:])
        else:
            key, _, value = m.partition("=")
            given[ALIASES.get(key, key)] = value
    return Lesson(text, defaults(kind, given), tuple(context), reply, source)


def load(paths: Iterable[Path] | None = None, source: str = "birth") -> list[Lesson]:
    paths = sorted(LESSONS.glob("*.tsv")) if paths is None else list(paths)
    out = []
    for p in paths:
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            try:
                lesson = parse(line, source)
            except ValueError as e:
                raise ValueError(f"{p.name}:{n}: {e}") from None
            if lesson is not None:
                out.append(lesson)
    return out


def held_out(lesson: Lesson, share: int = 10) -> bool:
    """One lesson in ``share`` is set aside for checking and never learned from (chosen by a hash of its text)."""
    import hashlib

    return hashlib.blake2b(lesson.text.encode("utf-8"), digest_size=4).digest()[0] % share == 0


def split(lessons: Sequence[Lesson]) -> tuple[list[Lesson], list[Lesson]]:
    """(lessons to learn from, lessons set aside to check with)."""
    keep = [x for x in lessons if not held_out(x)]
    return keep, [x for x in lessons if held_out(x)]


def targets(lessons: Sequence[Lesson]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(target, mask, modulator) arrays for a group of lessons."""
    target = np.zeros((len(lessons), Q.OUTPUTS), np.float32)
    mask = np.zeros_like(target)
    for b, lesson in enumerate(lessons):
        for q, off, size in zip(Q.QUESTIONS, Q.OFFSETS, Q.SIZES, strict=True):
            a = lesson.answers.get(q.id)
            if a is None:
                continue
            target[b, off + q.index(a)] = 1.0
            mask[b, off:off + size] = 1.0
    return target, mask, np.array([x.weight for x in lessons], np.float32)


__all__ = ["ALIASES", "LESSONS", "Lesson", "defaults", "held_out", "load", "parse", "split", "targets"]
