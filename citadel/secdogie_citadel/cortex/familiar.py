"""Familiarity: has the cortex met this before?

The brain tells the familiar from the new before it knows what the new thing is (familiarity cells in the
perirhinal cortex answer less to what has been seen before). This is that signal. Every reading unit the
cortex has learned from (a run of letters or digits, a pair of neighbouring characters in scripts written
without spaces) potentiates three of a million familiarity cells, chosen by a hash. A unit whose cells are
not all potentiated has never been met.

Two uses:

- **Not sure of what it has not met.** The more of a sentence is unfamiliar, the less the cortex's answers
  are trusted, so going ahead on a sentence full of the unknown needs more than it would otherwise.
- **What to find out.** The unfamiliar units are what the Socratic gate looks up or asks about: the part it
  did not understand, not the whole sentence.

Nothing is looked up here: it is a memory of what was met, and it only grows.
"""
from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

from .senses import clean

CELLS = 1 << 20
PER_UNIT = 3


def _wide(ch: str) -> bool:
    """A character of a script written without spaces between words (Han, kana, Hangul)."""
    return unicodedata.east_asian_width(ch) in ("W", "F") and ch.isalpha()


def units(text: str) -> list[str]:
    """The reading units of ``text``: runs of letters or digits (digits folded to 0), and single characters in
    scripts written without spaces (each of which carries meaning of its own)."""
    s = clean(text)
    out: list[str] = []
    run = ""
    for ch in s + " ":
        if _wide(ch):
            if run:
                out.append(run)
                run = ""
            out.append(ch)
        elif ch.isalnum():
            run += "0" if ch.isdigit() else ch
        elif run:
            out.append(run)
            run = ""
    return out


def _cells(unit: str) -> tuple[int, ...]:
    d = hashlib.blake2b(unit.encode("utf-8"), digest_size=4 * PER_UNIT, person=b"sd-familiar").digest()
    return tuple(int.from_bytes(d[4 * i:4 * i + 4], "little") % CELLS for i in range(PER_UNIT))


@dataclass
class Familiarity:
    cells: np.ndarray  # (CELLS,) bool: potentiated

    @classmethod
    def new(cls) -> Familiarity:
        return cls(np.zeros(CELLS, bool))

    def learn(self, texts: Iterable[str]) -> int:
        before = int(self.cells.sum())
        for t in texts:
            for u in units(t):
                self.cells[list(_cells(u))] = True
        return int(self.cells.sum()) - before

    def known(self, unit: str) -> bool:
        return bool(self.cells[list(_cells(unit))].all())

    def novelty(self, text: str) -> tuple[float, tuple[str, ...]]:
        """(share of the reading units never met, the stretches of the text they are in, without repeats)."""
        us = units(text)
        if not us:
            return 0.0, ()
        new = [i for i, u in enumerate(us) if not self.known(u)]
        spans: list[str] = []
        for i in new:
            u = us[i]
            if len(u) == 1 and _wide(u):  # a character: show it with its neighbour, the way it is read
                nxt = us[i + 1] if i + 1 < len(us) and len(us[i + 1]) == 1 and _wide(us[i + 1]) else ""
                prv = us[i - 1] if i > 0 and len(us[i - 1]) == 1 and _wide(us[i - 1]) else ""
                u = u + nxt if nxt else prv + u
            if u not in spans:
                spans.append(u)
        return len(new) / len(us), tuple(spans)

    def pack(self) -> bytes:
        return np.packbits(self.cells).tobytes()

    @classmethod
    def unpack(cls, data: bytes) -> Familiarity:
        return cls(np.unpackbits(np.frombuffer(data, np.uint8))[:CELLS].astype(bool))


__all__ = ["CELLS", "Familiarity", "units"]
