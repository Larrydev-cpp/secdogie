"""Cross-process input-ownership arbiter -- the baton the hybrid nodes pass.

secdogie's in-process `_INPUT_LOCK` (agent/secdogie_agent/actions.py) guarantees
that within ONE process only one desktop actor drives the mouse/keyboard at a
time. The hybrid game setup runs two SEPARATE processes that share the one
physical mouse/keyboard:

  - node A: secdogie's 2D UI logistics (open inventory, craft, hotbar, potions,
    reconnect) -- deterministic macros;
  - node B: the real-time combat controller (relative mouse-look + fire).

A `threading.Lock` cannot span processes, so ownership is arbitrated here with
an OS-level file lock (via the `filelock` library: fcntl on POSIX, msvcrt on
Windows). Two properties matter and are why this is a lock, not just a message:

  1. Mutual exclusion -- exactly one node holds the baton, so the two processes
     can never inject input into the same instant and corrupt each other (the
     cross-process version of the in-process input lock).
  2. Death-safety -- the OS drops the lock automatically when the holder's
     process exits, so a crash mid-combat (node B) cannot wedge the input
     forever; node A can reclaim it.

Handoff is a baton, not a broadcast. Node B's trigger fires -> B calls
`request_yield()` -> node A, between atomic steps (never mid-click), sees
`yield_requested()` and `release()`s -> B `acquire()`s. B's acquire *succeeding*
is itself the proof A released -- the same invariant as actions.execute's
activate-inside-lock design, where the next owner starting is the confirmation
the previous one let go.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock, Timeout


class InputArbiter:
    """A shared, cross-process token for "who may drive the mouse/keyboard now".

    Every node constructs its own `InputArbiter` pointing at the same
    `lock_dir`; the OS file lock underneath is what actually serializes them.
    """

    def __init__(self, lock_dir: str | os.PathLike[str] | None = None):
        base = Path(lock_dir) if lock_dir is not None else Path.home() / ".secdogie" / "handoff"
        base.mkdir(parents=True, exist_ok=True)
        # The OS lock file is the single source of truth for ownership. The two
        # sidecar files are advisory signaling only (owner id for observability,
        # a pending yield request); losing/staling them can never hand the same
        # baton to two nodes, because that is the OS lock's job, not theirs.
        self._lock = FileLock(str(base / "input.lock"))
        self._owner_file = base / "owner"
        self._yield_file = base / "yield.request"
        self._node_id: str | None = None

    @property
    def held(self) -> bool:
        """True if THIS arbiter instance currently holds the baton."""
        return self._lock.is_locked

    def acquire(self, node_id: str, *, timeout: float | None = None) -> bool:
        """Take exclusive input ownership for `node_id`.

        Blocks up to `timeout` seconds (None = block indefinitely, 0 = a single
        non-blocking try). Returns True on success, False if `timeout` elapsed
        with another node still holding it. Acquiring is itself proof the
        previous owner released -- the OS lock cannot be held by two processes at
        once.
        """
        try:
            self._lock.acquire(timeout=-1 if timeout is None else timeout)
        except Timeout:
            return False
        self._node_id = node_id
        self._write(self._owner_file, node_id)
        self._clear_yield()  # the handoff happened; any pending "please yield" is now moot
        return True

    def release(self) -> None:
        """Give up the baton. A no-op if this instance isn't holding it, so it's
        safe to call unconditionally in a finally/cleanup path."""
        if not self._lock.is_locked:
            return
        self._node_id = None
        self._safe_unlink(self._owner_file)
        self._lock.release()

    @contextmanager
    def hold(self, node_id: str, *, timeout: float | None = None) -> Iterator[InputArbiter]:
        """Scope the baton to a `with` block: acquire on entry, always release on
        exit (even on exception). Raises TimeoutError if it can't be acquired in
        time, so the caller never proceeds believing it owns input when it does
        not."""
        if not self.acquire(node_id, timeout=timeout):
            raise TimeoutError(f"{node_id!r} could not take input ownership within {timeout}s")
        try:
            yield self
        finally:
            self.release()

    def request_yield(self, requester: str) -> None:
        """Ask the current owner to release so `requester` can take over. Returns
        immediately; the owner sees it via `yield_requested()` and releases at
        its next safe point. This is the preemption channel -- node B raising its
        hand for the baton without ripping it out mid-action."""
        self._write(self._yield_file, requester)

    def yield_requested(self) -> str | None:
        """The current owner polls this between atomic steps; returns the
        requester id if a node is waiting for the baton, else None."""
        return self._read(self._yield_file)

    def clear_yield(self) -> None:
        """Drop any pending yield request (also done automatically on acquire)."""
        self._clear_yield()

    def owner(self) -> str | None:
        """Best-effort: the id that last recorded ownership. Advisory only -- it
        can be stale after a crash, since the OS lock (not this file) is the
        source of truth. Use it for logs/status, never for correctness."""
        return self._read(self._owner_file)

    # -- small fs helpers: atomic write, tolerant read/unlink -----------------

    def _write(self, path: Path, text: str) -> None:
        # Write-then-rename so a concurrent reader never sees a half-written id.
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text)
        os.replace(tmp, path)

    def _read(self, path: Path) -> str | None:
        try:
            text = path.read_text().strip()
        except FileNotFoundError:
            return None
        return text or None

    def _safe_unlink(self, path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def _clear_yield(self) -> None:
        self._safe_unlink(self._yield_file)
