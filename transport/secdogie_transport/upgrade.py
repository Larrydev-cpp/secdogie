"""Direct-connection upgrade + relay fallback (P2P.2, completed).

Modelled on Tailscale (every connection starts relayed, then upgrades to a direct
path if one can be proven) and libp2p DCUtR (peers exchange non-relay endpoints
over the relay and dial simultaneously). The lifecycle here is:

  1. A peer's candidate + reflexive endpoints are learned via rendezvous (P2P.1).
  2. Relay-mediated coordination (`connect` / `handle_relay`): a node sends a
     signed CONNECT over the relay carrying its own candidate endpoints; the peer
     records them, replies a CONNECT with its own, and both sides dial (PROBE) the
     other's endpoints at nearly the same time. This is what makes a hole-punch
     work when both peers are behind NAT -- neither has to be dialable first.
  3. A verified round-trip (PROBE -> PROBE-ACK) proves the direct path works, so
     the peer `Session` is migrated relay -> direct (identity unchanged -- same
     session_id / DID). If no ACK arrives, the session stays on the relay.
  4. Liveness + fallback: while direct, every authenticated inbound packet from
     the peer refreshes `last_seen`; a periodic `keepalive` re-probe keeps a quiet
     path warm. `sweep` downgrades a direct path that has gone silent back to the
     relay, so the relay is a fallback *after* an upgrade too, not only before it.
  5. Route selection (`send`): an application message goes over the direct path
     when the peer is DIRECT and falls back to the relay otherwise.

Authenticity comes for free from the transports, which already DID-sign every
datagram; the CONNECT / PROBE / PROBE-ACK carried inside are small typed payloads
with a nonce. This adds NO new crypto, no traffic obfuscation, and no
detection-evasion. It is ordinary connectivity verification between the operator's
own allowlisted peers -- a NAT hole-punch here means "let two authorized nodes
reach each other", exactly as Tailscale / libp2p do, never evasion. Confidentiality
of real traffic stays delegated to the tunnel.

The decision logic is a pure state machine (`UpgradeState`); the coordinator
(`DirectUpgrader`) drives it over a real `DirectUDPTransport` (direct path) and an
optional relay `Transport`, so the whole thing is exercised headless on 127.0.0.1.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

from .endpoint import Endpoint
from .session import Session
from .transport import Transport
from .udp import DirectUDPTransport

PROBE = "secdogie/upgrade/probe/v1"
PROBE_ACK = "secdogie/upgrade/probe-ack/v1"
CONNECT = "secdogie/upgrade/connect/v1"

# Upgrade states. A session is never "below" RELAYED -- the relay is always the
# fallback -- so failure (or a silent direct path) returns here; it never leaves
# the peer unreachable.
RELAYED = "relayed"
PROBING = "probing"
DIRECT = "direct"

# How long a direct path may be silent before `sweep` downgrades it to the relay.
DEFAULT_DEAD_AFTER = 30.0


def encode_probe(nonce: str) -> bytes:
    return json.dumps({"t": PROBE, "nonce": nonce}).encode("utf-8")


def encode_probe_ack(nonce: str) -> bytes:
    return json.dumps({"t": PROBE_ACK, "nonce": nonce}).encode("utf-8")


def encode_connect(endpoints, *, reply: bool = False) -> bytes:
    """A relay-carried CONNECT advertising this node's candidate endpoints, so the
    peer can dial them. `reply` marks the answer to a CONNECT, stopping the
    exchange after one round-trip."""
    return json.dumps({
        "t": CONNECT,
        "reply": bool(reply),
        "endpoints": [{"kind": e.kind, "host": e.host, "port": e.port} for e in endpoints],
    }).encode("utf-8")


def decode_upgrade(data: bytes) -> dict | None:
    """Parse an inner upgrade message (PROBE / PROBE-ACK / CONNECT), or None if
    `data` is not one (an ordinary application datagram passes through untouched)."""
    try:
        obj = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return None
    if isinstance(obj, dict) and obj.get("t") in (PROBE, PROBE_ACK, CONNECT):
        return obj
    return None


def _endpoints_from(obj: dict) -> list[Endpoint]:
    out: list[Endpoint] = []
    for e in obj.get("endpoints", []):
        try:
            out.append(Endpoint(e["kind"], e["host"], int(e["port"])))
        except (KeyError, ValueError, TypeError):
            continue  # a malformed candidate is skipped, never fatal
    return out


@dataclass
class UpgradeState:
    """The direct-path state for one peer. Pure: transitions are deterministic
    functions of the events fed in."""

    peer_did: str
    state: str = RELAYED
    direct_endpoint: Endpoint | None = None
    last_seen: float = 0.0

    @property
    def is_direct(self) -> bool:
        return self.state == DIRECT

    def begin_probe(self) -> None:
        if self.state != DIRECT:
            self.state = PROBING

    def on_ack(self, endpoint: Endpoint, now: float | None = None) -> None:
        """A verified direct round-trip on `endpoint` -> upgrade (and, if already
        direct, a refreshed liveness timestamp from the keepalive re-probe)."""
        self.state = DIRECT
        self.direct_endpoint = endpoint
        self.last_seen = time.time() if now is None else float(now)

    def touch(self, now: float | None = None) -> None:
        """An authenticated inbound packet arrived from the peer on the direct
        path: the path is alive, so refresh liveness (only while DIRECT)."""
        if self.state == DIRECT:
            self.last_seen = time.time() if now is None else float(now)

    def on_timeout(self) -> None:
        """No direct path proven -> fall back to the relay (unless already direct)."""
        if self.state != DIRECT:
            self.state = RELAYED
            self.direct_endpoint = None

    def expire(self, now: float | None = None, dead_after: float = DEFAULT_DEAD_AFTER) -> bool:
        """Downgrade a DIRECT path that has been silent longer than `dead_after`
        back to the relay. Returns whether a downgrade happened."""
        if self.state != DIRECT:
            return False
        t = time.time() if now is None else float(now)
        if t - self.last_seen <= dead_after:
            return False
        self.state = RELAYED
        self.direct_endpoint = None
        return True


class DirectUpgrader:
    """Drives the upgrade for a node's peer sessions over one
    `DirectUDPTransport`, with an optional relay `Transport` for coordination and
    fallback. Feed every inbound direct datagram to `handle` and every inbound
    relay datagram to `handle_relay`; use `send` to route an application message,
    `keepalive` to keep a quiet direct path warm, and `sweep` to downgrade silent
    ones."""

    def __init__(self, direct: DirectUDPTransport, session: Session,
                 *, relay: Transport | None = None):
        self.direct = direct
        self.relay = relay
        self.session = session  # the peer session this upgrader may migrate
        self._states: dict[str, UpgradeState] = {}
        self._pending: dict[str, Endpoint] = {}  # nonce -> endpoint being probed
        self._counter = 0

    @property
    def did(self) -> str:
        return self.direct.identity.did

    def state_for(self, peer_did: str) -> UpgradeState:
        return self._states.setdefault(peer_did, UpgradeState(peer_did))

    # -- dialing -------------------------------------------------------------

    def probe(self, peer_did: str, endpoint: Endpoint) -> bool:
        """Send a signed liveness PROBE to `endpoint` for `peer_did`. Returns
        whether the datagram was sent (the ACK, if any, arrives via `handle`)."""
        st = self.state_for(peer_did)
        st.begin_probe()
        self._counter += 1
        nonce = f"{peer_did}#{self._counter}"
        self._pending[nonce] = endpoint
        self.direct.set_peer_endpoint(peer_did, endpoint.host, endpoint.port)
        return self.direct.route(self.did, peer_did, encode_probe(nonce))

    def keepalive(self, peer_did: str) -> bool:
        """Re-probe the current direct endpoint to keep a quiet path warm (the ACK
        refreshes `last_seen`). No-op unless the peer is DIRECT."""
        st = self.state_for(peer_did)
        if not st.is_direct or st.direct_endpoint is None:
            return False
        return self.probe(peer_did, st.direct_endpoint)

    def connect(self, peer_did: str, endpoints, *, reply: bool = False) -> bool:
        """Relay-mediated coordination: advertise our candidate `endpoints` to the
        peer over the relay so both sides can dial. Requires a relay transport."""
        if self.relay is None:
            return False
        return self.relay.route(self.did, peer_did, encode_connect(endpoints, reply=reply))

    # -- inbound -------------------------------------------------------------

    def handle(self, from_did: str, data: bytes) -> bytes | None:
        """Process one inbound direct datagram. Returns a PROBE-ACK to route back
        when `data` is a PROBE, or None otherwise. A PROBE-ACK for one of our own
        probes migrates the peer session relay -> direct. Any authenticated inbound
        packet from a DIRECT peer refreshes its liveness. None is also returned for
        ordinary application data, which the caller then handles normally."""
        self.state_for(from_did).touch()
        msg = decode_upgrade(data)
        if msg is None:
            return None
        if msg["t"] == PROBE:
            return encode_probe_ack(str(msg.get("nonce", "")))
        if msg["t"] == PROBE_ACK:
            # a direct round-trip is proven for the endpoint we probed
            endpoint = self._pending.pop(str(msg.get("nonce", "")), None)
            if endpoint is not None:
                verified = Endpoint("observed", endpoint.host, endpoint.port)
                self.state_for(from_did).on_ack(verified)
                if from_did == self.session.peer.did:
                    self.session.migrate(verified)  # identity unchanged
        return None

    def handle_relay(self, from_did: str, data: bytes, *, my_endpoints=()) -> bool:
        """Process one inbound relay datagram. On a CONNECT, dial every advertised
        endpoint (the simultaneous-open half of the hole-punch) and, unless this is
        already a reply, answer with our own `my_endpoints` so the peer dials us
        too. Returns whether it was a CONNECT."""
        msg = decode_upgrade(data)
        if msg is None or msg["t"] != CONNECT:
            return False
        for ep in _endpoints_from(msg):
            self.probe(from_did, ep)
        if not msg.get("reply"):
            self.connect(from_did, my_endpoints, reply=True)
        return True

    # -- fallback ------------------------------------------------------------

    def on_timeout(self, peer_did: str) -> None:
        """Give up on any in-flight probes for `peer_did` and fall back to relay."""
        self._pending = {n: e for n, e in self._pending.items() if not n.startswith(f"{peer_did}#")}
        self.state_for(peer_did).on_timeout()

    def sweep(self, *, now: float | None = None, dead_after: float = DEFAULT_DEAD_AFTER) -> list[str]:
        """Downgrade every DIRECT peer whose path has gone silent back to the
        relay. Returns the DIDs that were downgraded."""
        return [did for did, st in self._states.items()
                if st.expire(now=now, dead_after=dead_after)]

    # -- routing -------------------------------------------------------------

    def send(self, peer_did: str, message: bytes) -> str:
        """Route an application message to `peer_did`: over the direct path when
        the peer is DIRECT, else over the relay. Returns the path used
        ("direct" / "relay" / "unreachable")."""
        st = self.state_for(peer_did)
        if st.is_direct and self.direct.route(self.did, peer_did, message):
            return "direct"
        if self.relay is not None and self.relay.route(self.did, peer_did, message):
            return "relay"
        return "unreachable"


__all__ = [
    "PROBE",
    "PROBE_ACK",
    "CONNECT",
    "RELAYED",
    "PROBING",
    "DIRECT",
    "DEFAULT_DEAD_AFTER",
    "encode_probe",
    "encode_probe_ack",
    "encode_connect",
    "decode_upgrade",
    "UpgradeState",
    "DirectUpgrader",
]
