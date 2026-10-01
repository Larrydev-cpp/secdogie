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
    traffic stays delegated to the C `tunnel/` (or the sealed v2 frames).
  * **No new crypto.** Signing/verification reuse secdogie-identity exactly as
    the direct UDP transport does; the reflexive address is standard connectivity
    discovery, not evasion.

Freshness (T3): once these frames cross a real network, a captured one could be
replayed. So every request carries a ``ts`` that the server accepts only within
``max_skew`` of its own clock and only if it is newer than the last one it
accepted from that DID for that request kind -- a replayed REGISTER cannot move a
peer's reflexive endpoint to the replayer's address. Every reply echoes the
request's ``ts``, and a client accepts only the reply to a request it still has
outstanding, so an old lookup result cannot point it at a stale endpoint. A
registration not renewed within ``ttl`` is no longer handed out.

`RendezvousServer` / `RendezvousClient` are the pure protocol (in-memory, frame
by frame). `RendezvousService` serves it on a node's `DirectUDPTransport` (the
rendezvous role, as `RelayService` is the relay role), and `RendezvousLink` is a
node's side over its own transport: periodic registration, and a blocking
``lookup(did)`` across the rendezvous it was given. Frames carry ``t`` (the
same value as ``type``) so the transport's frame dispatch can route them.
"""
from __future__ import annotations

import json
import math
import threading
import time
from collections import Counter
from dataclasses import dataclass

from secdogie_identity import Allowlist, Identity, require_trust, sign_payload, verify_payload

from .endpoint import Endpoint, EndpointSet

REGISTER = "secdogie/rendezvous/register/v1"
REGISTER_ACK = "secdogie/rendezvous/register-ack/v1"
LOOKUP = "secdogie/rendezvous/lookup/v1"
LOOKUP_RESULT = "secdogie/rendezvous/lookup-result/v1"

DEFAULT_MAX_SKEW = 120.0     # server: accepted |request ts - server clock|
DEFAULT_TTL = 90.0           # server: a registration not renewed this long is not handed out
DEFAULT_RATE = 20.0          # server: requests/s per DID, sustained...
DEFAULT_BURST = 40.0         # ...with this much burst
REGISTER_EVERY = 20.0        # link: re-register interval (well inside DEFAULT_TTL)
LOOKUP_TIMEOUT = 3.0         # link: how long one rendezvous gets to answer a lookup
_MAX_PENDING_LOOKUPS = 64    # client: outstanding lookups remembered at once

# Endpoint kinds a peer may self-report at registration. The authoritative
# `observed` (reflexive) kind is never self-reported -- the rendezvous stamps it
# from the packet source, so a peer cannot forge where it "appears" to be.
_SELF_REPORTABLE = frozenset({"local", "candidate", "public"})


def _encode(identity: Identity, payload: dict) -> bytes:
    return json.dumps(sign_payload(identity, {**payload, "t": payload["type"]})).encode("utf-8")


def _decode(raw) -> dict | None:
    if isinstance(raw, dict):
        return raw  # already parsed by the transport's frame dispatch
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def _is_num(v) -> bool:
    # json.loads accepts NaN / Infinity, which would slip past every comparison.
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


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


@dataclass
class _Bucket:
    tokens: float
    at: float


class RendezvousServer:
    """A DID-authenticated directory of (DID -> endpoints). It signs its replies
    with its own identity so a client can pin it, and gates every request behind
    an allowlist -- it only ever indexes the operator's authorized peers.

    `stats` counts ``registered`` / ``looked_up`` and every drop by reason."""

    def __init__(self, identity: Identity, *, allowlist=None, clock=time.time,
                 max_skew: float = DEFAULT_MAX_SKEW, ttl: float = DEFAULT_TTL,
                 rate: float = DEFAULT_RATE, burst: float = DEFAULT_BURST):
        self.identity = identity
        self.did = identity.did
        self._allowlist = require_trust(allowlist, "RendezvousServer")
        self._clock = clock
        self._max_skew = float(max_skew)
        self.ttl = float(ttl)
        self._rate = float(rate)
        self._burst = float(burst)
        self._registry: dict[str, _Registration] = {}
        self._last_ts: dict[tuple[str, str], float] = {}  # (request type, DID) -> newest ts accepted
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()
        self.stats: Counter = Counter()

    def known(self, did: str) -> EndpointSet | None:
        """The endpoints `did` registered, while that registration is live."""
        with self._lock:
            reg = self._registry.get(did)
            return reg.endpoints if reg is not None and self._live(reg) else None

    def _live(self, reg: _Registration) -> bool:
        return self._clock() - reg.last_seen <= self.ttl

    def _admit(self, obj: dict | None, kind: str) -> str | None:
        """The signer of a fresh, authorized `kind` request, else None (and the
        drop counted). Call with the lock held."""
        if obj is None or obj.get("type") != kind:
            return self._drop("malformed")
        ok, signer = verify_payload(obj, self._allowlist)
        if not ok or obj.get("did") != signer:
            return self._drop("unauthenticated")  # bad signature / not authorized / did != signer
        now = self._clock()
        if not self._take(signer, now):
            return self._drop("rate_limited")
        ts = obj.get("ts")
        if not _is_num(ts) or abs(now - ts) > self._max_skew:
            return self._drop("stale")
        if ts <= self._last_ts.get((kind, signer), float("-inf")):
            return self._drop("replayed")
        self._last_ts[(kind, signer)] = float(ts)
        return signer

    def on_register(self, raw, src_addr: tuple[str, int]) -> bytes | None:
        """Verify a register frame (bytes, or the parsed dict), record the peer's
        self-reported endpoints plus the reflexive (observed) address the packet
        came from, and return a signed ack carrying that reflexive endpoint and
        echoing the request's ``ts``. None (dropped) if unauthenticated, stale or
        replayed."""
        obj = _decode(raw)
        with self._lock:
            signer = self._admit(obj, REGISTER)
            if signer is None:
                return None
            endpoints = _endpoints_from_json(obj.get("endpoints"), allowed_kinds=_SELF_REPORTABLE)
            reflexive = endpoints.observe(src_addr[0], int(src_addr[1]))  # authoritative, from the packet
            self._registry[signer] = _Registration(endpoints=endpoints, last_seen=self._clock())
        self.stats["registered"] += 1
        ack = {
            "type": REGISTER_ACK,
            "to": signer,
            "reflexive": {"kind": reflexive.kind, "host": reflexive.host, "port": reflexive.port},
            "echo": obj["ts"],
            "ttl": self.ttl,
            "ts": self._clock(),
        }
        return _encode(self.identity, ack)

    def on_lookup(self, raw) -> bytes | None:
        """Verify a lookup frame and return a signed result with the target's
        live endpoints (empty when the target is unauthorized, unregistered or
        expired), echoing the request's ``ts``. Both querier and target must be
        authorized."""
        obj = _decode(raw)
        with self._lock:
            signer = self._admit(obj, LOOKUP)
            if signer is None:
                return None
            target = obj.get("target_did")
            endpoints: list[dict] = []
            if isinstance(target, str) and self._allowlist.contains(target):
                reg = self._registry.get(target)
                if reg is not None and self._live(reg):
                    endpoints = _endpoints_to_json(reg.endpoints)
        self.stats["looked_up"] += 1
        result = {
            "type": LOOKUP_RESULT,
            "to": signer,
            "target_did": target if isinstance(target, str) else "",
            "endpoints": endpoints,
            "echo": obj["ts"],
            "ts": self._clock(),
        }
        return _encode(self.identity, result)

    def _take(self, did: str, now: float) -> bool:
        bucket = self._buckets.setdefault(did, _Bucket(self._burst, now))
        bucket.tokens = min(self._burst, bucket.tokens + (now - bucket.at) * self._rate)
        bucket.at = now
        if bucket.tokens < 1:
            return False
        bucket.tokens -= 1
        return True

    def _drop(self, reason: str) -> None:
        self.stats[f"dropped_{reason}"] += 1
        return None


class RendezvousClient:
    """A node's side of rendezvous. It builds signed register/lookup frames and
    parses the rendezvous's signed replies, learning its own reflexive endpoint
    and a peer's endpoints. `server_did` pins the rendezvous so a reply is trusted
    only when signed by that exact identity, addressed to this node, and echoing
    a request this client still has outstanding."""

    def __init__(self, identity: Identity, server_did: str, *, clock=time.time):
        self.identity = identity
        self.server_did = server_did
        self._clock = clock
        self.self_endpoints = EndpointSet()  # own candidates + learned reflexive
        self._last_ts = float("-inf")
        self._pending_register: float | None = None
        self._pending_lookups: dict[float, str] = {}  # request ts -> target DID
        self._lock = threading.Lock()

    def _next_ts(self) -> float:
        # Strictly increasing, even when the clock does not move between two
        # requests: the server drops a request whose ts is not newer.
        ts = max(float(self._clock()), self._last_ts + 1e-6)
        self._last_ts = ts
        return ts

    def register_frame(self, local_endpoints) -> bytes:
        es = local_endpoints if isinstance(local_endpoints, EndpointSet) else EndpointSet(local_endpoints)
        with self._lock:
            for e in es.all():
                self.self_endpoints.add(e)
            ts = self._next_ts()
            self._pending_register = ts  # only the ack to the latest register counts
        payload = {
            "type": REGISTER,
            "did": self.identity.did,
            "endpoints": _endpoints_to_json(es),
            "ts": ts,
        }
        return _encode(self.identity, payload)

    def _verify_from_server(self, raw, expected_type: str) -> dict | None:
        obj = _decode(raw)
        if obj is None or obj.get("type") != expected_type:
            return None
        ok, signer = verify_payload(obj, Allowlist({self.server_did}))  # only the pinned server...
        if not ok or signer != self.server_did or obj.get("to") != self.identity.did:
            return None  # ...and the reply must be to us
        return obj

    def handle_register_ack(self, raw) -> Endpoint | None:
        """Verify the ack to this client's latest register and adopt the
        reflexive endpoint it reports as this node's own observed (public)
        endpoint. Returns it, or None if invalid, stale or unsolicited."""
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
        with self._lock:
            if self._pending_register is None or obj.get("echo") != self._pending_register:
                return None
            self._pending_register = None
            self.self_endpoints.add(reflexive)
        return reflexive

    def lookup_frame(self, target_did: str) -> bytes:
        with self._lock:
            ts = self._next_ts()
            self._pending_lookups[ts] = target_did
            while len(self._pending_lookups) > _MAX_PENDING_LOOKUPS:
                self._pending_lookups.pop(next(iter(self._pending_lookups)))
        payload = {
            "type": LOOKUP,
            "did": self.identity.did,
            "target_did": target_did,
            "ts": ts,
        }
        return _encode(self.identity, payload)

    def handle_lookup_result(self, raw) -> tuple[str, EndpointSet] | None:
        """Verify the result of an outstanding lookup and return (target_did,
        endpoints). The endpoint set is empty when the target is unknown,
        unauthorized or expired. None if invalid, stale or unsolicited."""
        obj = self._verify_from_server(raw, LOOKUP_RESULT)
        if obj is None:
            return None
        target = str(obj.get("target_did") or "")
        echo = obj.get("echo")
        with self._lock:
            if not _is_num(echo) or self._pending_lookups.get(echo) != target:
                return None
            del self._pending_lookups[echo]
        return target, _endpoints_from_json(obj.get("endpoints"))


class RendezvousService:
    """The rendezvous role on a node's `DirectUDPTransport`. Constructing it
    starts serving (the operator's opt-in); `stop()` withdraws it. Advertise it
    with ``sign_record(..., roles=[ROLE_RENDEZVOUS])`` so other nodes can find
    it. Replies go straight back to the packet's source address."""

    def __init__(self, transport, *, allowlist, **server_kw):
        self.transport = transport
        self.server = RendezvousServer(transport.identity, allowlist=allowlist, **server_kw)
        transport.on_frame(REGISTER, self._on_register)
        transport.on_frame(LOOKUP, self._on_lookup)

    @property
    def stats(self) -> Counter:
        return self.server.stats

    def stop(self) -> None:
        self.transport.on_frame(REGISTER, None)
        self.transport.on_frame(LOOKUP, None)

    def _on_register(self, obj: dict, addr: tuple) -> None:
        reply = self.server.on_register(obj, (addr[0], int(addr[1])))
        if reply is not None:
            self.transport.channel.send(addr[0], addr[1], reply)

    def _on_lookup(self, obj: dict, addr: tuple) -> None:
        reply = self.server.on_lookup(obj)
        if reply is not None:
            self.transport.channel.send(addr[0], addr[1], reply)


class RendezvousLink:
    """A node's side of one or more rendezvous, over its own `DirectUDPTransport`.

    ``servers`` maps each rendezvous DID to the address it is reached at, in
    order of preference. `register` announces this node's endpoints to all of
    them (`start` keeps doing so); `lookup` asks them in turn for a peer's
    endpoints and returns the first non-empty answer. Replies are matched to the
    rendezvous that signed them, so one link serves several rendezvous on one
    transport."""

    def __init__(self, transport, servers: dict[str, tuple[str, int]], *, clock=time.time):
        if not servers:
            raise ValueError("a rendezvous link needs at least one rendezvous")
        self.transport = transport
        self._servers = {did: (str(addr[0]), int(addr[1])) for did, addr in servers.items()}
        self._clients = {did: RendezvousClient(transport.identity, did, clock=clock) for did in self._servers}
        self._answers: dict[tuple[str, str], EndpointSet] = {}
        self._cond = threading.Condition()
        self._stop: threading.Event | None = None
        self.reflexive: dict[str, Endpoint] = {}  # rendezvous DID -> this node's address as it saw it
        transport.on_frame(REGISTER_ACK, self._on_ack)
        transport.on_frame(LOOKUP_RESULT, self._on_result)

    @classmethod
    def from_records(cls, transport, records, **kw) -> RendezvousLink:
        """Build the link from the rendezvous' own signed membership records (as
        ``secdogie-relay --rendezvous`` prints them). Handing a record over is
        the operator's decision to use that rendezvous; each must verify, carry
        the rendezvous role and name an endpoint."""
        from secdogie_identity import ALLOW_ANY

        from .membership import ROLE_RENDEZVOUS, verify_record

        servers: dict[str, tuple[str, int]] = {}
        for obj in records:
            rec = verify_record(obj, allowlist=ALLOW_ANY)  # self-signed; the operator chose it
            best = rec.endpoints.best() if rec is not None else None
            if rec is None or ROLE_RENDEZVOUS not in rec.roles or best is None:
                raise ValueError("a rendezvous record must be a valid, self-signed record "
                                 "with the rendezvous role and an endpoint")
            servers[rec.did] = (best.host, best.port)
        return cls(transport, servers, **kw)

    @property
    def servers(self) -> list[str]:
        return list(self._servers)

    def register(self, endpoints=()) -> None:
        """Announce this node's own endpoints (the rendezvous adds the address it
        sees the packet come from)."""
        for did, (host, port) in self._servers.items():
            self.transport.channel.send(host, port, self._clients[did].register_frame(endpoints))

    def lookup(self, did: str, timeout: float = LOOKUP_TIMEOUT) -> EndpointSet | None:
        """`did`'s endpoints from the first rendezvous that knows them, or None."""
        for server, (host, port) in self._servers.items():
            key = (server, did)
            with self._cond:
                self._answers.pop(key, None)
            self.transport.channel.send(host, port, self._clients[server].lookup_frame(did))
            with self._cond:
                self._cond.wait_for(lambda key=key: key in self._answers, timeout)
                found = self._answers.pop(key, None)
            if found is not None and found.best() is not None:
                return found
        return None

    def start(self, endpoints_fn=lambda: (), every: float = REGISTER_EVERY) -> threading.Event:
        """Register now and every `every` seconds on a daemon thread, announcing
        what `endpoints_fn()` returns, until `close()`."""
        stop = threading.Event()
        self._stop = stop

        def run() -> None:
            while True:
                try:
                    self.register(endpoints_fn())
                except Exception:  # noqa: BLE001 - a failed round retries on the next tick
                    pass
                if stop.wait(every):
                    return

        threading.Thread(target=run, daemon=True, name="rendezvous-register").start()
        return stop

    def close(self) -> None:
        if self._stop is not None:
            self._stop.set()
        self.transport.on_frame(REGISTER_ACK, None)
        self.transport.on_frame(LOOKUP_RESULT, None)

    def _client_for(self, obj: dict) -> RendezvousClient | None:
        signer = obj.get("signer")
        return self._clients.get(signer) if isinstance(signer, str) else None

    def _on_ack(self, obj: dict, addr: tuple) -> None:
        client = self._client_for(obj)
        reflexive = client.handle_register_ack(obj) if client is not None else None
        if reflexive is not None:
            self.reflexive[client.server_did] = reflexive

    def _on_result(self, obj: dict, addr: tuple) -> None:
        client = self._client_for(obj)
        result = client.handle_lookup_result(obj) if client is not None else None
        if result is not None:
            target, endpoints = result
            with self._cond:
                self._answers[(client.server_did, target)] = endpoints
                self._cond.notify_all()


__all__ = [
    "REGISTER",
    "REGISTER_ACK",
    "LOOKUP",
    "LOOKUP_RESULT",
    "RendezvousServer",
    "RendezvousClient",
    "RendezvousService",
    "RendezvousLink",
]
