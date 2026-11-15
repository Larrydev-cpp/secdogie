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

Their reasons are counted too. Each past request is read again and what
they were after inferred the same way as now, and a goal counts when it was
plainly their reason (likely, and resting on their own words). Those counts
shift this person's priors, a Dirichlet over the written-down ones
(``world.Known.prior``).

Everything is deterministic and comes from what they said, never from
anyone else.
"""
from __future__ import annotations

from functools import lru_cache

from . import lexicon as L
from .belief import goals
from .perceive import read_frame, read_situation
from .world import NOKNOWN, NOTHING, Known, scene

PLAIN_REASON = 0.6  # a past goal counts as their reason when it was at least this likely


@lru_cache(maxsize=64)
def _known(asked: tuple[tuple[float, str, str, str], ...]) -> Known:
    held = sum(1 for _, _, verdict, _ in asked if verdict == "dissuade")
    steady = sum(1 for _, _, _, category in asked if category == "considered")
    undone = sum(1 for _, text, _, _ in asked if L.REGRET_PAST.search(text))
    counts: dict[tuple[str, str], int] = {}
    for when, text, verdict, _ in asked:
        if verdict in ("refuse", "talk"):
            continue
        sc = scene(read_frame(text), read_situation(text, now=when), NOTHING, now=when)
        if sc is None:
            continue
        top = goals(sc)[0]
        if top.p >= PLAIN_REASON and top.because and top.goal.id not in ("plain", "told"):
            key = (sc.act.id, top.goal.id)
            counts[key] = counts.get(key, 0) + 1
    return Known(held, steady, undone, tuple(sorted((a, g, n) for (a, g), n in counts.items())))


def known_from(asked) -> Known:
    """``asked``: (when, text, verdict, category) of each past request, oldest first."""
    rows = tuple((float(w), str(t), str(v), str(c)) for w, t, v, c in asked)
    return _known(rows) if rows else NOKNOWN


__all__ = ["PLAIN_REASON", "known_from"]
