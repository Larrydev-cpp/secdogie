"""Merkle roots, RFC 6962 style -- how two nodes compare what they hold with
one hash.

``merkle_root(leaves)`` hashes each leaf as ``SHA-256(0x00 || leaf)`` and each
interior node as ``SHA-256(0x01 || left || right)``, splitting ``n`` leaves at
the largest power of two below ``n`` (RFC 6962, section 2.1). The domain
prefixes keep a leaf from ever passing for an interior node. The empty tree's
root is ``EMPTY_ROOT`` (64 zeros) rather than RFC 6962's hash of the empty
string, so "holds nothing" reads as such on the wire.

The mesh uses it for ``ledger_root``: every heartbeat carries the root over
the ``"target|incident_id"`` pairs its node's ledger holds, so neighbours that
disagree about the latches find out from the next heartbeat and fetch what
they lack (``participant.Participant``'s anti-entropy).
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterable

EMPTY_ROOT = "0" * 64


def _leaf(data: bytes) -> bytes:
    return hashlib.sha256(b"\x00" + data).digest()


def _node(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(b"\x01" + left + right).digest()


def _root(hashes: list[bytes]) -> bytes:
    n = len(hashes)
    if n == 1:
        return hashes[0]
    k = 1 << ((n - 1).bit_length() - 1)  # the largest power of two below n
    return _node(_root(hashes[:k]), _root(hashes[k:]))


def merkle_root(leaves: Iterable[bytes | str]) -> str:
    """Hex root over ``leaves`` in the order given (str leaves as UTF-8)."""
    hashes = [_leaf(x.encode("utf-8") if isinstance(x, str) else bytes(x)) for x in leaves]
    return _root(hashes).hex() if hashes else EMPTY_ROOT


__all__ = ["EMPTY_ROOT", "merkle_root"]
