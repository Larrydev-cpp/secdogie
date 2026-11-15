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
3. ``consider`` (here): what the person could say to change its mind is
   *simulated*. The request is weighed again with the phrase added, and the
   phrase is kept only if it really turns the answer into going ahead.
4. ``voice``: the letter, written from the trace.

Every number lives in ``knowledge.py`` and can be read and argued with. The
same input always gives the same thought.
"""
from __future__ import annotations

from dataclasses import replace

from .knowledge import DISSUADE, DOMAINS, NOTHING, PROCEED, REFUSE, Things
from .perceive import Frame, Situation, read_frame, read_situation
from .reason import Thought, think
from .voice import Context, Exchange, date_words, speak, support


def consider(text: str, *, act: str = "", recent=(), facts=(), things: Things = NOTHING, waited: bool = False,
             now: float | None = None) -> Thought | None:
    """Think about ``text``. Returns None when nothing in it is weighty.

    ``recent``: (when, what they asked) before this request.
    ``facts``: (key, value) they confirmed.
    ``things``: what the request touches, as the usage reader saw it.
    ``waited``: it was held back before, and the cooling-off has passed.
    """
    f, s = read_frame(text), read_situation(text, recent, facts, now)
    th = think(f, s, things, act=act, waited=waited, now=now)
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
        said = f"{text}（{phrase}）"
        again = think(read_frame(said), read_situation(said, recent, facts, now), things, act=act, waited=waited,
                      now=now)
        if again is not None and again.verdict == PROCEED:
            keys.append((mid, phrase))
    return replace(th, keys=tuple(keys))


__all__ = ["Context", "DISSUADE", "DOMAINS", "Exchange", "Frame", "PROCEED", "REFUSE", "Situation", "Things",
           "Thought", "consider", "date_words", "read_frame", "read_situation", "speak", "support", "think"]
