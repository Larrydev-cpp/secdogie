"""Episode-VSA: Orthogonal Role-Filler Binding, Unbinding & Cleanup Memory in 128 bytes.

Unlike biological brains — where superposed synaptic memories blur together because there is no
exact inverse unbinding or clean nearest-neighbor codebook snap — a 1,024-dim bipolar VSA
({-1, +1}^1024, packed into 128 bytes) can decode discrete episodic slots directly out of the
coordinate itself:

1. **Write (Orthogonal Binding + Superposition)**:
   Given up to ``MAX_EPISODE_SLOTS = 6`` core slots ``(role, value)`` (e.g. ``action``,
   ``object_class``, ``entity``, ``param``, ``tool``, ``outcome``), bind each filler vector with
   its orthogonal role vector via elementwise Hadamard product and quantize with parity
   tie-breaking into a single 128-byte coordinate::

       M = sign_parity(sum_{k in slots} (R_k ⊙ V_k))

2. **Read (Exact Bipolar Unbinding)**:
   Because bipolar vectors are self-inverse (``R_k ⊙ R_k = 1``), querying a specific slot
   (e.g. "which exact file was touched?") is a single bitwise XNOR in packed 128-byte space::

       V_hat_k = M ⊙ R_k

3. **Zero-Noise Cleanup Memory (Entity Codebook Snap)**:
   ``V_hat_k`` carries bounded crosstalk noise from the other 4–5 slots. Snapping ``V_hat_k``
   to its nearest neighbor in the local ``CleanupCodebook`` via XOR+POPCNT Hamming similarity
   eliminates crosstalk completely — achieving ~100% exact recovery (10–12 sigma above random
   codebook entries) while keeping the coordinate itself to 128 bytes.

4. **Hybrid Key–Payload Architecture**:
   Keep high-frequency core slots (<= 6) directly decodable inside the 128-byte VSA coordinate,
   and pair with ``ConcreteAnchor`` payload storage for arbitrarily long verbatim text, full
   command lines, and cryptographic journal references.

Zero regex and zero pretrained weights: pure deterministic ``blake2b`` projections + bitwise ops.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

try:
    import numpy as np
except ImportError:  # pragma: no cover - pure-Python fallback
    np = None  # type: ignore[assignment]

from .sememe_vsa import (
    _POPCNT_NP,
    _POPCNT_PY,
    DIM,
    PACKED_BYTES,
    _bipolar_vec,
    hamming_similarity_bytes,
    quantize_pack,
)

# Fixed 6-slot budget keeps SNR at 10–12 sigma in 1,024-dim bipolar space (~100% cleanup accuracy).
EPISODE_ROLES: tuple[str, ...] = (
    "action",
    "object_class",
    "entity",
    "param",
    "tool",
    "outcome",
)
MAX_EPISODE_SLOTS = 6
DEFAULT_CLEANUP_MIN_SIM = 0.16  # > 5.1 sigma above random noise floor (1/sqrt(1024) = 0.03125)
_NOISE_STD = 1.0 / (DIM**0.5)

_PACKED_CACHE: dict[str, bytes] = {}


def _norm_role(role: str) -> str:
    return role.strip().lower()


def _norm_value(value: str) -> str:
    return value.strip()


def role_vector(role: str):
    """Return the deterministic {-1, +1}^1024 bipolar vector for an episode role."""
    return _bipolar_vec(f"EP_ROLE:{_norm_role(role)}")


def value_vector(role: str, value: str):
    """Return the deterministic {-1, +1}^1024 bipolar vector for a concrete slot filler."""
    return _bipolar_vec(f"EP_VAL:{_norm_role(role)}:{_norm_value(value)}")


def role_packed(role: str) -> bytes:
    """128-byte packed binary representation of ``role_vector(role)``."""
    tag = f"EP_ROLE:{_norm_role(role)}"
    cached = _PACKED_CACHE.get(tag)
    if cached is not None:
        return cached
    packed = quantize_pack(_bipolar_vec(tag))
    _PACKED_CACHE[tag] = packed
    return packed


def value_packed(role: str, value: str) -> bytes:
    """128-byte packed binary representation of ``value_vector(role, value)``."""
    tag = f"EP_VAL:{_norm_role(role)}:{_norm_value(value)}"
    cached = _PACKED_CACHE.get(tag)
    if cached is not None:
        return cached
    packed = quantize_pack(_bipolar_vec(tag))
    _PACKED_CACHE[tag] = packed
    return packed


def unbind_role(coord: bytes, role: str) -> bytes:
    """Invert ``role`` out of a 128-byte episode coordinate via exact bipolar XNOR (M ⊙ R_k)."""
    if len(coord) != PACKED_BYTES:
        raise ValueError(f"expected {PACKED_BYTES}-byte episode coordinate, got {len(coord)}")
    r_bytes = role_packed(role)
    if np is not None:
        cm = np.frombuffer(coord, dtype=np.uint8)
        cr = np.frombuffer(r_bytes, dtype=np.uint8)
        return np.bitwise_not(np.bitwise_xor(cm, cr)).tobytes()
    return bytes((~(a ^ b)) & 0xFF for a, b in zip(coord, r_bytes, strict=True))


@dataclass(frozen=True)
class CleanupMatch:
    """Result of snapping a noisy unbound vector to the local Entity Codebook."""

    role: str
    value: str
    similarity: float  # bipolar cosine similarity in [0, 1]
    margin: float  # gap to second-best codebook candidate
    z_score: float  # standard deviations above random 1024-dim noise floor (std = 1/32)
    matched: bool  # True when similarity >= min_sim (rejects empty/absent roles)


@dataclass
class _RoleBook:
    values: list[str] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    keys: list[bytes] = field(default_factory=list)
    matrix: object = field(default=None, repr=False)

    def add(self, value: str, key: bytes) -> bool:
        if value in self.seen:
            return False
        self.seen.add(value)
        self.values.append(value)
        self.keys.append(key)
        self.matrix = None
        return True

    def ensure_matrix(self):
        if np is None:
            return None
        if self.matrix is None:
            if not self.keys:
                self.matrix = np.zeros((0, PACKED_BYTES), dtype=np.uint8)
            else:
                self.matrix = np.frombuffer(b"".join(self.keys), dtype=np.uint8).reshape(len(self.keys), PACKED_BYTES).copy()
        return self.matrix


@dataclass
class CleanupCodebook:
    """Role-partitioned Entity Codebook for zero-noise VSA cleanup via XOR+POPCNT."""

    _books: dict[str, _RoleBook] = field(default_factory=dict)

    def register(self, role: str, value: str) -> bool:
        nr = _norm_role(role)
        nv = _norm_value(value)
        if not nr or not nv:
            return False
        book = self._books.setdefault(nr, _RoleBook())
        return book.add(nv, value_packed(nr, nv))

    def register_many(self, role: str, values: Iterable[str]) -> int:
        added = 0
        for v in values:
            if self.register(role, v):
                added += 1
        return added

    def size(self, role: str | None = None) -> int:
        if role is not None:
            book = self._books.get(_norm_role(role))
            return len(book.values) if book is not None else 0
        return sum(len(b.values) for b in self._books.values())

    def roles(self) -> tuple[str, ...]:
        return tuple(self._books.keys())

    def cleanup(
        self,
        role: str,
        unbound_packed: bytes,
        *,
        min_sim: float = DEFAULT_CLEANUP_MIN_SIM,
    ) -> CleanupMatch:
        """Snap a noisy unbound 128-byte vector to the closest registered entity for ``role``."""
        nr = _norm_role(role)
        book = self._books.get(nr)
        if book is None or not book.values:
            return CleanupMatch(nr, "", 0.0, 0.0, 0.0, False)

        if np is not None and _POPCNT_NP is not None:
            mat = book.ensure_matrix()
            q = np.frombuffer(unbound_packed, dtype=np.uint8)
            dists = _POPCNT_NP[np.bitwise_xor(mat, q)].sum(axis=1)
            sims = np.maximum(0.0, 1.0 - 2.0 * dists.astype(np.float32) / DIM)
            best_idx = int(np.argmax(sims))
            best_sim = float(sims[best_idx])
            if len(sims) > 1:
                # Second-best candidate similarity for margin diagnostics
                part = np.partition(sims, -2)
                second_sim = float(part[-2])
            else:
                second_sim = 0.0
            best_val = book.values[best_idx]
        else:
            scored = [(hamming_similarity_bytes(k, unbound_packed), v) for k, v in zip(book.keys, book.values, strict=True)]
            scored.sort(key=lambda pair: pair[0], reverse=True)
            best_sim, best_val = scored[0]
            second_sim = scored[1][0] if len(scored) > 1 else 0.0

        margin = max(0.0, best_sim - second_sim)
        z_score = best_sim / _NOISE_STD
        matched = best_sim >= min_sim
        return CleanupMatch(
            role=nr,
            value=best_val if matched else "",
            similarity=best_sim,
            margin=margin,
            z_score=z_score,
            matched=matched,
        )


def bind_episode(
    slots: Mapping[str, str],
    *,
    codebook: CleanupCodebook | None = None,
    max_slots: int = MAX_EPISODE_SLOTS,
) -> bytes:
    """Orthogonally bind up to ``max_slots`` (default 6) concrete slots into a 128-byte coordinate.

    Raises ``ValueError`` if ``slots`` is empty or exceeds ``max_slots`` so single-coordinate
    superposition never degrades into biological-style blurring.
    """
    cleaned: list[tuple[str, str]] = []
    for raw_role, raw_val in slots.items():
        nr = _norm_role(raw_role)
        nv = _norm_value(raw_val)
        if nr and nv:
            cleaned.append((nr, nv))
    if not cleaned:
        raise ValueError("bind_episode requires at least one non-empty (role, value) slot")
    if len(cleaned) > max_slots:
        raise ValueError(
            f"episode coordinate holds at most {max_slots} bound slots to preserve cleanup SNR "
            f"(got {len(cleaned)}); offload extra/long fields to ConcreteAnchor payload"
        )

    if codebook is not None:
        for nr, nv in cleaned:
            codebook.register(nr, nv)

    if np is not None:
        acc = np.zeros(DIM, dtype=np.float32)
        for nr, nv in cleaned:
            acc += role_vector(nr) * value_vector(nr, nv)
        return quantize_pack(acc)

    acc_py = [0.0] * DIM
    for nr, nv in cleaned:
        rv = role_vector(nr)
        vv = value_vector(nr, nv)
        for idx in range(DIM):
            acc_py[idx] += rv[idx] * vv[idx]
    return quantize_pack(acc_py)


def decode_role(
    coord: bytes,
    role: str,
    codebook: CleanupCodebook,
    *,
    min_sim: float = DEFAULT_CLEANUP_MIN_SIM,
) -> CleanupMatch:
    """Unbind ``role`` from a 128-byte coordinate and snap via ``codebook`` cleanup."""
    unbound = unbind_role(coord, role)
    return codebook.cleanup(role, unbound, min_sim=min_sim)


def decode_episode(
    coord: bytes,
    codebook: CleanupCodebook,
    *,
    roles: Iterable[str] | None = None,
    min_sim: float = DEFAULT_CLEANUP_MIN_SIM,
) -> dict[str, CleanupMatch]:
    """Decode all matched concrete slots directly out of a 128-byte episode coordinate."""
    target_roles = tuple(roles) if roles is not None else tuple(dict.fromkeys((*EPISODE_ROLES, *codebook.roles())))
    decoded: dict[str, CleanupMatch] = {}
    for r in target_roles:
        match = decode_role(coord, r, codebook, min_sim=min_sim)
        if match.matched:
            decoded[match.role] = match
    return decoded


__all__ = [
    "CleanupCodebook",
    "CleanupMatch",
    "DEFAULT_CLEANUP_MIN_SIM",
    "EPISODE_ROLES",
    "MAX_EPISODE_SLOTS",
    "bind_episode",
    "decode_episode",
    "decode_role",
    "role_packed",
    "role_vector",
    "unbind_role",
    "value_packed",
    "value_vector",
]
