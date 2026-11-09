"""The address book: how an open mesh grows without seed nodes.

There are no seed nodes. A new node needs exactly one first contact, and any
member can be it:

  * a neighbour on the same network (LAN discovery);
  * an invite from anyone already in the mesh (one signed record);
  * an existing public DHT used as a meeting place.

Once in, every node keeps the signed records of the peers it has met and
trades them with its neighbours (peer exchange), so the mesh spreads itself
and survives any node leaving. After a restart, the saved book is the way back
in.

Everything here is a *hint* until it verifies. Each entry is a peer's own
self-signed membership record (``membership.py``): a node passing it along
cannot forge or alter it. Each entry sits in one of two tiers:

  * ``own``: DIDs on your allowlist (your nodes);
  * ``public``: any other DID.

Public entries meet three admission rules, so the book cannot be flooded with
cheap identities (a Sybil attack):

  * **An identity costs work.** A public DID needs a puzzle nonce such that
    ``sha256("secdogie/id-puzzle/v1|" + did + "|" + nonce)`` starts with
    ``difficulty`` zero bits. Minting one identity is cheap; minting
    thousands is not. The nonce is bound to the DID, so it cannot be reused
    for another.
  * **Address diversity.** At most ``per_prefix`` public peers per IPv4 /24 or
    IPv6 /48, so one network cannot fill the book.
  * **Room and behaviour.** At most ``capacity`` public peers. A peer that
    keeps failing is dropped first; then the stalest goes.

Your own nodes are never handed to public peers. An exchange with a public
peer offers only public entries, so the mesh never learns where your own
machines are from your book.

Pure and deterministic: no sockets, no clock unless given one. ``save`` and
``load`` keep the book as JSON; loading verifies every record again.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import random
from dataclasses import dataclass, field
from pathlib import Path

from .membership import PeerRecord, verify_record

PUZZLE_DOMAIN = "secdogie/id-puzzle/v1"
DEFAULT_DIFFICULTY = 20  # about a million hashes: a second or so per identity
OWN, PUBLIC = "own", "public"


def _puzzle_hash(did: str, nonce: int) -> int:
    digest = hashlib.sha256(f"{PUZZLE_DOMAIN}|{did}|{int(nonce)}".encode()).digest()
    return int.from_bytes(digest, "big")


def puzzle_ok(did: str, nonce, difficulty: int) -> bool:
    """Whether ``nonce`` solves the identity puzzle for ``did``."""
    if not isinstance(nonce, int) or isinstance(nonce, bool) or nonce < 0:
        return False
    return difficulty <= 0 or _puzzle_hash(did, nonce) >> (256 - difficulty) == 0


def solve_puzzle(did: str, difficulty: int, *, start: int = 0) -> int:
    """The first nonce from ``start`` that solves the identity puzzle for ``did``."""
    nonce = start
    while not puzzle_ok(did, nonce, difficulty):
        nonce += 1
    return nonce


def prefix_of(host: str) -> str:
    """The network a host belongs to for diversity: its /24 (IPv4) or /48 (IPv6).
    A name that is not an address is its own prefix."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return f"name:{host.lower()}"
    bits = 24 if ip.version == 4 else 48
    return str(ipaddress.ip_network(f"{ip}/{bits}", strict=False))


@dataclass
class Entry:
    record: PeerRecord
    tier: str
    nonce: int = 0
    failures: int = 0
    successes: int = 0
    prefixes: frozenset = field(default_factory=frozenset)

    def wire(self) -> dict:
        return {"record": self.record.signed, "nonce": self.nonce}


