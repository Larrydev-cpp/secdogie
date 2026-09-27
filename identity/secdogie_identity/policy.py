"""TrustPolicy: an allowlist minus the revoked (R1.2 / 3.0).

Every authenticated entry point in the mesh -- ``verify_payload``, the transport
frame decoders, ``journal.merge()``, the fleet message verifier -- already gates
the signer through a ``.contains(did)`` call on an allowlist. ``TrustPolicy``
implements that same one-method interface, answering ``contains`` as "on the
allowlist AND not revoked". So revocation plugs into every one of those paths by
construction, with no change to their code: a revoked DID is refused exactly the
way an unauthorized one always was -- silently, with no reply.

``RevocationStore`` persists the records a node has accepted, so a restart does
not forget who was revoked (revocation is permanent). Records are Master-signed
(see revocation.py), so the store trusts the signatures, not the file's origin.
"""
from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from pathlib import Path

from .revocation import MasterSet, RevocationRecord, verify_revocation


class RevocationStore:
    """Append-only, one JSON object per line. Durable because revocation must
    survive restarts; corrupt lines are skipped rather than fatal."""

    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)

    def load(self) -> list[dict]:
        if not self.path.exists():
            return []
        out: list[dict] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue  # a torn or garbled line never blocks the rest
            if isinstance(obj, dict):
                out.append(obj)
        return out

    def append(self, obj: dict) -> None:
        line = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)


ChangeCb = Callable[[frozenset], None]


class TrustPolicy:
    """An allowlist narrowed by Master-signed revocations. Duck-typed like
    ``Allowlist`` (``contains`` / ``dids``), so it drops into any component that
    takes an allowlist.

    ``masters`` and ``store`` are optional so a TrustPolicy can wrap a plain
    allowlist before revocation is configured; without ``masters`` it can hold
    no revocations (``apply`` refuses), which fails closed rather than open."""

    def __init__(self, allowlist, *, masters: MasterSet | None = None, store: RevocationStore | None = None):
        if allowlist is None:
            raise ValueError("a trust policy needs an allowlist")
        self._allowlist = allowlist
        self._masters = masters
        self._store = store
        self._revoked: set[str] = set()
        self._records: dict[str, RevocationRecord] = {}  # record_id -> record (de-dup)
        self._subs: list[ChangeCb] = []
        self._lock = threading.RLock()
        self.version = 0
        if store is not None:
            for obj in store.load():
                self._merge(obj, persist=False)

    # -- the allowlist interface (what .contains callers rely on) ------------

    def contains(self, did: str) -> bool:
        with self._lock:
            return self._allowlist.contains(did) and did not in self._revoked

    def dids(self) -> set[str]:
        with self._lock:
            return {d for d in self._allowlist.dids() if d not in self._revoked}

    def __len__(self) -> int:
        return len(self.dids())

    # -- revocation ----------------------------------------------------------

    def is_revoked(self, did: str) -> bool:
        with self._lock:
            return did in self._revoked

    def revoked(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._revoked)

    def apply(self, obj: dict) -> frozenset[str]:
        """Verify and merge one revocation record. Returns the DIDs newly revoked
        by it (empty if it does not verify or adds nothing). Subscribers are
        notified with that set. Idempotent: a record already applied, by
        ``record_id``, is a no-op."""
        newly, notify = self._merge(obj, persist=True)
        if newly:
            for cb in notify:
                cb(newly)
        return newly

    def _merge(self, obj: dict, *, persist: bool) -> tuple[frozenset[str], list[ChangeCb]]:
        if self._masters is None:
            return frozenset(), []
        record = verify_revocation(obj, self._masters)
        if record is None:
            return frozenset(), []
        with self._lock:
            if record.record_id in self._records:
                return frozenset(), []
            self._records[record.record_id] = record
            newly = frozenset(record.revoked - self._revoked)
            self._revoked |= record.revoked
            if persist and self._store is not None:
                self._store.append(obj)
            if newly:
                self.version += 1
            subs = list(self._subs)
        return newly, (subs if newly else [])

    def on_change(self, cb: ChangeCb) -> None:
        """Subscribe to newly-revoked DIDs (for cache eviction / self-halt)."""
        with self._lock:
            self._subs.append(cb)

    def records(self) -> list[RevocationRecord]:
        with self._lock:
            return list(self._records.values())


__all__ = ["RevocationStore", "TrustPolicy"]
