"""The hippocampus: experiences kept at once, recalled by how alike the neural activity is.

Every lesson the cortex gets while awake (a judgment of the external model it watched, an answer or a
correction from the person) is kept here immediately, together with the pattern of firing the cortex had
while reading it. When something new is read, the most alike patterns are recalled (pattern completion, as
in a modern Hopfield network: a softmax over the similarities), and what was learned with them is blended
into the answers. So one lesson is enough for the same thing, or something read the same way, to be
answered right at once; sleep later consolidates it into the synapses.

Why neural coordinates alone blur (like human biological memory) — and how SecDogie makes them concrete:
  A distributed firing vector is ideal as a fast (<1 ms) content-addressable index, but terrible as a
  lossy container for discrete facts (exact transfer amounts, exact file paths, exact recipients, or
  cryptographic ledger hashes). Following Hippocampal Indexing Theory (Teyler & Rudy), SecDogie separates
  the *coordinate index* (2N SNN firing pattern + optional 128-byte Sememe-VSA coordinate) from an
  immutable, lossless ``ConcreteAnchor`` stored at that coordinate. During retrieval, Stage 1 locates
  nearby coordinates by neural similarity, and Stage 2 dereferences each ``ConcreteAnchor`` and applies
  exact ``slot_compatibility`` verification (e.g. ensuring a 500 CNY self-transfer never silently blurs
  into a 50,000 CNY transfer to an unverified stranger).

The keys depend on the synapses, so after every sleep the kept experiences are read again with the new
synapses and their keys renewed while their ``ConcreteAnchor`` payloads remain lossless and intact.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace

import numpy as np

from . import questions as Q
from .curriculum import Lesson, targets
from .episode_vsa import (
    DEFAULT_CLEANUP_MIN_SIM,
    CleanupCodebook,
    CleanupMatch,
    bind_episode,
    decode_episode,
    decode_role,
)
from .sememe_vsa import DIM as SEMEME_DIM
from .sememe_vsa import PACKED_BYTES as SEMEME_PACKED_BYTES
from .sememe_vsa import _POPCNT_NP as SEMEME_POPCNT_NP
from .sememe_vsa import is_passive_voice, sememe_profile


@dataclass(frozen=True)
class ConcreteAnchor:
    """Lossless structured episode anchored at a neural/VSA coordinate.

    Combines two complementary silicon memory mechanisms:
    1. **Self-decoding 128-byte VSA coordinate (``vsa_coord``)**: binds up to 6 core slots
       (``tool``, ``entity``, ``param``, ``action``, ``object_class``, ``outcome``) orthogonally
       so high-frequency facts decode directly from the coordinate via Unbinding + Cleanup.
    2. **Lossless Payload Vault**: keeps arbitrarily long verbatim quotes, full paths/command
       lines, timestamps, and Ed25519 ``journal_ref`` hashes with zero truncation.
    """

    verbatim: str = ""
    tool: str = ""
    entities: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    quantities: tuple[tuple[str, float, str], ...] = ()  # (label, value, unit)
    facts: tuple[tuple[str, str], ...] = ()  # exact (key, value) slots
    timestamp_ms: int = 0
    journal_ref: str = ""
    vsa_coord: bytes = b""

    def has_structured_slots(self) -> bool:
        return bool(self.tool or self.entities or self.paths or self.quantities or self.facts)

    def core_slots(self) -> dict[str, str]:
        """Extract up to 6 high-frequency core slots suitable for high-SNR 128-byte VSA binding."""
        slots: dict[str, str] = {}
        if self.tool.strip():
            slots["tool"] = self.tool.strip()
        if self.paths:
            slots["entity"] = self.paths[0].strip()
        elif self.entities:
            slots["entity"] = self.entities[0].strip()
        if self.quantities:
            label, val, unit = self.quantities[0]
            slots["param"] = f"{label.strip()}:{val:g}{unit.strip()}"
        for k, v in self.facts:
            nk = k.strip().lower()
            nv = v.strip()
            if nk in ("action", "object_class", "outcome") and nv and nk not in slots:
                slots[nk] = nv
        return slots


@dataclass(frozen=True)
class ConcreteHit:
    """A single dereferenced concrete memory returned alongside neural recall."""

    lesson_index: int
    neural_similarity: float
    slot_compatibility: float
    effective_score: float
    mismatches: tuple[str, ...]
    anchor: ConcreteAnchor


def _norm_set(items: Sequence[str]) -> frozenset[str]:
    return frozenset(x.strip().lower() for x in items if x and x.strip())


def slot_compatibility(
    query: ConcreteAnchor | None,
    stored: ConcreteAnchor | None,
) -> tuple[float, tuple[str, ...]]:
    """Verify exact discrete slot alignment between a query and a coordinate-anchored memory.

    Zero regex and zero word tables: pure set/numeric/key-value comparison.
    Returns ``(score_in_0_to_1, mismatch_reasons)``. When either side has no structured
    slots, returns ``(1.0, ())`` so legacy callers remain unaffected.
    """
    if query is None or stored is None:
        return 1.0, ()
    if not query.has_structured_slots() or not stored.has_structured_slots():
        return 1.0, ()

    score = 1.0
    mismatches: list[str] = []

    # 1. Exact tool capability alignment
    q_tool = query.tool.strip().lower()
    s_tool = stored.tool.strip().lower()
    if q_tool and s_tool and q_tool != s_tool:
        score *= 0.35
        mismatches.append(f"tool:{q_tool}!={s_tool}")

    # 2. Exact resource paths / targets
    q_paths = _norm_set(query.paths)
    s_paths = _norm_set(stored.paths)
    if q_paths and s_paths:
        overlap = len(q_paths & s_paths) / max(1, len(q_paths | s_paths))
        if overlap == 0.0:
            score *= 0.25
            mismatches.append("paths:disjoint")
        elif overlap < 1.0:
            score *= 0.45 + 0.55 * overlap
            mismatches.append(f"paths:partial({overlap:.2f})")

    # 3. Exact actors / recipients / hosts
    q_ent = _norm_set(query.entities)
    s_ent = _norm_set(stored.entities)
    if q_ent and s_ent:
        overlap = len(q_ent & s_ent) / max(1, len(q_ent | s_ent))
        if overlap == 0.0:
            score *= 0.25
            mismatches.append("entities:disjoint")
        elif overlap < 1.0:
            score *= 0.45 + 0.55 * overlap
            mismatches.append(f"entities:partial({overlap:.2f})")

    # 4. Exact numerical magnitudes (prevents 500 vs 50,000 blurring)
    if query.quantities and stored.quantities:
        s_map: dict[tuple[str, str], float] = {}
        for label, val, unit in stored.quantities:
            s_map[(label.strip().lower(), unit.strip().lower())] = float(val)
        for label, q_val, unit in query.quantities:
            key = (label.strip().lower(), unit.strip().lower())
            if key not in s_map:
                continue
            s_val = s_map[key]
            q_abs = abs(float(q_val))
            s_abs = abs(float(s_val))
            if q_abs == s_abs:
                continue
            hi = max(q_abs, s_abs, 1e-9)
            lo = min(q_abs, s_abs)
            ratio = hi / max(lo, 1e-9)
            if ratio >= 10.0:
                score *= 0.15
                mismatches.append(f"quantity:{key[0]}({q_val:g} vs {s_val:g})")
            elif ratio >= 2.0:
                score *= 0.45
                mismatches.append(f"quantity:{key[0]}({q_val:g} vs {s_val:g})")
            else:
                score *= 0.85

    # 5. Exact discrete boolean / categorical facts (e.g. counterparty_verified, reversible, negated)
    if query.facts and stored.facts:
        s_facts = {k.strip().lower(): v.strip().lower() for k, v in stored.facts if k.strip()}
        for k, v in query.facts:
            nk = k.strip().lower()
            if nk in s_facts and s_facts[nk] != v.strip().lower():
                score *= 0.20
                mismatches.append(f"fact:{nk}({v.strip().lower()}!={s_facts[nk]})")

    return float(max(0.0, min(1.0, score))), tuple(mismatches)


@dataclass
class Recall:
    similarity: float  # how alike the best match is (cosine, 0..1)
    weight: float  # how much the recall counts in the answer (0..1)
    shares: np.ndarray  # (answers,) the recalled answers, as shares within each question
    mask: np.ndarray  # (answers,) where the recalled lessons said something
    lessons: tuple[int, ...] = ()  # the kept lessons it rests on, best first
    concrete: tuple[ConcreteHit, ...] = ()  # dereferenced lossless anchors at those coordinates


@dataclass
class Hippocampus:
    lessons: list[Lesson] = field(default_factory=list)
    keys: np.ndarray | None = None  # (kept, pattern) unit vectors
    anchors: list[ConcreteAnchor] = field(default_factory=list)  # 1:1 concrete records aligned with lessons
    codebook: CleanupCodebook = field(default_factory=CleanupCodebook)  # local entity codebook for VSA unbinding
    capacity: int = 20000
    _sememe_keys: np.ndarray | None = field(default=None, repr=False)
    _sememe_meta: np.ndarray | None = field(default=None, repr=False)  # (kept, 4): [n_sem, neg, is_query, is_greet]

    def __post_init__(self) -> None:
        self._sync_anchors()

    def _sync_anchors(self) -> None:
        """Guarantee ``anchors`` stays 1:1 aligned with ``lessons`` even when deserialized from older snapshots."""
        if len(self.anchors) < len(self.lessons):
            for lesson in self.lessons[len(self.anchors):]:
                self.anchors.append(ConcreteAnchor(verbatim=lesson.text))
        elif len(self.anchors) > len(self.lessons):
            del self.anchors[len(self.lessons):]
        if self._sememe_keys is not None and len(self._sememe_keys) != len(self.lessons):
            self._sememe_keys = None
            self._sememe_meta = None

    def _ensure_sememe_index(self) -> None:
        """Lazily build the 128-byte Sememe-VSA index aligned 1:1 with ``lessons``."""
        n = len(self.lessons)
        if n == 0:
            self._sememe_keys = np.zeros((0, SEMEME_PACKED_BYTES), dtype=np.uint8)
            self._sememe_meta = np.zeros((0, 5), dtype=np.int16)
            return
        if self._sememe_keys is not None and self._sememe_meta is not None and len(self._sememe_keys) == n:
            return
        packed_rows = bytearray()
        meta = np.zeros((n, 5), dtype=np.int16)
        for i, lesson in enumerate(self.lessons):
            pk, n_sem, neg, is_q, is_g = sememe_profile(lesson.text)
            is_p = is_passive_voice(lesson.text)
            packed_rows.extend(pk)
            meta[i] = (n_sem, int(neg), int(is_q), int(is_g), int(is_p))
        self._sememe_keys = np.frombuffer(bytes(packed_rows), dtype=np.uint8).reshape(n, SEMEME_PACKED_BYTES).copy()
        self._sememe_meta = meta

    def __len__(self) -> int:
        return len(self.lessons)

    def disputed_count(self) -> int:
        return sum(1 for x in self.lessons if x.disputed)

    def keep(self, lesson: Lesson, key: np.ndarray, *, anchor: ConcreteAnchor | None = None) -> None:
        self._sync_anchors()
        eff_anchor = anchor if anchor is not None else ConcreteAnchor(verbatim=lesson.text)
        # Auto-bind core <= 6 slots into the 128-byte VSA coordinate + register into CleanupCodebook
        core = eff_anchor.core_slots()
        if core:
            if not eff_anchor.vsa_coord:
                coord_bytes = bind_episode(core, codebook=self.codebook)
                eff_anchor = replace(eff_anchor, vsa_coord=coord_bytes)
            else:
                for r, v in core.items():
                    self.codebook.register(r, v)
        self.lessons.append(lesson)
        self.anchors.append(eff_anchor)
        k = key[None, :].astype(np.float16).astype(np.float32)  # kept at the precision it is stored at
        self.keys = k if self.keys is None or self.keys.shape[1] != k.shape[1] else np.concatenate([self.keys, k])
        if self._sememe_keys is not None and self._sememe_meta is not None:
            pk, n_sem, neg, is_q, is_g = sememe_profile(lesson.text)
            is_p = is_passive_voice(lesson.text)
            row_k = np.frombuffer(pk, dtype=np.uint8).reshape(1, SEMEME_PACKED_BYTES)
            row_m = np.array([[n_sem, int(neg), int(is_q), int(is_g), int(is_p)]], dtype=np.int16)
            self._sememe_keys = np.concatenate([self._sememe_keys, row_k], axis=0)
            self._sememe_meta = np.concatenate([self._sememe_meta, row_m], axis=0)
        if len(self.lessons) > self.capacity:  # forget the oldest disputed or non-person lesson first
            drop = next((i for i, x in enumerate(self.lessons) if x.disputed), None)
            if drop is None:
                drop = next((i for i, x in enumerate(self.lessons) if x.source != "person"), 0)
            del self.lessons[drop]
            del self.anchors[drop]
            self.keys = np.delete(self.keys, drop, axis=0)
            if self._sememe_keys is not None and self._sememe_meta is not None:
                self._sememe_keys = np.delete(self._sememe_keys, drop, axis=0)
                self._sememe_meta = np.delete(self._sememe_meta, drop, axis=0)

    def decode_coordinate(
        self,
        lesson_index: int,
        *,
        role: str | None = None,
        min_sim: float = DEFAULT_CLEANUP_MIN_SIM,
    ) -> CleanupMatch | dict[str, CleanupMatch]:
        """Directly decode concrete slot(s) out of a lesson's 128-byte VSA coordinate via Unbinding + Cleanup."""
        self._sync_anchors()
        anchor = self.anchors[lesson_index]
        if not anchor.vsa_coord:
            if role is not None:
                return CleanupMatch(role.strip().lower(), "", 0.0, 0.0, 0.0, False)
            return {}
        if role is not None:
            return decode_role(anchor.vsa_coord, role, self.codebook, min_sim=min_sim)
        return decode_episode(anchor.vsa_coord, self.codebook, min_sim=min_sim)

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
        self._sync_anchors()
        keep_idx: list[int] = []
        updated: list[Lesson] = []
        updated_anchors: list[ConcreteAnchor] = []
        evicted = 0
        for i, lesson in enumerate(self.lessons):
            if not lesson.disputed:
                keep_idx.append(i)
                updated.append(lesson)
                updated_anchors.append(self.anchors[i])
                continue
            next_trace = lesson.priority_trace * decay
            if next_trace < min_trace:
                evicted += 1
                continue
            keep_idx.append(i)
            updated.append(replace(lesson, priority_trace=next_trace))
            updated_anchors.append(self.anchors[i])
        if evicted or len(updated) == len(self.lessons):
            self.lessons = updated
            self.anchors = updated_anchors
            if self.keys is not None:
                self.keys = self.keys[keep_idx] if keep_idx else None
            if self._sememe_keys is not None and self._sememe_meta is not None:
                self._sememe_keys = self._sememe_keys[keep_idx] if keep_idx else None
                self._sememe_meta = self._sememe_meta[keep_idx] if keep_idx else None
        return evicted

    def rekey(self, keys: np.ndarray) -> None:
        self._sync_anchors()
        self.keys = keys.astype(np.float16).astype(np.float32) if len(self.lessons) else None
        self._sememe_keys = None
        self._sememe_meta = None

    def _sememe_similarities(self, text: str) -> np.ndarray | None:
        """Channel B: vectorized 128-byte Sememe-VSA Hamming similarity with polarity, voice & speech-act guards."""
        if not text or not text.strip() or SEMEME_POPCNT_NP is None:
            return None
        q_pk, q_nsem, q_neg, q_query, q_greet = sememe_profile(text)
        # Require at least 2 distinct sememes (e.g. ACT+OBJ) or explicit greeting so vague 1-concept text does not blur
        if q_nsem < 2 and not q_greet:
            return None
        q_passive = is_passive_voice(text)
        self._ensure_sememe_index()
        if self._sememe_keys is None or self._sememe_meta is None or len(self._sememe_keys) == 0:
            return None
        q_arr = np.frombuffer(q_pk, dtype=np.uint8)
        dists = SEMEME_POPCNT_NP[np.bitwise_xor(self._sememe_keys, q_arr)].sum(axis=1)
        sim = np.maximum(0.0, 1.0 - 2.0 * dists.astype(np.float32) / float(SEMEME_DIM))
        s_nsem = self._sememe_meta[:, 0]
        s_neg = self._sememe_meta[:, 1]
        s_query = self._sememe_meta[:, 2]
        s_greet = self._sememe_meta[:, 3]
        s_passive = self._sememe_meta[:, 4]
        # Polarity guard: negated vs affirmative must never cross-recall ("别删文件" vs "删文件")
        sim = np.where(s_neg != int(q_neg), sim * 0.20, sim)
        # Voice guard: victim reports ("my account got hacked") must never cross-recall active commands ("crack her password")
        sim = np.where(s_passive != int(q_passive), sim * 0.20, sim)
        # Speech-act guard: questions ("how to defend against phishing?") vs imperative commands
        sim = np.where(s_query != int(q_query), sim * 0.45, sim)
        # Specificity guard: avoid matching sparse 1-concept lessons unless greeting
        sim = np.where((s_nsem < 2) & (s_greet == 0), sim * 0.50, sim)
        return sim

    def recall(
        self,
        patterns: np.ndarray,
        *,
        sharpness: float,
        trust: float,
        top: int = 8,
        query_anchors: Sequence[ConcreteAnchor | None] | None = None,
        texts: Sequence[str] | None = None,
        sememe_trust: float = 0.72,
    ) -> list[Recall]:
        """Dual-Channel Hippocampal Recall: SNN 2N firing pattern + optional 128-byte Sememe-VSA channel,
        followed by Stage 2 lossless ``ConcreteAnchor`` slot verification."""
        self._sync_anchors()
        if self.keys is None or not len(self.lessons) or self.keys.shape[1] != patterns.shape[1]:
            return [Recall(0.0, 0.0, np.zeros(Q.OUTPUTS), np.zeros(Q.OUTPUTS)) for _ in range(len(patterns))]
        sims = patterns @ self.keys.T
        target, mask, _ = targets(self.lessons)
        reliability = np.array([max(0.0, min(1.0, x.priority_trace)) * 0.5 if x.disputed else 1.0 for x in self.lessons], np.float32)
        out = []
        for row_idx, snn_row in enumerate(sims):
            row = snn_row
            if texts is not None and row_idx < len(texts):
                sem_row = self._sememe_similarities(texts[row_idx])
                if sem_row is not None:
                    # Calibrate Sememe-VSA similarity onto the SNN trust scale with a 0.65 blending cap
                    # so semantic recall supports (rather than blindly steamrolling) bimodal cortex readings.
                    max_cap = trust + 0.65 * (1.0 - trust)
                    sem_hi = trust + (sem_row - sememe_trust) * (max_cap - trust) / max(1e-6, 1.0 - sememe_trust)
                    sem_lo = sem_row * (trust / max(1e-6, sememe_trust))
                    sem_mapped = np.clip(np.where(sem_row >= sememe_trust, sem_hi, sem_lo), 0.0, max_cap).astype(np.float32)
                    row = np.maximum(snn_row, sem_mapped)

            q_anchor = query_anchors[row_idx] if query_anchors is not None and row_idx < len(query_anchors) else None
            idx = np.argsort(-row)[:top]

            # Stage 2: dereference lossless ConcreteAnchors and verify exact discrete slots
            compat = np.ones(len(idx), dtype=np.float32)
            hits: list[ConcreteHit] = []
            for pos, lesson_i in enumerate(idx):
                li = int(lesson_i)
                stored_anchor = self.anchors[li]
                c_score, mismatches = slot_compatibility(q_anchor, stored_anchor)
                compat[pos] = c_score
                sim_val = float(row[li])
                hits.append(
                    ConcreteHit(
                        lesson_index=li,
                        neural_similarity=sim_val,
                        slot_compatibility=c_score,
                        effective_score=sim_val * c_score,
                        mismatches=mismatches,
                        anchor=stored_anchor,
                    )
                )

            # Re-rank concrete hits by effective score (neural similarity * exact slot alignment)
            order = np.argsort(-np.array([h.effective_score for h in hits], dtype=np.float32))
            idx = idx[order]
            compat = compat[order]
            hits = [hits[int(o)] for o in order]

            eff_sims = row[idx] * compat
            w = np.exp(sharpness * (eff_sims - eff_sims.max())) * reliability[idx] * compat
            wsum = float(w.sum())
            if wsum <= 1e-9:
                out.append(Recall(0.0, 0.0, np.zeros(Q.OUTPUTS), np.zeros(Q.OUTPUTS), (), tuple(hits)))
                continue
            w /= wsum
            shares = (w[:, None] * target[idx]).sum(axis=0)
            m = (w[:, None] * mask[idx]).sum(axis=0)
            best = float(eff_sims[0])
            best_rel = float(reliability[idx[0]])
            weight = float(np.clip((best - trust) / max(1e-6, 1.0 - trust), 0.0, 1.0)) * best_rel
            out.append(Recall(best, weight, shares, m, tuple(int(i) for i in idx), tuple(hits)))
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


__all__ = ["ConcreteAnchor", "ConcreteHit", "Hippocampus", "Recall", "blend", "slot_compatibility"]
