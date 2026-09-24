"""Kademlia routing + iterative lookup (P2P.4): scalable, decentralized peer
discovery on top of the membership layer.

P2P.3's gossip converges the *whole* peer set (O(N)) -- fine for a small mesh.
This adds the routing layer libp2p/Kademlia describes, so a node can *find* a
specific peer's current record in O(log N) hops without knowing everyone:

  * node id = ``sha256(did)`` as a 256-bit int (peer id derived from identity,
    exactly like libp2p derives it from the public key -- the DID already carries
    the key). Distance is XOR; a k-bucket routing table groups peers by the length
    of the shared id prefix.
  * ``find_node`` iteratively queries the closest known nodes for *their* closest,
    converging on the k nodes nearest a target id. ``find_peer`` returns a target's
    own signed record once discovered.

The value carried is P2P.3's **self-signed** ``PeerRecord`` (``membership.py``), so
the security property carries over unchanged: a relaying peer can pass a record
along but cannot forge or alter it (it does not hold that DID's key), and only
allowlisted DIDs are admitted -- every record is re-verified with ``verify_record``
before it enters a table. The query transport is injected (a ``query(to_did,
target_id)`` callback wired to the DID-authenticated transport at the call site),
so this module imports no concrete transport. No new crypto, no traffic
obfuscation, no hole-punching evasion; a node id is a hash, not a key.

Pure and headless: an in-process mesh of tables + a ``query`` that returns each
node's closest exercises the whole thing with no sockets.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable

from .membership import PeerRecord, verify_record

# id space: 256-bit, matching sha256 / a did:key's identity.
ID_BITS = 256

# A query asks a peer for the records it holds closest to ``target_id``.
QueryFn = Callable[[str, int], list]


def node_id(did: str) -> int:
    """The 256-bit Kademlia node id for a DID: ``sha256(did)`` as a big-endian int.
    Deterministic and bound to the identity (the DID carries the public key)."""
    return int.from_bytes(hashlib.sha256(did.encode("utf-8")).digest(), "big")


def xor_distance(a: int, b: int) -> int:
    """Kademlia's metric: the XOR of two node ids (smaller == closer)."""
    return a ^ b


def bucket_index(self_id: int, other_id: int) -> int:
    """Which k-bucket ``other_id`` falls in relative to ``self_id``: the index of
    the most-significant differing bit (0..ID_BITS-1). Identical ids -> -1 (self,
    never stored)."""
    d = self_id ^ other_id
    return d.bit_length() - 1


class RoutingTable:
    """A node's XOR-distance routing table: k-buckets of allowlisted, self-signed
    peer records, keyed by DID. Newest-seen wins within a full bucket.

    Simplification honestly noted: classic Kademlia pings the *oldest* contact of a
    full bucket and keeps it if still alive (favoring long-lived nodes). This pure
    layer has no transport to ping with, so a full bucket evicts its least-recently
    -seen entry for a newly-seen one. The have/want gossip (P2P.3) remains the
    convergence backstop; this table is the routing accelerator."""

    def __init__(self, self_did: str, *, k: int = 20):
        self.self_did = self_did
        self.self_id = node_id(self_did)
        self._k = k
        self._buckets: dict[int, list[str]] = {}
        self._records: dict[str, PeerRecord] = {}

    def add(self, record: PeerRecord) -> bool:
        """Insert or refresh a (pre-verified) peer record. Skips self and stale
        (older ``last_seen``) updates. Returns whether the table changed."""
        did = record.did
        if did == self.self_did:
            return False
        cur = self._records.get(did)
        if cur is not None and record.last_seen < cur.last_seen:
            return False  # older record: ignore
        bidx = bucket_index(self.self_id, node_id(did))
        bucket = self._buckets.setdefault(bidx, [])
        changed = did not in self._records
        if did in bucket:
            bucket.remove(did)  # refresh LRU position
        elif len(bucket) >= self._k:
            evicted = bucket.pop(0)  # least-recently-seen out
            self._records.pop(evicted, None)
        bucket.append(did)
        self._records[did] = record
        return changed or (cur is not None and record.last_seen > cur.last_seen)

    def add_signed(self, obj, *, allowlist=None, now: float | None = None) -> bool:
        """Verify a signed record (domain/type, signature==did, allowlist,
        anti-rollforward) and, if authentic, add it. A forged or unauthorized
        record is refused, never stored."""
        rec = verify_record(obj, allowlist=allowlist, now=now)
        if rec is None:
            return False
        return self.add(rec)

    def closest(self, target_id: int, count: int) -> list[str]:
        """The ``count`` known DIDs whose node ids are XOR-nearest ``target_id``."""
        return sorted(self._records, key=lambda d: node_id(d) ^ target_id)[:count]

    def get(self, did: str) -> PeerRecord | None:
        return self._records.get(did)

    def known(self) -> list[str]:
        return sorted(self._records)

    def remove(self, did: str) -> None:
        rec = self._records.pop(did, None)
        if rec is None:
            return
        bidx = bucket_index(self.self_id, node_id(did))
        bucket = self._buckets.get(bidx)
        if bucket and did in bucket:
            bucket.remove(did)

    def __len__(self) -> int:
        return len(self._records)


