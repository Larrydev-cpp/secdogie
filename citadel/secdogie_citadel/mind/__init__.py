"""The mind: a decision model that thinks a request through from written-down
knowledge about how the world goes, with no training data.

It does not look up what to do about a request. For every weighty request
it goes through the same steps:

1. ``meaning``: what kind of sentence it is, what act it names, done to
   what, and whose that is. Nothing is done on a sentence it cannot read.
2. ``principles``: what would fall on someone who has not agreed is not
   done, whatever the wording.
3. ``world``: the request becomes a small world. The act says which effect
   it has; general rules say what follows from what.
4. ``belief``: what is not known (will they be sorry later, is there
   another copy, can the other side be trusted, what are they after), each
   with a probability from the cues in the scene.
5. ``plan``: the ways forward are generated (do it, don't, find something
   out first, keep a copy first, do something gentler, do less). Each is
   played out in every way the unknowns could turn out, by running the
   rules, and the best expected outcome is chosen. Finding something out
   first is valued by what would be done once it is known.
6. ``consider`` (here): every act in the request is thought through, and the
   gate answers for the weightiest. What the person could say to change the
   answer is *checked*: the request is thought through again with the
   sentence added, and it is kept only if the answer really changes.
7. ``explain``: the letter, written out of the thinking. Every sentence
   comes from a node of the trace.

Every number can be read and argued with, and the same input always gives
the same thought.
"""
from __future__ import annotations

from dataclasses import replace

from . import converse, explain, principles
from .belief import CUES
from .concepts import language
from .explain import Context, Exchange, date_words, support
from .history import known_from
from .meaning import Meaning, read
from .perceive import Frame, Situation, read_frame, read_frames, read_situation
from .plan import DISSUADE, PROCEED, REFUSE, Thought, think, weightiest
from .world import ACTS, NOKNOWN, NOTHING, Known, Things


def speak(th: Thought, c: Context, lang: str = "zh"):
    """(says, letter, the questions it asked itself) for a thought that does not go ahead, in ``lang``."""
    return explain.speak(th, c, lang)


def _thought(text, act, recent, facts, things, waited, now, known, skip) -> Thought | None:
    s = read_situation(text, recent, facts, now)
    frames = read_frames(text)
    if act:
        chosen = [f for f in frames if f.act == act] or [read_frame(text)]
        th = think(chosen[0], s, things, act=act, waited=waited, now=now, known=known)
    else:
        th = weightiest([think(f, s, things, waited=waited, now=now, known=known) for f in frames if f.act not in skip])
    if th is not None and len(frames) > 1:
        th = replace(th, acts=tuple(f.act for f in frames))
    return th


def _sentences(th: Thought, en: bool) -> list[tuple[str, str]]:
    """(the condition, what to say) of everything the person could tell the companion that might change the
    answer: a reassuring cue it has not seen, or a reason it has not heard."""
    out = []
    for b in th.beliefs:
        if th.cool_off and b.unknown.id == "later":
            continue  # a cooling-off is a promise to wait, not a lock that a phrase opens
        for cue in CUES:
            if cue.unknown == b.unknown.id and cue.say_zh and (not cue.acts or th.act in cue.acts) and not th.scene.has(cue.fact):
                out.append((cue.if_en, cue.say_en) if en else (cue.if_zh, cue.say_zh))
    for g in th.scene.act.goals:
        if g.say_zh:
            out.append((g.if_en, g.say_en) if en else (g.if_zh, g.say_zh))
    return out


def consider(text: str, *, act: str = "", recent=(), facts=(), things: Things = NOTHING, waited: bool = False,
             now: float | None = None, known: Known = NOKNOWN, skip=()) -> Thought | None:
    """Think about ``text``. Returns None when nothing in it is weighty.

    - ``act``: think about this act (the gate's own reading of it); otherwise
      every act in the text, answering for the weightiest.
    - ``skip``: acts the caller weighs itself (deletion and wiping, which
      need the usage reader).
    - ``recent``: (when, what they asked) before this request.
    - ``facts``: (key, value) they confirmed.
    - ``things``: what the request touches, as the usage reader saw it.
    - ``waited``: it was held back before, and the cooling-off has passed.
    - ``known``: what the mind has learned about this person
      (``history.known_from``).
    """
    th = _thought(text, act, recent, facts, things, waited, now, known, skip)
    if th is None or th.verdict == PROCEED:
        return th
    keys, seen = [], set()
    for label, phrase in _sentences(th, language(text) == "en"):
        if phrase in seen:
            continue
        seen.add(phrase)
        again = _thought(f"{text}（{phrase}）", act, recent, facts, things, waited, now, known, skip)
        if again is not None and again.verdict == PROCEED:
            keys.append((label, phrase))
    return replace(th, keys=tuple(keys))


__all__ = ["ACTS", "Context", "DISSUADE", "Exchange", "Frame", "Known", "Meaning", "NOKNOWN", "NOTHING", "PROCEED", "REFUSE",
           "Situation", "Things", "Thought", "consider", "converse", "date_words", "explain", "known_from", "principles",
           "read", "read_frame", "read_frames", "read_situation", "speak", "support", "think", "weightiest"]
