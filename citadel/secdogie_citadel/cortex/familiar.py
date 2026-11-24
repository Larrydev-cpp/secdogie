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
    cells: np.ndarray  # (CELLS,) bool: potentiated (Gen 1 Bloom filter)
    sememe: object | None = None  # optional sememe_vsa.MeaningStore (Gen 2+ meaning sensor)
    mode: str = "bloom"  # "bloom" (Gen 1 legacy) | "sememe" (Gen 2+ meaning-level VSA)

    @classmethod
    def new(cls, *, mode: str = "bloom") -> Familiarity:
        store = None
        if mode == "sememe":
            from .sememe_vsa import MeaningStore

            store = MeaningStore()
        return cls(np.zeros(CELLS, bool), store, mode)

    @classmethod
    def with_sememe(cls, texts: Iterable[str] = ()) -> Familiarity:
        fam = cls.new(mode="sememe")
        if texts:
            fam.learn(texts)
        return fam

    def copy(self) -> Familiarity:
        store_copy = self.sememe.copy() if self.sememe is not None else None
        return Familiarity(self.cells.copy(), store_copy, self.mode)

    def mount_sememe(self, texts: Iterable[str] = ()) -> None:
        """Upgrade this familiarity sensor to meaning-level Sememe-VSA (used when descending to Gen 2+)."""
        if self.sememe is None:
            from .sememe_vsa import MeaningStore

            self.sememe = MeaningStore()
        self.mode = "sememe"
        if texts:
            self.sememe.add(texts)

    def learn(self, texts: Iterable[str]) -> int:
        seq = list(texts)
        before = int(self.cells.sum())
        for t in seq:
            for u in units(t):
                self.cells[list(_cells(u))] = True
        if self.sememe is not None:
            self.sememe.add(seq)
        return int(self.cells.sum()) - before

    def known(self, unit: str) -> bool:
        return bool(self.cells[list(_cells(unit))].all())

    def _bloom_novelty(self, text: str) -> tuple[float, tuple[str, ...]]:
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

    def novelty(self, text: str) -> tuple[float, tuple[str, ...]]:
        """(unfamiliarity 0..1, unfamiliar stretches without repeats).

        Uses meaning-level Sememe-VSA when mounted in ``sememe`` mode (Gen 2+), and the legacy
        character-unit Bloom filter in ``bloom`` mode (Gen 1). When a sentence has low sememe
        coverage (< 2 distinct concepts and not a greeting) and lexical Bloom cells are populated,
        defers to ``min(sememe_nov, bloom_nov)`` so everyday out-of-ontology nouns do not cause
        false over-caution while compositional dangers (>= 2 concepts) always use pure Sememe-VSA.
        """
        if self.mode == "sememe" and self.sememe is not None:
            nov, spans = self.sememe.novelty(text)
            if nov >= 0.25 and bool(self.cells.any()):
                from .sememe_vsa import sememe_profile

                _, n_sem, _, _, is_greet = sememe_profile(text)
                if n_sem < 2 and not is_greet:
                    b_nov, b_spans = self._bloom_novelty(text)
                    if b_nov < nov:
                        return b_nov, b_spans
            if not spans and nov >= 0.25:
                _, bloom_spans = self._bloom_novelty(text)
                spans = bloom_spans
            return nov, spans
        return self._bloom_novelty(text)

    def pack(self) -> bytes:
        return np.packbits(self.cells).tobytes()

    def pack_sememe(self) -> bytes | None:
        return self.sememe.pack() if self.sememe is not None else None

    @classmethod
    def unpack(cls, data: bytes, *, sememe_data: bytes | None = None) -> Familiarity:
        cells = np.unpackbits(np.frombuffer(data, np.uint8))[:CELLS].astype(bool)
        if sememe_data:
            from .sememe_vsa import MeaningStore

            return cls(cells, MeaningStore.unpack(sememe_data), "sememe")
        return cls(cells, None, "bloom")


__all__ = ["CELLS", "Familiarity", "units"]
