"""Rendezvous + reflexive endpoint discovery (P2P.1): how authorized peers find
each other's current endpoints without a central authority over their data.

This is the STUN/AutoNAT + coordination-server function, modelled on Tailscale's
DERP and libp2p's Identify/AutoNAT: a node REGISTERS its DID and local candidate
endpoints at a rendezvous and learns its own *reflexive* (public) endpoint -- the
source address the rendezvous actually saw -- then LOOKS UP another authorized
peer's known endpoints. With both sides' candidate + reflexive endpoints in hand,
the direct-connection upgrade (P2P.2) can attempt a hole punch, falling back to
the hub relay.

Two boundaries keep this an honest, compliant advance:

  * **Authenticated directory, not a third-party host.** Every message is a
    DID-signed frame and every peer is allowlist-gated, so the rendezvous only
    ever indexes the operator's OWN authorized nodes. It is a dumb directory of
    (DID -> endpoints); it never sees or relays data-plane plaintext, and it adds
    no traffic obfuscation and no detection-evasion. Confidentiality of actual
    traffic stays delegated to the tunnel / WireGuard.
  * **No new crypto.** Signing/verification reuse secdogie-identity exactly as
    the direct UDP transport does; the reflexive address is standard connectivity
    discovery, not evasion.
  * **No replay.** Every frame's signed `ts` must be within the clock skew
    (freshness.py). A REGISTER must also be newer than the last one accepted for
    that DID, so a captured REGISTER replayed from another address cannot re-point
    the peer's reflexive endpoint at the replayer.

Pure protocol + an in-memory registry, so the whole thing runs headless; on the
wire it rides the same UDPChannel as DirectUDPTransport (loopback-testable).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

from secdogie_identity import Identity, sign_payload, verify_payload

from .endpoint import Endpoint, EndpointSet
from .freshness import DEFAULT_MAX_SKEW, is_fresh_seconds

REGISTER = "secdogie/rendezvous/register/v1"
REGISTER_ACK = "secdogie/rendezvous/register-ack/v1"
LOOKUP = "secdogie/rendezvous/lookup/v1"
LOOKUP_RESULT = "secdogie/rendezvous/lookup-result/v1"

# Endpoint kinds a peer may self-report at registration. The authoritative
# `observed` (reflexive) kind is never self-reported -- the rendezvous stamps it
# from the packet source, so a peer cannot forge where it "appears" to be.
_SELF_REPORTABLE = frozenset({"local", "candidate", "public"})


def _encode(identity: Identity, payload: dict) -> bytes:
    return json.dumps(sign_payload(identity, payload)).encode("utf-8")


def _decode(raw: bytes) -> dict | None:
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


def _endpoints_to_json(endpoints: EndpointSet) -> list[dict]:
    return [{"kind": e.kind, "host": e.host, "port": e.port} for e in endpoints.all()]


def _endpoints_from_json(items, *, allowed_kinds=None) -> EndpointSet:
    es = EndpointSet()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        if allowed_kinds is not None and kind not in allowed_kinds:
            continue
        try:
            es.add(Endpoint(str(kind), str(item["host"]), int(item["port"])))
        except (KeyError, ValueError, TypeError):
            continue  # a malformed endpoint is skipped, never fatal
    return es


@dataclass
class _Registration:
    endpoints: EndpointSet
    last_seen: float


class RendezvousServer:
    """A DID-authenticated directory of (DID -> endpoints). It signs its replies
    with its own identity so a client can pin it, and gates every request behind
    an allowlist -- it only ever indexes the operator's authorized peers."""

    def __init__(self, identity: Identity, *, allowlist=None, clock=time.time,
                 max_skew: float = DEFAULT_MAX_SKEW):
        self.identity = identity
        self.did = identity.did
        self._allowlist = allowlist
        self._clock = clock
        self.max_skew = max_skew
        self._registry: dict[str, _Registration] = {}
        self._last_register: dict[str, tuple[float, str]] = {}  # did -> (ts, sig) last accepted

    def known(self, did: str) -> EndpointSet | None:
        reg = self._registry.get(did)
        return reg.endpoints if reg is not None else None

    def on_register(self, raw: bytes, src_addr: tuple[str, int]) -> bytes | None:
        """Verify a register frame, record the peer's self-reported endpoints plus
        the reflexive (observed) address the packet came from, and return a signed
        ack carrying that reflexive endpoint. None (dropped) if unauthenticated."""
        obj = _decode(raw)
        if obj is None or obj.get("type") != REGISTER:
            return None
        ok, signer = verify_payload(obj, self._allowlist)
        if not ok or obj.get("did") != signer:
            return None  # bad signature / not authorized / did != signer
        ts = obj.get("ts")
        if not is_fresh_seconds(ts, now=self._clock(), max_skew=self.max_skew):
            return None  # stale or future-dated
        last = self._last_register.get(signer)
        if last is not None and (ts < last[0] or (ts == last[0] and obj.get("sig") == last[1])):
            return None  # older than, or an exact replay of, the last accepted REGISTER
        self._last_register[signer] = (ts, obj.get("sig"))

        endpoints = _endpoints_from_json(obj.get("endpoints"), allowed_kinds=_SELF_REPORTABLE)
        reflexive = endpoints.observe(src_addr[0], int(src_addr[1]))  # authoritative, from the packet
        self._registry[signer] = _Registration(endpoints=endpoints, last_seen=self._clock())

        ack = {
            "type": REGISTER_ACK,
            "to": signer,
            "reflexive": {"kind": reflexive.kind, "host": reflexive.host, "port": reflexive.port},
            "ts": self._clock(),
        }
        return _encode(self.identity, ack)

    def on_lookup(self, raw: bytes) -> bytes | None:
        """Verify a lookup frame and return a signed result with the target's
        known endpoints (empty when the target is unauthorized or unregistered).
        Both querier and target must be authorized."""
        obj = _decode(raw)
        if obj is None or obj.get("type") != LOOKUP:
            return None
        ok, signer = verify_payload(obj, self._allowlist)
        if not ok or obj.get("did") != signer:
            return None
        if not is_fresh_seconds(obj.get("ts"), now=self._clock(), max_skew=self.max_skew):
            return None
        target = obj.get("target_did")
        endpoints: list[dict] = []
        if isinstance(target, str) and (self._allowlist is None or self._allowlist.contains(target)):
            reg = self._registry.get(target)
            if reg is not None:
                endpoints = _endpoints_to_json(reg.endpoints)
        result = {
            "type": LOOKUP_RESULT,
            "to": signer,
            "target_did": target if isinstance(target, str) else "",
            "endpoints": endpoints,
            "ts": self._clock(),
        }
        return _encode(self.identity, result)


