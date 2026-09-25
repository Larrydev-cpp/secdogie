"""A distributed state abstraction over the signed journal.

The journal is an append-only, signed, hash-chained *event* log. This adds a
STATE layer on top: entities (goal / task / run / capability / knowledge
reference) built by folding StateDeltas in the journal's deterministic total
order. It is honest about what it is:

    event journal != CRDT

`StateStore` is a deterministic, last-writer-wins fold (LWW by the journal's
(lamport, author, seq) order), sufficient because each entity is written by one
author at a time. The `StateDelta` / `apply` / `merge` / `materialize` interface
is shaped so a real CRDT (an LWW-Map / OR-Set) can be swapped in later without
changing callers -- but no fake CRDT is written here.

Large observations (screenshots, AX trees, DIB bitmaps, log blobs) are NEVER put
in a delta payload; a `knowledge` entity references them by content hash
({observation_id, content_hash, metadata}) instead.
"""
from __future__ import annotations

from dataclasses import dataclass, field

_OPS = frozenset({"set", "patch", "delete"})
# Entity types this layer materializes first. Not enforced (open set), but named
# so callers agree on vocabulary.
ENTITY_TYPES = ("goal", "task", "run", "step", "capability", "knowledge")

STATE_EVENT_KIND = "state"


@dataclass(frozen=True)
class StateDelta:
    author: str
    seq: int
    lamport: int
    entity_type: str
    entity_id: str
    operation: str  # set | patch | delete
    payload: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.operation not in _OPS:
            raise ValueError(f"unknown operation {self.operation!r} (expected one of {sorted(_OPS)})")
        if not self.entity_type or not self.entity_id:
            raise ValueError("entity_type and entity_id are required")

    def key(self) -> tuple[str, int]:
        return (self.author, self.seq)

    def order(self) -> tuple[int, str, int]:
        return (self.lamport, self.author, self.seq)

    def to_body(self) -> dict:
        return {
            "author": self.author, "seq": self.seq, "lamport": self.lamport,
            "entity_type": self.entity_type, "entity_id": self.entity_id,
            "operation": self.operation, "payload": self.payload,
        }


def record_state(journal, entity_type: str, entity_id: str, operation: str, payload: dict | None = None) -> dict:
    """Append a state delta to a journal as a signed `state` event. The event's
    author/seq/lamport (assigned by the journal) become the delta's identity, so
    state deltas are authentic and totally ordered like every other event."""
    if operation not in _OPS:
        raise ValueError(f"unknown operation {operation!r}")
    if not entity_type or not entity_id:
        raise ValueError("entity_type and entity_id are required")
    return journal.append(
        STATE_EVENT_KIND,
        {"entity_type": entity_type, "entity_id": entity_id, "operation": operation, "payload": payload or {}},
    )


def delta_from_event(event: dict) -> StateDelta | None:
    """Build a StateDelta from a journal `state` event, or None if it isn't one
    / is malformed. The event's own author/seq/lamport are the delta's, so state
    inherits the journal's authenticity and ordering."""
    if event.get("kind") != STATE_EVENT_KIND:
        return None
    body = event.get("body") or {}
    try:
        return StateDelta(
            author=str(event["author"]),
            seq=int(event["seq"]),
            lamport=int(event["lamport"]),
            entity_type=str(body["entity_type"]),
            entity_id=str(body["entity_id"]),
            operation=str(body["operation"]),
            payload=dict(body.get("payload") or {}),
        )
    except (KeyError, ValueError, TypeError):
        return None


class StateStore:
    """Folds StateDeltas into materialized entities. Idempotent apply/merge;
    deterministic materialize by (lamport, author, seq)."""

    def __init__(self):
        self._deltas: dict[tuple[str, int], StateDelta] = {}

    def apply(self, delta: StateDelta) -> bool:
        """Add one delta. Idempotent: a duplicate (author, seq) is a no-op."""
        if delta.key() in self._deltas:
            return False
        self._deltas[delta.key()] = delta
        return True

    def merge(self, deltas) -> int:
        return sum(1 for d in deltas if self.apply(d))

    def merge_events(self, events) -> int:
        """Merge the `state` events out of a journal event list."""
        return self.merge(d for d in (delta_from_event(e) for e in events) if d is not None)

    def materialize(self) -> dict[str, dict[str, dict]]:
        """{entity_type: {entity_id: state_dict}} after folding in total order."""
        state: dict[str, dict[str, dict]] = {}
        for d in sorted(self._deltas.values(), key=lambda d: d.order()):
            ent = state.setdefault(d.entity_type, {})
            if d.operation == "set":
                ent[d.entity_id] = dict(d.payload)
            elif d.operation == "patch":
                ent[d.entity_id] = {**ent.get(d.entity_id, {}), **d.payload}
            else:  # delete
                ent.pop(d.entity_id, None)
        # drop entity types that ended up empty
        return {etype: ents for etype, ents in state.items() if ents}

    def get(self, entity_type: str, entity_id: str) -> dict | None:
        return self.materialize().get(entity_type, {}).get(entity_id)

    def entities(self, entity_type: str) -> dict[str, dict]:
        return self.materialize().get(entity_type, {})
