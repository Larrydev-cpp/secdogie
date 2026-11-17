"""What the companion learns about one person from their own record.

The record is the person's past requests as the gate journaled them: when, the verdict, what decided it,
and the cortex's answers about what kind of change it was and why. Three things are counted:

- **held**: weighty steps the gate held back;
- **steady**: times they came back after a cooling-off, calm, and went ahead; their own say then weighs more
  and the next cooling-off is shorter;
- **undone**: times something the companion did was undone or restored afterwards.

Their reasons are counted too: a reason the cortex was sure of, for a weighty change, counts for that kind
of change. Everything comes from their own record.
"""
from __future__ import annotations

from collections.abc import Iterable

from .world import NOKNOWN, Known

PLAIN_REASON = 0.6


def known_from(records: Iterable[dict]) -> Known:
    """``records``: journal entries with ``verdict``, ``because``, and the cortex's ``answers`` ({question: [answer, sure]})."""
    held = steady = undone = 0
    counts: dict[tuple[str, str], int] = {}
    for r in records:
        verdict, because = r.get("verdict", ""), r.get("because", "")
        held += verdict == "dissuade"
        steady += because == "waited"
        undone += bool(r.get("undone"))
        a = r.get("answers") or {}
        effect, why = a.get("effect"), a.get("why")
        if verdict in ("proceed", "dissuade") and effect and why and why[0] not in ("unsaid", "other") and why[1] >= PLAIN_REASON:
            counts[(effect[0], why[0])] = counts.get((effect[0], why[0]), 0) + 1
    if not (held or steady or undone or counts):
        return NOKNOWN
    return Known(held, steady, undone, tuple(sorted((a, g, n) for (a, g), n in counts.items())))


__all__ = ["PLAIN_REASON", "known_from"]
