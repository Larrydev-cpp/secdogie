"""The relay role, hostable by any allowlisted node (2C).

`DirectUpgrader` already routes "direct when proven, else relay", but the relay
was whatever `Transport` the caller passed -- in practice the in-memory
`HubTransport`, a special node. 2C turns relaying into a *role*: any allowlisted
node (a home NAS, a VPS, a desktop) can take it on or drop it at runtime, and
the client side picks among the nodes that currently offer it, so losing a relay
degrades to another one instead of partitioning the mesh.

Discovery rides membership (P2P.3). A relay's self-signed record carries
``roles=["relay"]``; a node that holds relay leases advertises
``relays=[...]`` -- "reach me through these", the circuit-address idea from
libp2p. Both lists are covered by the record's own signature, so a gossiping
peer cannot add, remove or redirect them.

Wire (every frame DID-signed JSON, on the node's existing UDP channel)::

    client -> relay   {t: register,     from, to: relay, ts}             lease request / renewal
    relay  -> client  {t: register-ack, from: relay, to, lease, echo}    echo = that request's ts
    sender -> relay   {t: send,         from, to: relay, dst, inner}
    relay  -> dst     {t: deliver,      from: relay, to: dst, src, inner}

``inner`` is the end-to-end frame `DirectUDPTransport` would have sent directly:
DID-signed by the original sender, and sealed to the destination's key when
encryption is on. The relay cannot read a sealed inner frame and cannot alter any
inner frame; the destination runs it through exactly the checks a direct datagram
gets (signature, allowlist, addressed to it, replay window), except that the
relay's address is never adopted as the sender's endpoint.

Zero-trust rules, all enforced here:

  * A relay needs an explicit allowlist (there is no "serve anyone" mode) and
    re-checks it on every forward, for both sender and destination, not only at
    registration: a DID taken off the allowlist -- or, with revocation, revoked
    -- stops being relayed at once.
  * A registration must be fresh: its ``ts`` within ``max_skew`` of the relay's
    clock and newer than the last one accepted for that DID, so a captured
    register replayed from another address cannot redirect a client.
  * A node accepts deliveries only from relays it asked for a lease, and only
    when the delivery's ``src`` is the inner frame's actual signer; it accepts an
    ack only when it echoes the node's latest request.
  * A per-DID token bucket, an inner-frame size cap, a bounded client table, and
    leases that lapse unless renewed. A relay delivers only to its registered
    clients and never to another relay, so there are no chains or loops.

No new crypto (signing is secdogie-identity's, sealing is sealed.py's), no
traffic obfuscation, no detection-evasion: the relay moves opaque frames between
the operator's own allowlisted nodes, as Tailscale's DERP and libp2p's circuit
relay do. It never touches the physical-action path, so it suits a headless node.
"""
from __future__ import annotations

import base64
import json
import math
import threading
import time
from collections import Counter
from dataclasses import dataclass

from secdogie_identity import sign_payload, verify_payload

from .membership import ROLE_RELAY, MembershipView
from .session import Session
from .transport import DeliverFn, Transport
from .udp import DirectUDPTransport

RELAY_REGISTER = "secdogie/relay/register/v1"
RELAY_ACK = "secdogie/relay/register-ack/v1"
RELAY_SEND = "secdogie/relay/send/v1"
RELAY_DELIVER = "secdogie/relay/deliver/v1"

DEFAULT_LEASE = 60.0        # relay: how long a registration lasts unless renewed
MAX_LEASE = 600.0           # client: never trust a longer lease than this
DEFAULT_MAX_CLIENTS = 64    # relay: registrations held at once
DEFAULT_RATE = 50.0         # relay: frames/s per DID, sustained...
DEFAULT_BURST = 100.0       # ...with this much burst
DEFAULT_MAX_SKEW = 120.0    # relay: accepted |register ts - relay clock|
MAX_INNER = 60_000          # base64 chars of an inner frame: a deliver frame stays one UDP datagram
RENEW_EVERY = 10.0          # client: lease heartbeat, so a dead relay is noticed within ~15 s
ACK_TIMEOUT = 5.0           # client: a register unanswered this long => the relay is gone
DOWN_FOR = 30.0             # client: how long a gone relay is skipped
DEFAULT_MAX_RELAYS = 2      # client: leases held at once, for redundancy


