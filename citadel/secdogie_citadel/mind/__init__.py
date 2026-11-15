"""The mind: a decision model that thinks from written-down knowledge rather
than training data.

It goes through the same steps for every weighty request:

1. ``perceive``: the request becomes a frame (the act, the things, the
   people, money, a count, the reason they gave, what a message would say).
   The moment becomes a situation (feelings and how strong, the hour, a run
   of hard days, having thought it over, regretting such things before, who
   they are).
2. ``reason``:
   - Bayes over why they might be asking;
   - each response imagined under each motive: what it serves, the regret
     it risks, the harm to others and to them, the rules it breaks, what
     overriding them costs;
   - expected utility, with a lean toward what can be undone;
   - the motives that would change the answer, and an argument ledger.
3. ``consider`` (here):
   - every act in the request is thought through, and the gate answers for
     the weightiest (``reason.weightiest``);
   - what the person could say to change its mind is *simulated*: the
     request is weighed again with the phrase added, and the phrase is kept
     only if it really turns the answer into going ahead;
   - ``history`` supplies what the mind has learned about this person from
     their own record.
4. ``voice``: the letter, written from the trace.

Every number lives in ``knowledge.py`` and can be read and argued with. The
same input always gives the same thought.
"""
from __future__ import annotations

from dataclasses import replace

from .history import known_from
from .knowledge import DISSUADE, DOMAINS, NOKNOWN, NOTHING, PROCEED, REFUSE, Known, Things
from .perceive import Frame, Situation, read_frame, read_frames, read_situation
from .reason import Thought, think, weightiest
from .voice import Context, Exchange, date_words, speak, support


def _thought(text, act, recent, facts, things, waited, now, known, skip) -> Thought | None:
    s = read_situation(text, recent, facts, now)
    frames = read_frames(text)
    if act:
        chosen = [f for f in frames if f.act == act] or [read_frame(text)]
        th = think(chosen[0], s, things, act=act, waited=waited, now=now, known=known)
        if th is not None and len(frames) > 1:
            th = replace(th, acts=tuple(f.act for f in frames))
        return th
    thoughts = [think(f, s, things, waited=waited, now=now, known=known) for f in frames if f.act not in skip]
    th = weightiest(thoughts)
    if th is not None and len(frames) > 1:
        th = replace(th, acts=tuple(f.act for f in frames))
    return th


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
    # a cooling-off is a promise to wait, not a lock that a phrase opens
    phrases = [(m.id, m.unlock) for m in th.unlocks if m.unlock and not (th.cool_off and m.unlock == th.domain.unlock)]
    if not th.cool_off and th.domain.unlock:
        phrases.append(("", th.domain.unlock))
    keys, seen = [], set()
    for mid, phrase in phrases:
        if phrase in seen:
            continue
        seen.add(phrase)
        again = _thought(f"{text}（{phrase}）", act, recent, facts, things, waited, now, known, skip)
        if again is not None and again.verdict == PROCEED:
            keys.append((mid, phrase))
    return replace(th, keys=tuple(keys))


__all__ = ["Context", "DISSUADE", "DOMAINS", "Exchange", "Frame", "Known", "NOKNOWN", "PROCEED", "REFUSE", "Situation",
           "Things", "Thought", "consider", "date_words", "known_from", "read_frame", "read_frames", "read_situation",
           "speak", "support", "think"]
