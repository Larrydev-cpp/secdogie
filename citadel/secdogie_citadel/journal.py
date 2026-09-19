"""A signed, append-only event journal -- the Citadel state substrate.

Each node appends events it authors; every event is Ed25519-signed by its author
DID (secdogie-identity) and chained to that author's previous event, so the log
is both authentic (only the holder of a DID's key can write as that DID) and
tamper-evident (altering any past event breaks its author's chain). Events from
different authors are merged idempotently and read back in a deterministic total
order, so two nodes that have seen the same set of events derive the same state
-- without a central writer.

Design choices:
  * One hash chain PER AUTHOR (prev_hash -> the author's previous entry_hash),
    the same shape as agent/secdogie_agent/trace.py, reusing
    secdogie_identity.canonical for the exact byte encoding a signature covers.
  * Total order across authors is (lamport, ts, author, seq): a Lamport clock
    gives causal-ish ordering, ts and (author, seq) make it a deterministic tie
    break. Each event is authored by exactly one node, so a total-ordered log is
    sufficient and no CRDT is needed yet (add an LWW-Map on top only when two
    nodes must concurrently mutate the same key).
  * merge() is idempotent and self-verifying: re-feeding events is a no-op, and
    a forged/tampered/unauthorized event is dropped, never stored.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import threading
import time
from typing import Any

from secdogie_identity import Allowlist, Identity, PublicIdentity, canonical

# prev_hash of an author's first event -- a fixed, checkable anchor.
GENESIS = "0" * 64

_PAYLOAD_KEYS = ("author", "seq", "lamport", "ts", "kind", "body", "prev_hash")


def _payload(event: dict) -> dict:
    return {k: event[k] for k in _PAYLOAD_KEYS}


def _entry_hash(payload: dict) -> str:
    return hashlib.sha256(canonical(payload)).hexdigest()


class Journal:
    """A per-node view of the shared event log, persisted to SQLite.

    `identity` (this node's signing key) is required to append; a read-only /
    replica journal can omit it and still merge and project others' events.
    `allowlist` (authorized author DIDs) gates merge; None accepts any
    validly-signed event."""

    def __init__(
        self,
        path: str = ":memory:",
        *,
        identity: Identity | None = None,
        allowlist: Allowlist | None = None,
        clock=time.time,
    ):
        self.identity = identity
        self.allowlist = allowlist
        self._clock = clock
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()
        self._lamport = self._max_lamport()

    def _init_db(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS journal (
                author     TEXT NOT NULL,
                seq        INTEGER NOT NULL,
                lamport    INTEGER NOT NULL,
                ts         REAL NOT NULL,
                kind       TEXT NOT NULL,
                body       TEXT NOT NULL,
                prev_hash  TEXT NOT NULL,
                entry_hash TEXT NOT NULL,
                sig        TEXT NOT NULL,
                PRIMARY KEY (author, seq)
            )
            """
        )
        self._conn.commit()

    def _max_lamport(self) -> int:
        row = self._conn.execute("SELECT MAX(lamport) AS m FROM journal").fetchone()
        return int(row["m"]) if row and row["m"] is not None else 0

    def _head(self, author: str) -> tuple[int, str]:
        row = self._conn.execute(
            "SELECT seq, entry_hash FROM journal WHERE author = ? ORDER BY seq DESC LIMIT 1",
            (author,),
        ).fetchone()
        if row is None:
            return 0, GENESIS
        return int(row["seq"]), row["entry_hash"]

    def _row_to_event(self, row: sqlite3.Row) -> dict:
        return {
            "author": row["author"],
            "seq": int(row["seq"]),
            "lamport": int(row["lamport"]),
            "ts": row["ts"],
            "kind": row["kind"],
            "body": json.loads(row["body"]),
            "prev_hash": row["prev_hash"],
            "entry_hash": row["entry_hash"],
            "sig": row["sig"],
        }

    def _insert(self, event: dict) -> None:
        self._conn.execute(
            "INSERT INTO journal (author, seq, lamport, ts, kind, body, prev_hash, entry_hash, sig) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event["author"], event["seq"], event["lamport"], event["ts"], event["kind"],
                json.dumps(event["body"], separators=(",", ":"), ensure_ascii=False),
                event["prev_hash"], event["entry_hash"], event["sig"],
            ),
        )
        self._conn.commit()

    # -- writing -------------------------------------------------------------

    def append(self, kind: str, body: Any) -> dict:
        """Author, sign, chain, and store one event; returns it."""
        if self.identity is None:
            raise RuntimeError("this journal has no identity; it cannot append (read-only replica)")
        author = self.identity.did
        with self._lock:
            head_seq, head_hash = self._head(author)
            self._lamport += 1
            payload = {
                "author": author,
                "seq": head_seq + 1,
                "lamport": self._lamport,
                "ts": self._clock(),
                "kind": kind,
                "body": body,
                "prev_hash": head_hash,
            }
            sig = base64.b64encode(self.identity.sign(canonical(payload))).decode("ascii")
            event = {**payload, "entry_hash": _entry_hash(payload), "sig": sig}
            self._insert(event)
            return event

    # -- merging (replication) ----------------------------------------------

    def merge(self, events: list[dict]) -> int:
        """Verify and store foreign events. Idempotent; returns how many were
        newly accepted. Sorted by (author, seq) so an author's chain is applied
        in order within the batch."""
        accepted = 0
        for e in sorted(events, key=lambda e: (str(e.get("author", "")), int(e.get("seq", 0)))):
            if self._accept(e):
                accepted += 1
        return accepted

    def _accept(self, event: dict) -> bool:
        if not all(k in event for k in (*_PAYLOAD_KEYS, "entry_hash", "sig")):
            return False
        payload = _payload(event)
        author = payload["author"]
        seq = payload["seq"]
        if not isinstance(author, str) or isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
            return False
        # authenticity: author must be a valid Ed25519 did:key, on the allowlist,
        # and the signature must verify over the canonical payload.
        try:
            pub = PublicIdentity.from_did(author)
        except ValueError:
            return False
        if self.allowlist is not None and not self.allowlist.contains(author):
            return False
        try:
            sig = base64.b64decode(event["sig"], validate=True)
        except (ValueError, TypeError):
            return False
        if not pub.verify(canonical(payload), sig):
            return False
        if event["entry_hash"] != _entry_hash(payload):
            return False
        with self._lock:
            if self._conn.execute(
                "SELECT 1 FROM journal WHERE author = ? AND seq = ?", (author, seq)
            ).fetchone() is not None:
                return False  # duplicate or conflicting (author, seq) -> idempotent no-op
            head_seq, head_hash = self._head(author)
            if seq != head_seq + 1 or payload["prev_hash"] != head_hash:
                return False  # gap, replay below head, or broken chain -> drop
            self._insert(event)
            if payload["lamport"] > self._lamport:
                self._lamport = payload["lamport"]
        return True

    # -- reading -------------------------------------------------------------

    def heads(self) -> dict[str, int]:
        """{author: highest seq} -- the have-vector for anti-entropy."""
        rows = self._conn.execute("SELECT author, MAX(seq) AS s FROM journal GROUP BY author").fetchall()
        return {row["author"]: int(row["s"]) for row in rows}

    def since(self, author: str, after_seq: int) -> list[dict]:
        """This author's events with seq > after_seq, in seq order."""
        rows = self._conn.execute(
            "SELECT * FROM journal WHERE author = ? AND seq > ? ORDER BY seq", (author, after_seq)
        ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def events(self) -> list[dict]:
        """All events in the deterministic total order (lamport, ts, author, seq)."""
        rows = self._conn.execute(
            "SELECT * FROM journal ORDER BY lamport, ts, author, seq"
        ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def verify(self) -> tuple[bool, str | None]:
        """Re-derive every author's chain and re-check every signature.
        Returns (ok, reason); reason names the first broken event."""
        by_author: dict[str, list[sqlite3.Row]] = {}
        for row in self._conn.execute("SELECT * FROM journal ORDER BY author, seq").fetchall():
            by_author.setdefault(row["author"], []).append(row)
        for author, rows in by_author.items():
            try:
                pub = PublicIdentity.from_did(author)
            except ValueError:
                return False, f"{author}: not a valid did:key"
            prev = GENESIS
            for i, row in enumerate(rows):
                event = self._row_to_event(row)
                payload = _payload(event)
                if payload["seq"] != i + 1:
                    return False, f"{author}#{payload['seq']}: seq out of order"
                if payload["prev_hash"] != prev:
                    return False, f"{author}#{payload['seq']}: chain broken"
                if event["entry_hash"] != _entry_hash(payload):
                    return False, f"{author}#{payload['seq']}: content does not match its hash"
                try:
                    if not pub.verify(canonical(payload), base64.b64decode(event["sig"])):
                        return False, f"{author}#{payload['seq']}: bad signature"
                except (ValueError, TypeError):
                    return False, f"{author}#{payload['seq']}: unreadable signature"
                prev = event["entry_hash"]
        return True, None

    def close(self) -> None:
        self._conn.close()
