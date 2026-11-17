"""Senses: what the cortex is told, turned into spikes.

**Reading.** The cortex reads a sentence one character at a time. Each character, each pair of neighbouring
characters and each run of three make a small set of input neurons fire, the way neurons in the brain's
visual word-form area answer to single letters and to letter pairs. Which neurons a character (or a pair)
drives is fixed and random: it is drawn from a hash of the characters. So there is no table of words,
nothing is looked up, and every script is read the same way. What the firing *means* is learned.

**Context.** What surrounds the sentence (that it is the middle of the night, for one) is a small group of
input neurons that keep firing steadily while the sentence is read.

The result is a block of input spikes, ticks x sentences x input neurons. Sentences in one block are
aligned at their ends, so every sentence finishes on the same tick and is followed by the same few quiet
ticks in which the answer neurons settle.
"""
from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

MAX_CHARS = 200


@dataclass(frozen=True)
class Reading:
    """How the senses are wired. Fixed for a lineage: changing it would rewire every input synapse."""

    n_read: int = 2048  # input neurons that answer to characters
    n_context: int = 64  # input neurons that carry the context
    k: tuple[int, int, int] = (5, 5, 4)  # neurons per character, per pair, per run of three
    ticks_per_char: int = 1
    settle: int = 12  # quiet ticks after the last character

    @property
    def n_in(self) -> int:
        return self.n_read + self.n_context

    def view(self) -> dict:
        return {"n_read": self.n_read, "n_context": self.n_context, "k": list(self.k), "ticks_per_char": self.ticks_per_char,
                "settle": self.settle}

    @classmethod
    def of(cls, d: dict) -> Reading:
        return cls(int(d["n_read"]), int(d["n_context"]), tuple(int(x) for x in d["k"]), int(d["ticks_per_char"]),
                   int(d["settle"]))


@lru_cache(maxsize=1 << 17)
def _cells(gram: str, order: int, n: int, k: int, offset: int) -> tuple[int, ...]:
    digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=2 * max(k, 1), person=b"secdogie-senses" + bytes([order])).digest()
    return tuple(offset + int.from_bytes(digest[2 * i:2 * i + 2], "little") % n for i in range(k))


def clean(text: str) -> str:
    """Unicode's own folding (full-width to half-width, case) and single spaces. No meaning is added."""
    s = " ".join(unicodedata.normalize("NFKC", text).casefold().split())
    return s[:MAX_CHARS]


def ticks(text: str, r: Reading) -> int:
    return len(clean(text)) * r.ticks_per_char + r.settle


def spikes(text: str, r: Reading) -> list[tuple[int, ...]]:
    """The input neurons that fire for each character of ``text``, in order."""
    s = clean(text)
    out = []
    for i in range(len(s)):
        cells: list[int] = []
        for order, k in enumerate(r.k, start=1):
            if k and i + 1 >= order:
                cells.extend(_cells(s[i + 1 - order:i + 1], order, r.n_read, k, 0))
        out.append(tuple(sorted(set(cells))))
    return out


def context_cells(flags: Sequence[str], r: Reading) -> tuple[int, ...]:
    cells: list[int] = []
    for f in flags:
        cells.extend(_cells(f, 9, r.n_context, 8, r.n_read))
    return tuple(sorted(set(cells)))


def block(texts: Sequence[str], contexts: Sequence[Sequence[str]], r: Reading, length: int | None = None) -> np.ndarray:
    """Input spikes for a group of sentences: an array (ticks, sentences, input neurons) of 0 and 1."""
    rows = [spikes(t, r) for t in texts]
    T = length or max(len(x) for x in rows) * r.ticks_per_char + r.settle
    X = np.zeros((T, len(texts), r.n_in), dtype=np.float32)
    for b, (cells, ctx) in enumerate(zip(rows, contexts, strict=True)):
        start = T - r.settle - len(cells) * r.ticks_per_char
        for i, c in enumerate(cells):
            if c:
                X[start + i * r.ticks_per_char, b, list(c)] = 1.0
        if ctx:
            on = list(context_cells(ctx, r))
            X[max(0, start):T:2, b, on] = 1.0  # tonic: every other tick, all through the reading
    return X


__all__ = ["MAX_CHARS", "Reading", "block", "clean", "context_cells", "spikes", "ticks"]