class PeerBook:
    def __init__(self, own_did: str, *, own=frozenset(), difficulty: int = DEFAULT_DIFFICULTY,
                 capacity: int = 512, per_prefix: int = 2, max_failures: int = 5):
        self.own_did = own_did
        self.own = frozenset(own)  # DIDs on your allowlist
        self.difficulty = int(difficulty)
        self.capacity = int(capacity)
        self.per_prefix = int(per_prefix)
        self.max_failures = int(max_failures)
        self._entries: dict[str, Entry] = {}

    # -- reading ----------------------------------------------------------------------

    def get(self, did: str) -> Entry | None:
        return self._entries.get(did)

    def dids(self, tier: str | None = None) -> list[str]:
        return sorted(d for d, e in self._entries.items() if tier is None or e.tier == tier)

    def __len__(self) -> int:
        return len(self._entries)

    # -- admitting ----------------------------------------------------------------------

    def admit(self, obj, *, nonce: int = 0, now: float | None = None) -> str | None:
        """Take one hint (a signed record, and for a public DID its puzzle
        nonce). Returns the tier it was admitted to, or None with nothing
        changed: unverifiable, our own record, an unsolved puzzle, an older
        record than the one held, or no room under the admission rules."""
        rec = verify_record(obj, allowlist=None, now=now)
        if rec is None or rec.did == self.own_did:
            return None
        tier = OWN if rec.did in self.own else PUBLIC
        if tier == PUBLIC and not puzzle_ok(rec.did, nonce, self.difficulty):
            return None
        held = self._entries.get(rec.did)
        if held is not None and held.record.last_seen >= rec.last_seen:
            return None
        prefixes = frozenset(prefix_of(e.host) for e in rec.endpoints.all())
        if tier == PUBLIC:
            if not prefixes:
                return None  # a public peer we could never reach
            if not self._room_for(rec.did, prefixes, rec.last_seen):
                return None
        if held is not None:
            held.record, held.nonce, held.prefixes = rec, nonce, prefixes
        else:
            self._entries[rec.did] = Entry(rec, tier, nonce, prefixes=prefixes)
        return tier

    def _room_for(self, did: str, prefixes: frozenset, last_seen: float) -> bool:
        others = [e for d, e in self._entries.items() if e.tier == PUBLIC and d != did]
        for p in prefixes:
            if sum(1 for e in others if p in e.prefixes) >= self.per_prefix:
                return False  # this network already has its share
        if len(others) < self.capacity:
            return True
        # Full: the worst goes (failing first, then the stalest), and only for a fresher newcomer.
        worst = max(others, key=lambda e: (e.failures, -e.record.last_seen, e.record.did))
        if worst.failures == 0 and worst.record.last_seen >= last_seen:
            return False
        del self._entries[worst.record.did]
        return True

    # -- behaviour --------------------------------------------------------------------

    def succeeded(self, did: str) -> None:
        e = self._entries.get(did)
        if e is not None:
            e.successes += 1
            e.failures = 0

    def failed(self, did: str) -> None:
        """A peer did not answer or misbehaved; a public peer that keeps failing is dropped."""
        e = self._entries.get(did)
        if e is None:
            return
        e.failures += 1
        if e.tier == PUBLIC and e.failures >= self.max_failures:
            del self._entries[did]

    # -- peer exchange ------------------------------------------------------------------

    def offer(self, to_tier: str, *, limit: int = 32, rng: random.Random | None = None) -> list[dict]:
        """Entries to hand a neighbour. A public neighbour gets public entries
        only; your own nodes are never revealed to it."""
        pool = [e for e in self._entries.values() if e.failures == 0 and (to_tier == OWN or e.tier == PUBLIC)]
        pool.sort(key=lambda e: (-e.record.last_seen, e.record.did))
        if rng is not None and len(pool) > limit:
            pool = rng.sample(pool, limit)
        return [e.wire() for e in pool[:limit]]

    def merge(self, offered, *, now: float | None = None) -> int:
        """Take a neighbour's offer; returns how many entries were admitted."""
        if not isinstance(offered, list):
            return 0
        n = 0
        for item in offered[:256]:
            if isinstance(item, dict) and self.admit(item.get("record"), nonce=item.get("nonce", 0), now=now):
                n += 1
        return n

    # -- keeping it --------------------------------------------------------------------

    def save(self, path: Path) -> None:
        data = [{**e.wire(), "failures": e.failures, "successes": e.successes} for e in self._entries.values()]
        tmp = Path(path).with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)

    def load(self, path: Path, *, now: float | None = None) -> int:
        """Read a saved book back, verifying every record again."""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        n = 0
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict):
                continue
            if self.admit(item.get("record"), nonce=item.get("nonce", 0), now=now):
                e = self._entries.get((item.get("record") or {}).get("did"))
                if e is not None:
                    e.failures = int(item.get("failures", 0) or 0)
                    e.successes = int(item.get("successes", 0) or 0)
                n += 1
        return n


__all__ = ["DEFAULT_DIFFICULTY", "OWN", "PUBLIC", "PUZZLE_DOMAIN", "Entry", "PeerBook", "prefix_of", "puzzle_ok",
           "solve_puzzle"]