def _sign(identity, payload: dict) -> bytes:
    return json.dumps(sign_payload(identity, payload)).encode("utf-8")


def _authentic(obj: dict, allowlist, self_did: str) -> str | None:
    """The signer of an envelope addressed to us by an authorized DID, else None."""
    ok, signer = verify_payload(obj, allowlist)
    if not ok or obj.get("from") != signer or obj.get("to") != self_did:
        return None
    return signer


def _is_num(v) -> bool:
    # json.loads accepts NaN / Infinity, which would slip past every comparison.
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


@dataclass
class _Registration:
    addr: tuple[str, int]
    expires_at: float


@dataclass
class _Bucket:
    tokens: float
    at: float


class RelayService:
    """The relay role. Constructing it on a node's `DirectUDPTransport` starts
    serving (the operator's opt-in); `stop()` withdraws it. Advertise it with
    ``announce(..., roles=[ROLE_RELAY])`` so other nodes can find it.

    `stats` counts ``registered`` / ``forwarded`` and every drop by reason."""

    def __init__(self, transport: DirectUDPTransport, *, allowlist, lease: float = DEFAULT_LEASE,
                 max_clients: int = DEFAULT_MAX_CLIENTS, rate: float = DEFAULT_RATE,
                 burst: float = DEFAULT_BURST, max_skew: float = DEFAULT_MAX_SKEW, clock=time.time):
        if allowlist is None:
            raise ValueError("a relay needs an explicit allowlist: it never serves unknown DIDs")
        self.transport = transport
        self.identity = transport.identity
        self.did = transport.identity.did
        self.lease = float(lease)
        self.max_clients = int(max_clients)
        self._allowlist = allowlist
        self._rate = float(rate)
        self._burst = float(burst)
        self._max_skew = float(max_skew)
        self._clock = clock
        self._clients: dict[str, _Registration] = {}
        self._last_ts: dict[str, float] = {}    # DID -> newest register ts accepted (kept past expiry)
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()
        self.stats: Counter = Counter()
        transport.on_frame(RELAY_REGISTER, self._on_register)
        transport.on_frame(RELAY_SEND, self._on_send)

    def stop(self) -> None:
        """Stop serving: frames for the role are ignored from now on and every
        lease is forgotten. Clients notice on their next heartbeat and move on."""
        self.transport.on_frame(RELAY_REGISTER, None)
        self.transport.on_frame(RELAY_SEND, None)
        with self._lock:
            self._clients.clear()

    def clients(self) -> list[str]:
        """DIDs holding a live registration."""
        now = self._clock()
        with self._lock:
            return sorted(did for did, reg in self._clients.items() if reg.expires_at > now)

    def _on_register(self, obj: dict, addr: tuple) -> None:
        signer = _authentic(obj, self._allowlist, self.did)
        if signer is None:
            return self._drop("unauthenticated")
        now = self._clock()
        ts = obj.get("ts")
        with self._lock:
            if not self._take(signer, now):
                return self._drop("rate_limited")
            if not _is_num(ts) or abs(now - ts) > self._max_skew:
                return self._drop("stale")
            if ts <= self._last_ts.get(signer, float("-inf")):
                return self._drop("replayed")
            if signer not in self._clients:
                self._expire(now)
                if len(self._clients) >= self.max_clients:
                    return self._drop("full")
            self._last_ts[signer] = float(ts)
            self._clients[signer] = _Registration((addr[0], int(addr[1])), now + self.lease)
        self.stats["registered"] += 1
        ack = {"t": RELAY_ACK, "from": self.did, "to": signer, "lease": self.lease, "echo": ts}
        self.transport.channel.send(addr[0], addr[1], _sign(self.identity, ack))

    def _on_send(self, obj: dict, addr: tuple) -> None:
        signer = _authentic(obj, self._allowlist, self.did)
        if signer is None:
            return self._drop("unauthenticated")
        now = self._clock()
        dst, inner = obj.get("dst"), obj.get("inner")
        with self._lock:
            if not self._take(signer, now):
                return self._drop("rate_limited")
            if not isinstance(dst, str) or dst == signer or not isinstance(inner, str) or len(inner) > MAX_INNER:
                return self._drop("malformed")
            if not self._allowlist.contains(dst):
                return self._drop("unauthorized")  # checked at use time, not only at registration
            reg = self._clients.get(dst)
            if reg is None or reg.expires_at <= now:
                return self._drop("no_route")
            target = reg.addr
        deliver = {"t": RELAY_DELIVER, "from": self.did, "to": dst, "src": signer, "inner": inner}
        self.transport.channel.send(target[0], target[1], _sign(self.identity, deliver))
        self.stats["forwarded"] += 1

    def _take(self, did: str, now: float) -> bool:
        bucket = self._buckets.setdefault(did, _Bucket(self._burst, now))
        bucket.tokens = min(self._burst, bucket.tokens + (now - bucket.at) * self._rate)
        bucket.at = now
        if bucket.tokens < 1:
            return False
        bucket.tokens -= 1
        return True

    def _expire(self, now: float) -> None:
        for did in [d for d, reg in self._clients.items() if reg.expires_at <= now]:
            del self._clients[did]

    def _drop(self, reason: str) -> None:
        self.stats[f"dropped_{reason}"] += 1