def _lookup(
    target_id: int,
    *,
    seed,
    query: QueryFn,
    allowlist=None,
    alpha: int = 3,
    now: float | None = None,
    max_rounds: int = ID_BITS,
) -> tuple[list[str], dict[str, dict]]:
    """The iterative Kademlia lookup shared by ``find_node``/``find_peer``. Returns
    ``(dids_closest_to_target, {did: signed_record})``. Bounded and terminating: a
    round that discovers no new node stops it."""
    shortlist: dict[str, int] = {did: node_id(did) ^ target_id for did in seed}
    records: dict[str, dict] = {}
    queried: set[str] = set()

    for _ in range(max_rounds):
        candidates = sorted(
            (d for d in shortlist if d not in queried), key=lambda d: shortlist[d]
        )[:alpha]
        if not candidates:
            break
        progressed = False
        for to_did in candidates:
            queried.add(to_did)
            for signed in query(to_did, target_id) or []:
                rec = verify_record(signed, allowlist=allowlist, now=now)
                if rec is None:
                    continue  # unauthorized / forged / tampered -> dropped
                records[rec.did] = rec.signed
                if rec.did not in shortlist:
                    shortlist[rec.did] = node_id(rec.did) ^ target_id
                    progressed = True
        if not progressed:
            break

    ordered = sorted(shortlist, key=lambda d: shortlist[d])
    return ordered, records


def find_node(
    target_id: int,
    *,
    seed,
    query: QueryFn,
    allowlist=None,
    count: int = 20,
    alpha: int = 3,
    now: float | None = None,
) -> list[str]:
    """Iteratively find the ``count`` DIDs closest to ``target_id``, starting from
    ``seed`` and asking each peer (via ``query``) for its closest. Every returned
    record is allowlist-gated + signature-verified before it counts."""
    ordered, _ = _lookup(
        target_id, seed=seed, query=query, allowlist=allowlist, alpha=alpha, now=now
    )
    return ordered[:count]


def find_peer(
    target_did: str,
    *,
    seed,
    query: QueryFn,
    allowlist=None,
    alpha: int = 3,
    now: float | None = None,
) -> PeerRecord | None:
    """Locate a specific peer's current self-signed record, or None if it is not
    discovered. The record is re-verified before return, so a caller always gets an
    authentic, allowlisted record (or nothing)."""
    _, records = _lookup(
        node_id(target_did), seed=seed, query=query, allowlist=allowlist, alpha=alpha, now=now
    )
    signed = records.get(target_did)
    if signed is None:
        return None
    return verify_record(signed, allowlist=allowlist, now=now)


__all__ = [
    "ID_BITS",
    "QueryFn",
    "node_id",
    "xor_distance",
    "bucket_index",
    "RoutingTable",
    "find_node",
    "find_peer",
]
