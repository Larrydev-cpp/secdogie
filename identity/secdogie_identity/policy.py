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

A long-running process picks up records added to the store after it started with
``TrustPolicy.refresh()`` -- usually from ``start_refresher`` -- so an operator
who appends a co-signed record (``secdogie-identity revoke-apply``) reaches every
process that shares the store without restarting any of them. ``load_trust_policy``
is the one loader every command line uses.
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

    def stamp(self) -> tuple[int, int] | None:
        """(mtime_ns, size) of the store, or None when it does not exist yet --
        enough to tell whether it changed without reading it."""
        try:
            st = self.path.stat()
        except FileNotFoundError:
            return None
        return (st.st_mtime_ns, st.st_size)

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
        self._store_stamp = None
        if store is not None:
            self._store_stamp = store.stamp()
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

    def refresh(self) -> frozenset[str]:
        """Apply records added to the store since the last look (by another
        process, or by ``revoke-apply``). Returns the DIDs this newly revoked and
        notifies subscribers once with them. Cheap when nothing changed: one
        ``stat`` of the store, no read. Records are verified exactly as ``apply``
        verifies them, so a forged line in the store changes nothing."""
        if self._store is None:
            return frozenset()
        stamp = self._store.stamp()
        with self._lock:
            if stamp == self._store_stamp:
                return frozenset()
            self._store_stamp = stamp
        newly: set[str] = set()
        for obj in self._store.load():
            newly |= self._merge(obj, persist=False)[0]
        if newly:
            with self._lock:
                subs = list(self._subs)
            for cb in subs:
                cb(frozenset(newly))
        return frozenset(newly)

    def on_change(self, cb: ChangeCb) -> None:
        """Subscribe to newly-revoked DIDs (for cache eviction / self-halt)."""
        with self._lock:
            self._subs.append(cb)

    def records(self) -> list[RevocationRecord]:
        with self._lock:
            return list(self._records.values())


def halt_on_self_revocation(newly_revoked, self_did: str, stop_actions) -> bool:
    """If ``self_did`` is among ``newly_revoked`` DIDs, run each stop action once
    and return True; otherwise do nothing and return False.

    This is the node's own authorization lifecycle: when the mesh's masters have
    revoked *this* node, it winds itself down cleanly, exactly as if the operator
    had stopped it locally. Pure and side-effect-only through ``stop_actions`` so
    it can be unit-tested headlessly; the caller decides how to exit the process
    (a clean ``SystemExit(0)``) once it returns True. Each action is best-effort:
    one that raises does not stop the others, so a half-torn-down node still
    completes its shutdown."""
    if self_did not in newly_revoked:
        return False
    for action in stop_actions:
        try:
            action()
        except Exception:  # noqa: BLE001 - a failing stop step must not block the halt
            pass
    return True


def start_refresher(policy: TrustPolicy, *, interval: float = 5.0) -> threading.Event:
    """Call ``policy.refresh()`` every ``interval`` seconds on a daemon thread.
    Set the returned Event to stop it. A refresh that raises (the store is
    briefly unreadable, say) is retried on the next tick rather than ending the
    thread, so revocation keeps flowing."""
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(interval):
            try:
                policy.refresh()
            except Exception:  # noqa: BLE001 - try again next tick
                pass

    threading.Thread(target=loop, daemon=True, name="revocation-refresh").start()
    return stop


def load_trust_policy(allow_path, *, masters_path=None, revocations_path=None,
                      refresh_interval: float | None = 5.0):
    """The allowlist a command line should use.

    Without ``masters_path`` it is the plain ``Allowlist`` from ``allow_path``,
    exactly as before. With it, a ``TrustPolicy`` over that allowlist, reading
    revocations from ``revocations_path`` when given and -- unless
    ``refresh_interval`` is None -- re-reading that store on a background
    thread. ``revocations_path`` without ``masters_path`` is refused: a store
    nobody can verify would silently do nothing."""
    from .allowlist import Allowlist

    if revocations_path and not masters_path:
        raise ValueError("--revocations needs --masters (revocations are verified against the masters)")
    allowlist = Allowlist.load(allow_path)
    if not masters_path:
        return allowlist
    store = RevocationStore(revocations_path) if revocations_path else None
    policy = TrustPolicy(allowlist, masters=MasterSet.load(masters_path), store=store)
    if store is not None and refresh_interval is not None:
        start_refresher(policy, interval=refresh_interval)
    return policy


__all__ = ["RevocationStore", "TrustPolicy", "halt_on_self_revocation", "start_refresher",
           "load_trust_policy"]
