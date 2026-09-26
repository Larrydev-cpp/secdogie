"""Membership + endpoint gossip (P2P.3): a decentralized directory of who is in
the mesh, kept convergent by anti-entropy so it survives the hub dying.

Each node *self-signs* a record of its own reachability -- ``{did, endpoints,
last_seen}`` signed by its DID -- so a peer's endpoints cannot be forged by
whoever relays them, and only allowlisted DIDs are accepted. Nodes gossip these
records pairwise using the same have/want anti-entropy that ``citadel/sync.py``
uses for the journal: exchange a digest of ``{did: last_seen}``, send the records
the other is missing or staler on, and merge last-writer-wins by ``last_seen``.
The hub is only bootstrap/relay; the membership itself is not owned by it, so if
the hub dies the nodes still know each other.

The self-signature is the security property that makes gossip safe: a relaying
peer can pass along another node's record but cannot alter its endpoints or
invent a peer, because it does not hold that DID's key.

A record may also carry two optional, equally self-signed lists (2C): ``roles``
-- the mesh services the node currently offers (``relay`` / ``rendezvous``), so
any allowlisted node can take one on and be found -- and ``relays`` -- the relay
DIDs through which the node can be reached right now (a circuit address, as in
libp2p). Records without them encode exactly as before. No new crypto (signing
reuses secdogie-identity), no traffic obfuscation, no detection-evasion -- an
authenticated, self-owned directory that converges. Pure and loopback-testable.
"""
from __future__ import annotations

from dataclasses import dataclass

from secdogie_identity import pubkey_from_did, sign_payload, verify_payload

from .endpoint import Endpoint, EndpointSet

RECORD_TYPE = "secdogie/membership/record/v1"

ROLE_RELAY = "relay"
ROLE_RENDEZVOUS = "rendezvous"
# The only roles a record can advertise; anything else is dropped on verify.
ROLES = frozenset({ROLE_RELAY, ROLE_RENDEZVOUS})
MAX_ADVERTISED_RELAYS = 4


def sign_record(identity, endpoints, *, last_seen: float, roles=(), relays=()) -> dict:
    """A node's self-signed reachability announcement. ``endpoints`` is an
    ``EndpointSet`` or an iterable of ``Endpoint``; ``roles`` are the mesh
    services it offers (a subset of ``ROLES``); ``relays`` are the relay DIDs it
    holds leases with. Only the node itself can produce this (it holds the DID's
    key), so a relayed record is tamper-proof."""
    es = endpoints if isinstance(endpoints, EndpointSet) else EndpointSet(endpoints)
    payload = {
        "type": RECORD_TYPE,
        "did": identity.did,
        "endpoints": [{"kind": e.kind, "host": e.host, "port": e.port} for e in es.all()],
        "last_seen": float(last_seen),
    }
    unknown = sorted(set(roles) - ROLES)
    if unknown:
        raise ValueError(f"unknown role(s): {', '.join(unknown)} (expected a subset of {sorted(ROLES)})")
    if roles:
        payload["roles"] = sorted(set(roles))
    relays = list(dict.fromkeys(relays))
    if len(relays) > MAX_ADVERTISED_RELAYS:
        raise ValueError(f"at most {MAX_ADVERTISED_RELAYS} relays can be advertised")
    for relay in relays:
        pubkey_from_did(relay)  # a well-formed did:key
        if relay == identity.did:
            raise ValueError("a node cannot advertise itself as its own relay")
    if relays:
        payload["relays"] = relays
    return sign_payload(identity, payload)


@dataclass(frozen=True)
class PeerRecord:
    """A verified membership record. ``signed`` is the original signed object, so
    the view can re-gossip it with its signature intact (transitive trust)."""

    did: str
    last_seen: float
    endpoints: EndpointSet
    signed: dict
    roles: tuple[str, ...] = ()
    relays: tuple[str, ...] = ()