class RendezvousClient:
    """A node's side of rendezvous. It builds signed register/lookup frames and
    parses the rendezvous's signed replies, learning its own reflexive endpoint
    and a peer's endpoints. `server_did` pins the rendezvous so a reply is trusted
    only when signed by that exact identity."""

    def __init__(self, identity: Identity, server_did: str, *, clock=time.time,
                 max_skew: float = DEFAULT_MAX_SKEW):
        self.identity = identity
        self.server_did = server_did
        self._clock = clock
        self.max_skew = max_skew
        self.self_endpoints = EndpointSet()  # own candidates + learned reflexive

    def register_frame(self, local_endpoints) -> bytes:
        es = local_endpoints if isinstance(local_endpoints, EndpointSet) else EndpointSet(local_endpoints)
        for e in es.all():
            self.self_endpoints.add(e)
        payload = {
            "type": REGISTER,
            "did": self.identity.did,
            "endpoints": _endpoints_to_json(es),
            "nonce": self._clock(),
            "ts": self._clock(),
        }
        return _encode(self.identity, payload)

    def _verify_from_server(self, raw: bytes, expected_type: str) -> dict | None:
        obj = _decode(raw)
        if obj is None or obj.get("type") != expected_type:
            return None
        ok, signer = verify_payload(obj)  # signature validity...
        if not ok or signer != self.server_did or obj.get("to") != self.identity.did:
            return None  # ...and the reply must be from the pinned rendezvous, to us
        if not is_fresh_seconds(obj.get("ts"), now=self._clock(), max_skew=self.max_skew):
            return None  # a stale (replayed) reply
        return obj

    def handle_register_ack(self, raw: bytes) -> Endpoint | None:
        """Verify the ack and adopt the reflexive endpoint it reports as this
        node's own observed (public) endpoint. Returns it, or None if invalid."""
        obj = self._verify_from_server(raw, REGISTER_ACK)
        if obj is None:
            return None
        r = obj.get("reflexive")
        if not isinstance(r, dict):
            return None
        try:
            reflexive = Endpoint("observed", str(r["host"]), int(r["port"]))
        except (KeyError, ValueError, TypeError):
            return None
        self.self_endpoints.add(reflexive)
        return reflexive

    def lookup_frame(self, target_did: str) -> bytes:
        payload = {
            "type": LOOKUP,
            "did": self.identity.did,
            "target_did": target_did,
            "nonce": self._clock(),
            "ts": self._clock(),
        }
        return _encode(self.identity, payload)

    def handle_lookup_result(self, raw: bytes) -> tuple[str, EndpointSet] | None:
        """Verify a lookup result and return (target_did, endpoints). The endpoint
        set is empty when the target is unknown/unauthorized."""
        obj = self._verify_from_server(raw, LOOKUP_RESULT)
        if obj is None:
            return None
        target = str(obj.get("target_did") or "")
        return target, _endpoints_from_json(obj.get("endpoints"))


__all__ = [
    "REGISTER",
    "REGISTER_ACK",
    "LOOKUP",
    "LOOKUP_RESULT",
    "RendezvousServer",
    "RendezvousClient",
]
