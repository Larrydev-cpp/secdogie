"""The hippocampus: experiences kept at once, recalled by how alike the neural activity is.

Every lesson the cortex gets while awake (a judgment of the external model it watched, an answer or a
correction from the person) is kept here immediately, together with the pattern of firing the cortex had
while reading it. When something new is read, the most alike patterns are recalled (pattern completion, as
in a modern Hopfield network: a softmax over the similarities), and what was learned with them is blended
into the answers. So one lesson is enough for the same thing, or something read the same way, to be
answered right at once; sleep later consolidates it into the synapses.

The keys depend on the synapses, so after every sleep the kept experiences are read again with the new
synapses and their keys renewed.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from . import questions as Q
from .curriculum import Lesson, targets


@dataclass
class Recall:
    similarity: float  # how alike the best match is (cosine, 0..1)
    weight: float  # how much the recall counts in the answer (0..1)
    shares: np.ndarray  # (answers,) the recalled answers, as shares within each question
    mask: np.ndarray  # (answers,) where the recalled lessons said something
    lessons: tuple[int, ...] = ()  # the kept lessons it rests on, best first


@dataclass
class Hippocampus:
    lessons: list[Lesson] = field(default_factory=list)
    keys: np.ndarray | None = None  # (kept, pattern) unit vectors
    capacity: int = 20000

    def __len__(self) -> int:
        return len(self.lessons)

    def keep(self, lesson: Lesson, key: np.ndarray) -> None:
        self.lessons.append(lesson)
        k = key[None, :].astype(np.float16).astype(np.float32)  # kept at the precision it is stored at
        self.keys = k if self.keys is None or self.keys.shape[1] != k.shape[1] else np.concatenate([self.keys, k])
        if len(self.lessons) > self.capacity:  # forget the oldest that did not come from the person
            drop = next((i for i, x in enumerate(self.lessons) if x.source != "person"), 0)
            del self.lessons[drop]
            self.keys = np.delete(self.keys, drop, axis=0)

    def rekey(self, keys: np.ndarray) -> None:
        self.keys = keys.astype(np.float16).astype(np.float32) if len(self.lessons) else None

    def recall(self, patterns: np.ndarray, *, sharpness: float, trust: float, top: int = 8) -> list[Recall]:
        """For each pattern, what the most alike kept experiences say.

        A disputed lesson (an observed answer less cautious than the cortex's own, kept only for sleep)
        never speaks here: it is recorded, not learned from, until sleep's safety gate takes it in. The
        lessons a recall rests on are still named by their place in ``lessons``.
        """
        if self.keys is None or not len(self.lessons) or self.keys.shape[1] != patterns.shape[1]:
            return [Recall(0.0, 0.0, np.zeros(Q.OUTPUTS), np.zeros(Q.OUTPUTS)) for _ in range(len(patterns))]
        usable = [i for i, x in enumerate(self.lessons) if not x.meta.get("disputed")]
        if not usable:
            return [Recall(0.0, 0.0, np.zeros(Q.OUTPUTS), np.zeros(Q.OUTPUTS)) for _ in range(len(patterns))]
        sims = patterns @ self.keys[usable].T
        target, mask, _ = targets([self.lessons[i] for i in usable])
        out = []
        for row in sims:
            idx = np.argsort(-row)[:top]
            w = np.exp(sharpness * (row[idx] - row[idx].max()))
            w /= w.sum()
            shares = (w[:, None] * target[idx]).sum(axis=0)
            m = (w[:, None] * mask[idx]).sum(axis=0)
            best = float(row[idx[0]])
            weight = float(np.clip((best - trust) / max(1e-6, 1.0 - trust), 0.0, 1.0))
            out.append(Recall(best, weight, shares, m, tuple(int(usable[i]) for i in idx)))
        return out


def blend(cortex_shares: np.ndarray, recalls: Sequence[Recall]) -> np.ndarray:
    """The cortex's answers with what was recalled mixed in, question by question, where the recall speaks."""
    out = cortex_shares.copy()
    for b, r in enumerate(recalls):
        if r.weight <= 0:
            continue
        for off, size in zip(Q.OFFSETS, Q.SIZES, strict=True):
            sl = slice(off, off + size)
            said = r.mask[sl].mean()
            if said <= 0:
                continue
            rec = r.shares[sl] / max(1e-9, r.shares[sl].sum())
            a = r.weight * min(1.0, said)
            out[b, sl] = (1 - a) * out[b, sl] + a * rec
    return out


__all__ = ["Hippocampus", "Recall", "blend"]
