"""Sessions: every request a node takes on has a bounded, explicit life.

A serving node holds state for each request it is routing; a user holds state
for each ask. Before this module that state lived in plain dicts: a node took
on as many requests as anyone sent, and a user kept every ask forever. Here it
is a ``Session`` with an explicit lifecycle in a ``SessionTable`` with hard
bounds, so memory stays bounded whatever arrives:

    open --> deciding --> dispatched --> settling --> closed
               ^                              |
               +------- retry (attempt+1) ----+

  * **admission** -- a table holds at most ``capacity`` live sessions and at
    most ``per_owner`` for one owner (one user cannot fill a node). A request
    that does not fit is refused ``busy: ...`` and nothing is kept for it;
  * **edges** -- ``advance`` refuses any move not in the table's edges, and
    ``closed`` is terminal: a late answer for a closed session finds nothing
    (``live`` returns None) and is dropped;
  * **close** -- the one way out. It drops the session from the live table and
    calls its ``release()``, so the request, decision, legs and answers it held
    are no longer referenced; only the final frame stays, for repeats;
  * **retention** -- final frames are kept in an LRU bounded by count, by
    encoded bytes, and optionally by age;
  * **expiry** -- every session carries ``expires_at``; ``expired(now)`` lists
    the live ones past it, for the owner to close.

``BoundedCache`` is the same discipline for the other per-request memories
(a model node's answers, the safety layer's equivocation memory): an LRU with
a count *and* a byte budget.

Python cannot promise that a value is gone from memory, only that nothing
references it. That is what "memory-safe" means here: bounded growth under any
input, no session acting after it closed, and secrets referenced for as short
a time as the code can manage (see ``lease.LeaseSecret``).
"""
from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

OPEN = "open"
DECIDING = "deciding"
DISPATCHED = "dispatched"
SETTLING = "settling"
SUBMITTED = "submitted"
AWAITING_DIR = "awaiting-dir"
CLOSED = "closed"

# A serving node's request: decide, send the legs, judge what comes back,
# retry on other paths or close.
SESSION_EDGES: Mapping[str, frozenset[str]] = {
    OPEN: frozenset({DECIDING, CLOSED}),
    DECIDING: frozenset({DISPATCHED, CLOSED}),
    DISPATCHED: frozenset({SETTLING, CLOSED}),
    SETTLING: frozenset({DECIDING, CLOSED}),
    CLOSED: frozenset(),
}
# A user's ask: submitted (again, on a retry), waiting for a directory in
# lockdown, closed with an outcome.
ASK_EDGES: Mapping[str, frozenset[str]] = {
    OPEN: frozenset({SUBMITTED, AWAITING_DIR, CLOSED}),
    SUBMITTED: frozenset({SUBMITTED, AWAITING_DIR, CLOSED}),
    AWAITING_DIR: frozenset({SUBMITTED, AWAITING_DIR, CLOSED}),
    CLOSED: frozenset(),
}

OUTCOME_ACCEPTED = "accepted"
OUTCOME_INTERCEPTED = "intercepted"
OUTCOME_REFUSED = "refused"
OUTCOME_EXPIRED = "expired"
OUTCOME_ABORTED = "aborted"
OUTCOME_RELAYED = "relayed"   # the lockdown forwarder hands every answer back unjudged
OUTCOMES = frozenset({OUTCOME_ACCEPTED, OUTCOME_INTERCEPTED, OUTCOME_REFUSED, OUTCOME_EXPIRED, OUTCOME_ABORTED,
                      OUTCOME_RELAYED})


class IllegalSessionEdge(RuntimeError):
    """A session move its table does not allow (closed is terminal)."""


@dataclass(eq=False)
class Session:
    key: str
    owner: str
    opened_at: float
    expires_at: float
    state: str = OPEN
    outcome: str | None = None

    def release(self) -> None:
        """Drop what the session holds. Called once, by ``SessionTable.close``."""


S = TypeVar("S", bound=Session)


