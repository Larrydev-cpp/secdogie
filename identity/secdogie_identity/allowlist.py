"""The set of DIDs authorized to act as nodes / operators.

A plain `key = value` file, the same shape as the tunnel's peer list: repeatable
`authorized_did = did:key:z...` lines, `#` comments, blank lines ignored. An
optional human label may follow the DID on the same line and is discarded. This
is the membership boundary that replaces the fleet transport's "any host can
join" -- authentication (a valid signature) is necessary but not sufficient; the
signer's DID must also be on this list.
"""
from __future__ import annotations

import os
from pathlib import Path

from .did import pubkey_from_did

_AUTHORIZED_KEY = "authorized_did"


class Allowlist:
    def __init__(self, dids: set[str] | None = None):
        self._dids: set[str] = set(dids or ())

    def contains(self, did: str) -> bool:
        return did in self._dids

    def dids(self) -> set[str]:
        return set(self._dids)

    def add(self, did: str) -> None:
        pubkey_from_did(did)  # validate it is a well-formed Ed25519 did:key
        self._dids.add(did)

    def __len__(self) -> int:
        return len(self._dids)

    @classmethod
    def load(cls, path: str | os.PathLike) -> Allowlist:
        al = cls()
        for lineno, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            if "=" not in s:
                raise ValueError(f"{path}:{lineno}: expected 'key = value'")
            key, _, val = s.partition("=")
            key = key.strip()
            if key != _AUTHORIZED_KEY:
                raise ValueError(f"{path}:{lineno}: unknown key {key!r} (expected {_AUTHORIZED_KEY})")
            did = val.split()[0] if val.split() else ""
            try:
                al.add(did)
            except ValueError as exc:
                raise ValueError(f"{path}:{lineno}: {exc}") from exc
        return al


class _AllowAny:
    """Trusts every DID. For tests and throwaway local development only: it is
    never a default anywhere. A constructor that is handed ``None`` refuses to
    start; one handed ``ALLOW_ANY`` was told, in so many words, to trust anyone.
    (Truthy on purpose: code that asks ``if allowlist`` must not read it as
    "no allowlist".)"""

    def contains(self, did: str) -> bool:
        return True

    def __contains__(self, did: object) -> bool:
        return True

    def __bool__(self) -> bool:
        return True

    def __repr__(self) -> str:
        return "ALLOW_ANY"


ALLOW_ANY = _AllowAny()


def require_trust(trust, what: str):
    """Zero-trust by default: return ``trust`` unless it is ``None``, in which
    case refuse -- naming ``what`` needed it and how to say "anyone" on purpose."""
    if trust is None:
        raise ValueError(f"{what} needs an allowlist / trust policy (pass ALLOW_ANY to trust anyone, "
                         "for tests and local development only)")
    return trust