@dataclass
class _Lease:
    requested_at: float = 0.0
    ts: float = 0.0             # the latest register's ts; only an ack echoing it counts
    pending: bool = True        # that register is still unanswered
    expires_at: float = 0.0     # 0 until the first ack


class RelayClient(Transport):
    """A node's side of the relay role, and the `Transport` to hand
    `DirectUpgrader` as its ``relay``.

    It keeps leases with up to ``max_relays`` relay-role peers from the
    membership view (call `refresh()` every few seconds and advertise what it
    returns), and routes to a peer through one of the relays that peer
    advertises -- relays this node holds a live lease with first, relays it saw
    fail skipped. Relayed inbound messages go to the callback given to
    `register`, separate from the direct transport's, so the upgrader can tell
    the two paths apart."""

    def __init__(self, transport: DirectUDPTransport, membership: MembershipView, *, allowlist,
                 max_relays: int = DEFAULT_MAX_RELAYS, clock=time.time):
        if allowlist is None:
            raise ValueError("a relay client needs an explicit allowlist: it never trusts unknown relays")
        self.transport = transport
        self.identity = transport.identity
        self.did = transport.identity.did
        self.max_relays = int(max_relays)
        self._membership = membership
        self._allowlist = allowlist
        self._clock = clock
        self._leases: dict[str, _Lease] = {}    # relay DID -> lease (insertion order = preference)
        self._down: dict[str, float] = {}       # relay DID -> skipped until
        self._last_ts = 0.0
        self._deliver: DeliverFn | None = None
        self._lock = threading.Lock()
        transport.on_frame(RELAY_ACK, self._on_ack)
        transport.on_frame(RELAY_DELIVER, self._on_deliver)

    def close(self) -> None:
        self.transport.on_frame(RELAY_ACK, None)
        self.transport.on_frame(RELAY_DELIVER, None)

    # -- leases --------------------------------------------------------------

    def relays(self) -> list[str]:
        """Relays holding a live lease for this node -- what to advertise."""
        now = self._clock()
        with self._lock:
            return [r for r, lease in self._leases.items() if lease.expires_at > now]

    def refresh(self) -> list[str]:
        """Maintenance tick. Renews leases on a heartbeat, gives up on relays
        that stop answering (skipping them for a while), and tops up to
        ``max_relays`` from the relay-role peers in the membership view. Returns
        the live relays, to advertise with ``announce(..., relays=...)``."""
        now = self._clock()
        outgoing: list[tuple[str, bytes]] = []
        with self._lock:
            for relay, lease in list(self._leases.items()):
                if lease.pending and now - lease.requested_at > ACK_TIMEOUT:
                    del self._leases[relay]
                    self._down[relay] = now + DOWN_FOR
                elif not lease.pending and now - lease.requested_at >= RENEW_EVERY:
                    outgoing.append((relay, self._request(lease, relay, now)))
            for relay in self._membership.providers(ROLE_RELAY):
                if len(self._leases) >= self.max_relays:
                    break
                if relay == self.did or relay in self._leases or self._down.get(relay, 0.0) > now:
                    continue
                if not self._allowlist.contains(relay) or self._endpoint(relay) is None:
                    continue
                lease = self._leases[relay] = _Lease()
                outgoing.append((relay, self._request(lease, relay, now)))
            live = [r for r, lease in self._leases.items() if lease.expires_at > now]
        for relay, frame in outgoing:
            self._send_to(relay, frame)
        return live

    def _request(self, lease: _Lease, relay: str, now: float) -> bytes:
        # Strictly increasing, so the relay can reject a replayed register.
        self._last_ts = max(now, self._last_ts + 1e-3)
        lease.ts, lease.requested_at, lease.pending = self._last_ts, now, True
        return _sign(self.identity, {"t": RELAY_REGISTER, "from": self.did, "to": relay, "ts": lease.ts})

    def _on_ack(self, obj: dict, addr: tuple) -> None:
        signer = _authentic(obj, self._allowlist, self.did)
        length = obj.get("lease")
        if signer is None or not _is_num(length) or length <= 0:
            return
        with self._lock:
            lease = self._leases.get(signer)
            if lease is None or not lease.pending or obj.get("echo") != lease.ts:
                return  # unsolicited, duplicate, or an old ack replayed
            lease.pending = False
            lease.expires_at = self._clock() + min(float(length), MAX_LEASE)

    # -- inbound -------------------------------------------------------------

    def _on_deliver(self, obj: dict, addr: tuple) -> None:
        signer = _authentic(obj, self._allowlist, self.did)
        if signer is None:
            return
        with self._lock:
            if signer not in self._leases:
                return  # only relays this node asked to serve it
        inner = obj.get("inner")
        if not isinstance(inner, str):
            return
        try:
            frame = base64.b64decode(inner, validate=True)
        except ValueError:
            return
        opened = self.transport.open_relayed(frame)
        if opened is None or opened[0] != obj.get("src"):
            return  # forged / tampered / replayed / relabelled inner frame
        if self._deliver is not None:
            self._deliver(*opened)

    # -- Transport -----------------------------------------------------------

    def register(self, session: Session, deliver: DeliverFn) -> bool:
        """Set the callback for messages that arrive through a relay."""
        self._deliver = deliver
        session.established = True
        return True

    def route(self, from_did: str, to_did: str, message: bytes) -> bool:
        """Send `message` to `to_did` through one of the relays it advertises.
        Returns whether it was handed to a relay (UDP: not a delivery receipt)."""
        if from_did != self.did:
            return False
        record = self._membership.get(to_did)
        if record is None or not record.relays:
            return False
        now = self._clock()
        with self._lock:
            live = {r for r, lease in self._leases.items() if lease.expires_at > now}
            candidates = [r for r in record.relays
                          if r not in (self.did, to_did) and self._down.get(r, 0.0) <= now
                          and self._allowlist.contains(r)]
        candidates.sort(key=lambda r: r not in live)  # stable: proven-alive relays first
        relay = next((r for r in candidates if self._endpoint(r) is not None), None)
        if relay is None:
            return False
        inner = self.transport.build_frame(to_did, message)
        if inner is None:
            return False  # encryption on and no verified key for the peer
        encoded = base64.b64encode(inner).decode("ascii")
        if len(encoded) > MAX_INNER:
            return False
        send = {"t": RELAY_SEND, "from": self.did, "to": relay, "dst": to_did, "inner": encoded}
        return self._send_to(relay, _sign(self.identity, send))

    def migrate(self, did: str, endpoint) -> bool:
        """Nothing to migrate: the relay path is addressed by DID, not endpoint."""
        return False

    # -- helpers -------------------------------------------------------------

    def _endpoint(self, relay: str):
        endpoints = self._membership.endpoints_for(relay)
        return endpoints.best() if endpoints is not None else None

    def _send_to(self, relay: str, frame: bytes) -> bool:
        endpoint = self._endpoint(relay)
        if endpoint is None:
            return False
        self.transport.channel.send(endpoint.host, endpoint.port, frame)
        return True


__all__ = [
    "RELAY_REGISTER",
    "RELAY_ACK",
    "RELAY_SEND",
    "RELAY_DELIVER",
    "DEFAULT_LEASE",
    "RENEW_EVERY",
    "ACK_TIMEOUT",
    "DOWN_FOR",
    "MAX_INNER",
    "RelayService",
    "RelayClient",
]
