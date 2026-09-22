"""Direct-connection upgrade + relay fallback (P2P.2).

Modelled on Tailscale (every connection starts relayed, then upgrades to a direct
path if one can be proven) and libp2p DCUtR (peers exchange non-relay endpoints
and confirm a direct path). Here the flow is:

  1. A peer's candidate + reflexive endpoints are learned via rendezvous (P2P.1).
  2. The upgrader sends a DID-signed liveness PROBE straight to a candidate over
     `DirectUDPTransport`; the far side replies a PROBE-ACK.
  3. A verified round-trip proves the direct path works, so the peer `Session` is
     migrated relay -> direct (identity unchanged -- the same session_id/DID). If
     no ACK arrives, the session stays on the relay. Fallback is never lost.

Authenticity comes for free from `DirectUDPTransport`, which already DID-signs
every datagram; the PROBE/ACK carried inside is just a small typed payload with a
nonce. This adds NO new crypto, no traffic obfuscation, and no detection-evasion.
It is ordinary connectivity verification between the operator's own allowlisted
peers -- a NAT hole-punch here means "let two authorized nodes reach each other",
exactly as Tailscale/libp2p do, never evasion. Confidentiality of real traffic
stays delegated to the tunnel.

The decision logic is a pure state machine (`UpgradeState`); the coordinator
(`DirectUpgrader`) drives it over a real `DirectUDPTransport`, so the whole thing
is exercised headless on 127.0.0.1.

Note on NAT: on a real symmetric NAT the two sides must dial simultaneously
(DCUtR times this with RTT/2). That timing rides these same PROBE messages and is
the refinement layered on top; the verifiable core here is probe -> ack -> migrate,
which needs no NAT to test.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from .endpoint import Endpoint
from .session import Session
from .udp import DirectUDPTransport

PROBE = "secdogie/upgrade/probe/v1"
PROBE_ACK = "secdogie/upgrade/probe-ack/v1"

# Upgrade states. A session is never "below" RELAYED -- the relay is always the
# fallback -- so failure returns here, it never leaves the peer unreachable.
RELAYED = "relayed"
PROBING = "probing"
DIRECT = "direct"


def encode_probe(nonce: str) -> bytes:
    return json.dumps({"t": PROBE, "nonce": nonce}).encode("utf-8")


def encode_probe_ack(nonce: str) -> bytes:
    return json.dumps({"t": PROBE_ACK, "nonce": nonce}).encode("utf-8")


def decode_upgrade(data: bytes) -> dict | None:
    """Parse an inner upgrade message, or None if `data` is not one (an ordinary
    application datagram passes through untouched)."""
    try:
        obj = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return None
    if isinstance(obj, dict) and obj.get("t") in (PROBE, PROBE_ACK):
        return obj
    return None


@dataclass
class UpgradeState:
    """The direct-path state for one peer. Pure: transitions are deterministic
    functions of the events fed in."""

    peer_did: str
    state: str = RELAYED
    direct_endpoint: Endpoint | None = None

    @property
    def is_direct(self) -> bool:
        return self.state == DIRECT

    def begin_probe(self) -> None:
        if self.state != DIRECT:
            self.state = PROBING

    def on_ack(self, endpoint: Endpoint) -> None:
        """A verified direct round-trip on `endpoint` -> upgrade."""
        self.state = DIRECT
        self.direct_endpoint = endpoint

    def on_timeout(self) -> None:
        """No direct path proven -> fall back to the relay (unless already direct)."""
        if self.state != DIRECT:
            self.state = RELAYED
            self.direct_endpoint = None


class DirectUpgrader:
    """Drives the upgrade for a node's peer sessions over one
    `DirectUDPTransport`. Feed every inbound direct datagram to `handle`; call
    `probe` to attempt a candidate; call `on_timeout` when a probe window
    elapses with no ACK."""

    def __init__(self, direct: DirectUDPTransport, session: Session):
        self.direct = direct
        self.session = session  # the peer session this upgrader may migrate
        self._states: dict[str, UpgradeState] = {}
        self._pending: dict[str, Endpoint] = {}  # nonce -> endpoint being probed
        self._counter = 0

    def state_for(self, peer_did: str) -> UpgradeState:
        return self._states.setdefault(peer_did, UpgradeState(peer_did))

    def probe(self, peer_did: str, endpoint: Endpoint) -> bool:
        """Send a signed liveness PROBE to `endpoint` for `peer_did`. Returns
        whether the datagram was sent (the ACK, if any, arrives via `handle`)."""
        st = self.state_for(peer_did)
        st.begin_probe()
        self._counter += 1
        nonce = f"{peer_did}#{self._counter}"
        self._pending[nonce] = endpoint
        self.direct.set_peer_endpoint(peer_did, endpoint.host, endpoint.port)
        return self.direct.route(self.direct.identity.did, peer_did, encode_probe(nonce))

    def handle(self, from_did: str, data: bytes) -> bytes | None:
        """Process one inbound direct datagram. Returns a PROBE-ACK to route back
        when `data` is a PROBE, or None otherwise. A PROBE-ACK for one of our own
        probes migrates the peer session relay -> direct. None is also returned
        for ordinary application data, which the caller then handles normally."""
        msg = decode_upgrade(data)
        if msg is None:
            return None
        if msg["t"] == PROBE:
            return encode_probe_ack(str(msg.get("nonce", "")))
        # PROBE_ACK: a direct round-trip is proven for the endpoint we probed.
        endpoint = self._pending.pop(str(msg.get("nonce", "")), None)
        if endpoint is not None:
            # The verified path is a working direct endpoint; record it as such.
            verified = Endpoint("observed", endpoint.host, endpoint.port)
            self.state_for(from_did).on_ack(verified)
            if from_did == self.session.peer.did:
                self.session.migrate(verified)  # identity unchanged
        return None

    def on_timeout(self, peer_did: str) -> None:
        """Give up on any in-flight probes for `peer_did` and fall back to relay."""
        self._pending = {n: e for n, e in self._pending.items() if not n.startswith(f"{peer_did}#")}
        self.state_for(peer_did).on_timeout()


__all__ = [
    "PROBE",
    "PROBE_ACK",
    "RELAYED",
    "PROBING",
    "DIRECT",
    "encode_probe",
    "encode_probe_ack",
    "decode_upgrade",
    "UpgradeState",
    "DirectUpgrader",
]
