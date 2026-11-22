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
from dataclasses import dataclass, field, replace

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

    def disputed_count(self) -> int:
        return sum(1 for x in self.lessons if x.disputed)

    def keep(self, lesson: Lesson, key: np.ndarray) -> None:
        self.lessons.append(lesson)
        k = key[None, :].astype(np.float16).astype(np.float32)  # kept at the precision it is stored at
        self.keys = k if self.keys is None or self.keys.shape[1] != k.shape[1] else np.concatenate([self.keys, k])
        if len(self.lessons) > self.capacity:  # forget the oldest disputed or non-person lesson first
            drop = next((i for i, x in enumerate(self.lessons) if x.disputed), None)
            if drop is None:
                drop = next((i for i, x in enumerate(self.lessons) if x.source != "person"), 0)
            del self.lessons[drop]
            self.keys = np.delete(self.keys, drop, axis=0)

    def corroborate(self, text: str, answers: dict | None = None, *, source: str = "person") -> int:
        """Clear ``disputed`` on kept memories corroborated by human or consistent awake confirmation."""
        needle = text.strip().lower()
        cleared = 0
        for i, lesson in enumerate(self.lessons):
            if not lesson.disputed:
                continue
            same_text = lesson.text.strip().lower() == needle
            same_answers = answers is not None and all(lesson.answers.get(k) == v for k, v in answers.items() if k in lesson.answers)
            if same_text or same_answers:
                self.lessons[i] = replace(lesson, disputed=False, priority_trace=0.0, source=source if same_text else lesson.source)
                cleared += 1
        return cleared

    def decay_disputed(self, *, decay: float = 0.7, min_trace: float = 0.15) -> int:
        """Decay ``priority_trace`` on disputed lessons during sleep and evict uncorroborated ones."""
        keep_idx: list[int] = []
        updated: list[Lesson] = []
        evicted = 0
        for i, lesson in enumerate(self.lessons):
            if not lesson.disputed:
                keep_idx.append(i)
                updated.append(lesson)
                continue
            next_trace = lesson.priority_trace * decay
            if next_trace < min_trace:
                evicted += 1
                continue
            keep_idx.append(i)
            updated.append(replace(lesson, priority_trace=next_trace))
        if evicted or len(updated) == len(self.lessons):
            self.lessons = updated
            if self.keys is not None:
                self.keys = self.keys[keep_idx] if keep_idx else None
        return evicted

    def rekey(self, keys: np.ndarray) -> None:
        self.keys = keys.astype(np.float16).astype(np.float32) if len(self.lessons) else None

    def recall(self, patterns: np.ndarray, *, sharpness: float, trust: float, top: int = 8) -> list[Recall]:
        """For each pattern, what the most alike kept experiences say."""
        if self.keys is None or not len(self.lessons) or self.keys.shape[1] != patterns.shape[1]:
            return [Recall(0.0, 0.0, np.zeros(Q.OUTPUTS), np.zeros(Q.OUTPUTS)) for _ in range(len(patterns))]
        sims = patterns @ self.keys.T
        target, mask, _ = targets(self.lessons)
        reliability = np.array([max(0.0, min(1.0, x.priority_trace)) * 0.5 if x.disputed else 1.0 for x in self.lessons], np.float32)
        out = []
        for row in sims:
            idx = np.argsort(-row)[:top]
            w = np.exp(sharpness * (row[idx] - row[idx].max())) * reliability[idx]
            wsum = float(w.sum())
            if wsum <= 1e-9:
                out.append(Recall(0.0, 0.0, np.zeros(Q.OUTPUTS), np.zeros(Q.OUTPUTS)))
                continue
            w /= wsum
            shares = (w[:, None] * target[idx]).sum(axis=0)
            m = (w[:, None] * mask[idx]).sum(axis=0)
            best = float(row[idx[0]])
            best_rel = float(reliability[idx[0]])
            weight = float(np.clip((best - trust) / max(1e-6, 1.0 - trust), 0.0, 1.0)) * best_rel
            out.append(Recall(best, weight, shares, m, tuple(int(i) for i in idx)))
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
