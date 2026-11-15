"""What the mind learns about one person from their own record, with no
training data.

The record is the person's past requests and how the gate answered each
(``deliberate.Standing.asked``). Three things are counted:

- **held:** weighty steps the gate held back;
- **steady:** times they came back after a cooling-off, calm, and went
  ahead (category ``considered``). Their choices held, so their own say
  weighs more and the next cooling-off is shorter;
- **undone:** times they asked to restore, recover or undo something. They
  have regretted such steps before, so regret weighs more.

Their motives are counted too. Each past request is perceived again and its
motives inferred the same way as now, and a motive counts when it was
plainly their reason (likely, and resting on their own words). Those counts
shift this person's priors, a Dirichlet over the written-down ones
(``knowledge.Known.prior``).

Everything is deterministic and comes from what they said, never from
anyone else.
"""
from __future__ import annotations

from functools import lru_cache

from . import lexicon as L
from .knowledge import DOMAINS, NOKNOWN, NOTHING, Known
from .perceive import read_frame, read_situation

PLAIN_REASON = 0.6  # a past motive counts as their reason when it was at least this likely


@lru_cache(maxsize=64)
def _known(asked: tuple[tuple[float, str, str, str], ...]) -> Known:
    from .reason import beliefs  # reasoning reads what is known; knowing reads past reasoning

    held = sum(1 for _, _, verdict, _ in asked if verdict == "dissuade")
    steady = sum(1 for _, _, _, category in asked if category == "considered")
    undone = sum(1 for _, text, _, _ in asked if L.REGRET_PAST.search(text))
    counts: dict[tuple[str, str], int] = {}
    for when, text, verdict, _ in asked:
        if verdict == "refuse":
            continue
        f = read_frame(text)
        d = DOMAINS.get(f.act)
        if d is None or f.negated:
            continue
        top = beliefs(d, f, read_situation(text, now=when), NOTHING)[0]
        if top.p >= PLAIN_REASON and top.because and top.motive.id not in ("plain", "scam", "online", "stranger"):
            key = (d.act, top.motive.id)
            counts[key] = counts.get(key, 0) + 1
    motives = tuple(sorted((a, m, n) for (a, m), n in counts.items()))
    return Known(held, steady, undone, motives)


def known_from(asked) -> Known:
    """``asked``: (when, text, verdict, category) of each past request, oldest first."""
    rows = tuple((float(w), str(t), str(v), str(c)) for w, t, v, c in asked)
    return _known(rows) if rows else NOKNOWN


__all__ = ["PLAIN_REASON", "known_from"]