def verify_record(obj, *, allowlist=None, now: float | None = None, max_future_skew: float = 300.0):
    """Verify a signed membership record, or None if it is not one / is
    unauthentic. Checks the domain type, the signature (``signer == did``), the
    allowlist, and -- when ``now`` is given -- rejects a ``last_seen`` claiming to
    be more than ``max_future_skew`` seconds in the future (an anti-rollforward
    guard so a peer can't pin itself as forever-newest)."""
    if not isinstance(obj, dict) or obj.get("type") != RECORD_TYPE:
        return None
    ok, signer = verify_payload(obj, allowlist)
    if not ok or obj.get("did") != signer:
        return None
    try:
        last_seen = float(obj["last_seen"])
    except (KeyError, TypeError, ValueError):
        return None
    if now is not None and last_seen > now + max_future_skew:
        return None
    es = EndpointSet()
    for item in obj.get("endpoints") or []:
        if not isinstance(item, dict):
            continue
        try:
            es.add(Endpoint(str(item["kind"]), str(item["host"]), int(item["port"])))
        except (KeyError, ValueError, TypeError):
            continue
    roles = tuple(sorted({r for r in _strings(obj.get("roles")) if r in ROLES}))
    relays = tuple(dict.fromkeys(r for r in _strings(obj.get("relays")) if r != signer))
    return PeerRecord(did=signer, last_seen=last_seen, endpoints=es, signed=obj,
                      roles=roles, relays=relays[:MAX_ADVERTISED_RELAYS])


def _strings(value) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


class MembershipView:
    """A node's convergent view of the mesh: DID -> latest signed PeerRecord.
    Last-writer-wins by ``last_seen``; only allowlisted, self-signed records are
    admitted."""

    def __init__(self, *, allowlist=None):
        self._allowlist = allowlist
        self._records: dict[str, PeerRecord] = {}

    def merge_record(self, obj, *, now: float | None = None) -> bool:
        """Verify and merge one signed record. Newer ``last_seen`` wins; a tie or
        older record is ignored. Returns whether the view changed."""
        rec = verify_record(obj, allowlist=self._allowlist, now=now)
        if rec is None:
            return False
        cur = self._records.get(rec.did)
        if cur is not None and rec.last_seen <= cur.last_seen:
            return False
        self._records[rec.did] = rec
        return True

    def get(self, did: str) -> PeerRecord | None:
        return self._records.get(did)

    def endpoints_for(self, did: str) -> EndpointSet | None:
        rec = self._records.get(did)
        return rec.endpoints if rec is not None else None

    def known(self) -> list[str]:
        return sorted(self._records)

    def providers(self, role: str) -> list[str]:
        """DIDs whose latest record advertises ``role``, freshest first."""
        recs = [r for r in self._records.values() if role in r.roles]
        return [r.did for r in sorted(recs, key=lambda r: (-r.last_seen, r.did))]

    def digest(self) -> dict[str, float]:
        """The have-summary sent to a peer: what I know and how fresh."""
        return {did: rec.last_seen for did, rec in self._records.items()}

    def records_for(self, remote_digest: dict) -> list[dict]:
        """The signed records this view holds that the remote lacks or is staler
        on (the want-response)."""
        out: list[dict] = []
        for did, rec in self._records.items():
            theirs = remote_digest.get(did)
            if theirs is None or rec.last_seen > float(theirs):
                out.append(rec.signed)
        return out

    def apply_records(self, records, *, now: float | None = None) -> int:
        return sum(1 for r in records if self.merge_record(r, now=now))


def announce(identity, endpoints, *, last_seen: float, view: MembershipView, now: float | None = None,
             roles=(), relays=()) -> dict:
    """Sign this node's own record and merge it into ``view``. Returns the signed
    record (to hand to the transport for gossip)."""
    signed = sign_record(identity, endpoints, last_seen=last_seen, roles=roles, relays=relays)
    view.merge_record(signed, now=now)
    return signed


def gossip_round(a: MembershipView, b: MembershipView, *, now: float | None = None) -> tuple[int, int]:
    """One anti-entropy exchange between two views (mirrors ``sync.sync_round``).
    Each sends the other the records it is missing or staler on; both merge.
    Returns ``(merged_into_a, merged_into_b)``. Idempotent: a repeated round with
    no new records merges nothing."""
    da, db = a.digest(), b.digest()
    to_a = b.records_for(da)
    to_b = a.records_for(db)
    return a.apply_records(to_a, now=now), b.apply_records(to_b, now=now)


__all__ = [
    "RECORD_TYPE",
    "ROLE_RELAY",
    "ROLE_RENDEZVOUS",
    "ROLES",
    "MAX_ADVERTISED_RELAYS",
    "PeerRecord",
    "MembershipView",
    "sign_record",
    "verify_record",
    "announce",
    "gossip_round",
]
