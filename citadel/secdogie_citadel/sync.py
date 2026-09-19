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


def events_message(events: list[dict]) -> dict:
    return {"kind": EVENTS, "events": events}


def respond_to_have(journal: Any, have_msg: dict) -> dict:
    """Given a peer's have-message, build the events-message to send back."""
    heads = have_msg.get("heads") if isinstance(have_msg, dict) else {}
    return events_message(events_since(journal, heads or {}))


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
