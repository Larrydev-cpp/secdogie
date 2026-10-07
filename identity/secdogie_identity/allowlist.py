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

    def replace(self, dids) -> None:
        """Swap in a new membership at once (a re-read of the file). One
        assignment, so a reader sees the old set or the new one, never half."""
        new = set(dids)
        for did in new:
            pubkey_from_did(did)
        self._dids = new

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


def append_authorized(path: str | os.PathLike, did: str, *, label: str = "") -> bool:
    """Add ``did`` to the allowlist file at ``path``, durably: appended (never
    rewritten), then fsync'd. False, and nothing written, when the file already
    lists it. ``label`` is the human note after the DID; it must stay on one
    line. The file is created (owner-only) if missing."""
    pubkey_from_did(did)
    if any(c in label for c in "\r\n"):
        raise ValueError("an allowlist label must be one line")
    p = Path(path)
    if p.exists() and Allowlist.load(p).contains(did):
        return False
    line = f"{_AUTHORIZED_KEY} = {did}" + (f"  # {label}" if label else "") + "\n"
    fd = os.open(p, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        size = os.fstat(fd).st_size
        if size and os.pread(fd, 1, size - 1) != b"\n":
            line = "\n" + line
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    return True


class AllowlistWatcher:
    """Keep a live allowlist in step with its file: a long-running node picks
    up an App that ``secdogie-node pair`` (another process) just enrolled, and
    drops one whose line was removed, without a restart. ``target`` is the
    ``Allowlist`` (or ``TrustPolicy``) the node already shares with its
    transport and sessions. Cheap when nothing changed (one ``stat``). A file
    that does not parse leaves the current membership in place."""

    def __init__(self, path: str | os.PathLike, target, *, on_change=None):
        self.path = Path(path)
        self.target = target
        self.on_change = on_change
        self._stamp = self._stat()

    def _stat(self):
        try:
            st = self.path.stat()
        except FileNotFoundError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def refresh(self) -> tuple[frozenset[str], frozenset[str]] | None:
        """Re-read the file when it changed. Returns (added, removed), or None
        when nothing was applied."""
        stamp = self._stat()
        if stamp == self._stamp or stamp is None:
            return None
        try:
            fresh = Allowlist.load(self.path).dids()
        except (OSError, ValueError):
            return None  # half-written or broken: keep what we have, look again next time
        self._stamp = stamp
        before = set(self.target.dids())
        self.target.replace(fresh)
        after = set(self.target.dids())
        change = (frozenset(after - before), frozenset(before - after))
        if (change[0] or change[1]) and self.on_change is not None:
            self.on_change(*change)
        return change

    def start(self, interval: float = 2.0):
        """Refresh every ``interval`` seconds on a daemon thread; set the
        returned Event to stop it."""
        import threading

        stop = threading.Event()

        def loop() -> None:
            while not stop.wait(interval):
                try:
                    self.refresh()
                except Exception:  # noqa: BLE001 - look again next tick
                    pass

        threading.Thread(target=loop, daemon=True, name="allowlist-watch").start()
        return stop


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

