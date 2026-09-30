"""Anti-entropy replication for the journal -- transport-agnostic.

Two nodes converge by exchanging a have-vector (each author's highest seq) and
then the events the other is missing. Because journal.merge() is idempotent and
self-verifying, this converges regardless of message order or duplication, and a
star topology (every node syncs with a hub) replicates the whole set correctly,
generalising to node-to-node without change.

These are pure builders over a Journal: the actual bytes ride whatever transport
carries them (the fleet TCP or the C tunnel). `sync_round` wires two in-process
journals together for tests, with no sockets.
"""
from __future__ import annotations

import json
from typing import Any

HAVE = "journal_have"
EVENTS = "journal_events"


def have_message(journal: Any) -> dict:
    """{kind, heads: {author: seq}} -- what this node already has."""
    return {"kind": HAVE, "heads": journal.heads()}


def events_since(journal: Any, remote_heads: dict) -> list[dict]:
    """The events this journal holds that a peer with `remote_heads` lacks."""
    out: list[dict] = []
    for author, local_seq in journal.heads().items():
        have = int(remote_heads.get(author, 0)) if isinstance(remote_heads, dict) else 0
        if local_seq > have:
            out.extend(journal.since(author, have))
    return out


def events_batch(journal: Any, remote_heads: dict, max_bytes: int) -> tuple[list[dict], bool]:
    """Like `events_since`, but at most about ``max_bytes`` of JSON, so one
    message fits a datagram. Returns (events, more): ``more`` when something was
    left out for a later round. Each author's events stay a prefix in seq order
    (``journal.merge`` needs a chain without gaps), and authors are taken round
    robin, so an author the peer cannot accept never crowds out the rest. At
    least one event goes out whenever any is due, so every round makes progress."""
    heads = remote_heads if isinstance(remote_heads, dict) else {}
    queues = []
    for author, local_seq in sorted(journal.heads().items()):
        have = int(heads.get(author, 0))
        if local_seq > have:
            queues.append(journal.since(author, have))
    out: list[dict] = []
    size = 0
    while any(queues):
        for q in queues:
            if not q:
                continue
            cost = len(json.dumps(q[0], separators=(",", ":"))) + 1
            if out and size + cost > max_bytes:
                return out, True
            out.append(q.pop(0))
            size += cost
    return out, False


def events_message(events: list[dict], *, more: bool = False) -> dict:
    msg = {"kind": EVENTS, "events": events}
    if more:
        msg["more"] = True
    return msg


def respond_to_have(journal: Any, have_msg: dict, *, max_bytes: int | None = None) -> dict:
    """Given a peer's have-message, build the events-message to send back --
    with ``max_bytes``, at most about that much, flagged ``more`` if truncated."""
    heads = have_msg.get("heads") if isinstance(have_msg, dict) else {}
    if max_bytes is None:
        return events_message(events_since(journal, heads or {}))
    events, more = events_batch(journal, heads or {}, max_bytes)
    return events_message(events, more=more)


def apply_events_message(journal: Any, msg: dict) -> int:
    """Merge a received events-message; returns how many were newly accepted."""
    if not isinstance(msg, dict) or msg.get("kind") != EVENTS:
        return 0
    return journal.merge(msg.get("events") or [])


def sync_round(a: Any, b: Any) -> tuple[int, int]:
    """One full bidirectional exchange between two journals (for tests / in-proc).
    Returns (accepted_by_a, accepted_by_b)."""
    to_a = events_since(b, a.heads())
    to_b = events_since(a, b.heads())
    return a.merge(to_a), b.merge(to_b)