def frame_size(final: Any) -> int:
    """Bytes a retained final value costs, roughly: its compact JSON."""
    try:
        return len(json.dumps(final, separators=(",", ":"), default=str, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return len(repr(final))


class BoundedCache:
    """An LRU bounded by entry count and by a byte budget. ``size(value)``
    says what an entry costs; the oldest entries go first."""

    def __init__(self, *, max_items: int, max_bytes: int, size: Callable[[Any], int] = frame_size):
        if max_items < 1 or max_bytes < 1:
            raise ValueError("a cache needs room for at least one entry")
        self.max_items = int(max_items)
        self.max_bytes = int(max_bytes)
        self._size = size
        self._items: OrderedDict[Hashable, tuple[Any, int]] = OrderedDict()
        self.bytes = 0

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, key) -> bool:
        return key in self._items

    def __getitem__(self, key):
        return self._items[key][0]

    def __setitem__(self, key, value) -> None:
        self.put(key, value)

    def get(self, key, default=None):
        hit = self._items.get(key)
        return default if hit is None else hit[0]

    def put(self, key, value) -> None:
        cost = int(self._size(value))
        self.pop(key)
        self._items[key] = (value, cost)
        self.bytes += cost
        while self._items and (len(self._items) > self.max_items or self.bytes > self.max_bytes):
            _k, (_v, c) = self._items.popitem(last=False)
            self.bytes -= c

    def touch(self, key) -> None:
        if key in self._items:
            self._items.move_to_end(key)

    def pop(self, key, default=None):
        hit = self._items.pop(key, None)
        if hit is None:
            return default
        self.bytes -= hit[1]
        return hit[0]

    def clear(self) -> None:
        self._items.clear()
        self.bytes = 0

    def keys(self):
        return list(self._items)


class SessionTable(Generic[S]):
    def __init__(self, *, capacity: int, per_owner: int | None = None, retain_count: int,
                 retain_bytes: int = 4 << 20, retain_for: float | None = None,
                 edges: Mapping[str, frozenset[str]] = SESSION_EDGES):
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self.capacity = int(capacity)
        self.per_owner = int(per_owner) if per_owner is not None else self.capacity
        self.retain_for = retain_for
        self.edges = edges
        self._live: dict[str, S] = {}
        self._owners: dict[str, int] = {}
        self._final = BoundedCache(max_items=retain_count, max_bytes=retain_bytes,
                                   size=lambda entry: frame_size(entry[1]))

    # -- live sessions ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._live)

    def admit(self, session: S) -> bool:
        """Take ``session`` on if the table has room for it and its owner."""
        if session.key in self._live:
            return False
        if len(self._live) >= self.capacity or self._owners.get(session.owner, 0) >= self.per_owner:
            return False
        self._live[session.key] = session
        self._owners[session.owner] = self._owners.get(session.owner, 0) + 1
        return True

    def live(self, key) -> S | None:
        return self._live.get(key)

    def sessions(self) -> list[S]:
        return list(self._live.values())

    def owned(self, owner: str) -> int:
        return self._owners.get(owner, 0)

    def advance(self, session: S, new: str) -> None:
        if new not in self.edges.get(session.state, frozenset()):
            raise IllegalSessionEdge(f"{session.state} -> {new}")
        if self._live.get(session.key) is not session:
            raise IllegalSessionEdge(f"{session.key[:12]} is not live")
        session.state = new

    def close(self, session: S, outcome: str, final: Any = None, *, now: float = 0.0) -> None:
        """End ``session``: off the live table, its references released, and
        ``final`` (if any) kept for repeats."""
        if outcome not in OUTCOMES:
            raise ValueError(f"unknown outcome {outcome!r}")
        self.advance(session, CLOSED)
        session.outcome = outcome
        del self._live[session.key]
        left = self._owners[session.owner] - 1
        if left:
            self._owners[session.owner] = left
        else:
            del self._owners[session.owner]
        session.release()
        if final is not None:
            self.remember(session.key, final, now=now)

    def expired(self, now: float) -> list[S]:
        """Live sessions past their ``expires_at``, oldest first."""
        return sorted((s for s in self._live.values() if s.expires_at <= now), key=lambda s: s.opened_at)

    # -- what stays after close ----------------------------------------------------------

    def remember(self, key, final: Any, *, now: float = 0.0) -> None:
        self._final.put(key, (float(now), final))

    def final(self, key) -> Any:
        hit = self._final.get(key)
        return None if hit is None else hit[1]

    def forget(self, key) -> Any:
        hit = self._final.pop(key)
        return None if hit is None else hit[1]

    def prune(self, now: float) -> None:
        """Drop retained finals older than ``retain_for``."""
        if self.retain_for is None:
            return
        for key in self._final.keys():
            at, _ = self._final.get(key)
            if now - at < self.retain_for:
                break  # oldest first: the rest are newer
            self._final.pop(key)

    def stats(self) -> dict[str, int]:
        return {"live": len(self._live), "owners": len(self._owners), "retained": len(self._final),
                "retained_bytes": self._final.bytes}


__all__ = [
    "OPEN",
    "DECIDING",
    "DISPATCHED",
    "SETTLING",
    "SUBMITTED",
    "AWAITING_DIR",
    "CLOSED",
    "SESSION_EDGES",
    "ASK_EDGES",
    "OUTCOMES",
    "OUTCOME_ACCEPTED",
    "OUTCOME_INTERCEPTED",
    "OUTCOME_REFUSED",
    "OUTCOME_EXPIRED",
    "OUTCOME_ABORTED",
    "OUTCOME_RELAYED",
    "IllegalSessionEdge",
    "Session",
    "SessionTable",
    "BoundedCache",
    "frame_size",
]
