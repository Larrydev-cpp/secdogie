"""Three-tier auto-fallback encrypted tunnel state machine (V1.2 Phase 3).

Every Hub <-> Spoke link tries the fastest local path first and degrades
automatically without dropping frames or compromising end-to-end encryption:

- **Tier 1 (``lan``)**: mDNS + LAN direct QUIC/WSS (< 1 ms, zero external hops).
- **Tier 2 (``webrtc``)**: WebRTC P2P ICE/STUN hole-punching (~15-60 ms).
- **Tier 3 (``relay``)**: Self-hosted encrypted blind relay (``secdogie-tunnel``),
  forwarding only DID-signed E2EE ciphertext frames (the relay can never read
  plaintext or forge frames).

On consecutive failures, ``HybridTunnel`` smoothly degrades
``lan -> webrtc -> relay``. After ``upgrade_cooldown`` seconds, ``probe_upgrade``
probes higher tiers and automatically promotes back to ``webrtc`` or ``lan``
when direct connectivity returns (e.g., phone comes back onto home Wi-Fi).
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .star import SequencedFrameQueue

TIER_LAN = "lan"
TIER_WEBRTC = "webrtc"
TIER_RELAY = "relay"
TIER_ORDER = (TIER_LAN, TIER_WEBRTC, TIER_RELAY)

TIER_LABELS = {
    "zh": {
        TIER_LAN: "LAN 直连",
        TIER_WEBRTC: "WebRTC P2P",
        TIER_RELAY: "E2EE 加密中继",
    },
    "en": {
        TIER_LAN: "LAN direct",
        TIER_WEBRTC: "WebRTC P2P",
        TIER_RELAY: "E2EE relay",
    },
}


def tier_label(tier: str, lang: str = "zh") -> str:
    table = TIER_LABELS["en" if lang == "en" else "zh"]
    return table.get(tier, tier)


@dataclass
class PeerLinkStatus:
    peer_did: str
    tier: str = TIER_LAN
    rtt_ms: int | None = None
    consecutive_failures: int = 0
    last_success: float = 0.0
    last_probe: float = 0.0
    degraded_reason: str = ""
    encrypted: bool = True
    history: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SendOutcome:
    ok: bool
    tier: str
    seq_id: int
    rtt_ms: int | None = None
    degraded_from: str | None = None
    error: str = ""


class HybridTunnel:
    """Three-tier auto-fallback transport coordinator with resumable frame queueing.

    Each tier is backed by a pluggable sender callback:
    ``send_fn(peer_did, frame_dict) -> tuple[bool, int | None, str]``
    returning ``(ok, rtt_ms, reason)``, and an optional probe callback:
    ``probe_fn(peer_did) -> tuple[bool, int | None]``.
    """

    def __init__(
        self,
        *,
        senders: dict[str, Callable[[str, dict], tuple[bool, int | None, str]]] | None = None,
        probes: dict[str, Callable[[str], tuple[bool, int | None]]] | None = None,
        failure_threshold: int = 2,
        upgrade_cooldown: float = 15.0,
        require_e2ee_on_relay: bool = True,
        clock: Callable[[], float] = time.monotonic,
    ):
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        self._senders = dict(senders or {})
        self._probes = dict(probes or {})
        self.failure_threshold = int(failure_threshold)
        self.upgrade_cooldown = float(upgrade_cooldown)
        self.require_e2ee_on_relay = bool(require_e2ee_on_relay)
        self._clock = clock
        self._peers: dict[str, PeerLinkStatus] = {}
        self._queues: dict[str, SequencedFrameQueue] = {}

    def register_tier(
        self,
        tier: str,
        sender: Callable[[str, dict], tuple[bool, int | None, str]],
        probe: Callable[[str], tuple[bool, int | None]] | None = None,
    ) -> None:
        if tier not in TIER_ORDER:
            raise ValueError(f"unknown tier {tier!r}")
        self._senders[tier] = sender
        if probe is not None:
            self._probes[tier] = probe

    def status_of(self, peer_did: str) -> PeerLinkStatus:
        st = self._peers.get(peer_did)
        if st is None:
            initial = next((t for t in TIER_ORDER if t in self._senders), TIER_LAN)
            st = PeerLinkStatus(peer_did=peer_did, tier=initial)
            self._peers[peer_did] = st
        return st

    def queue_of(self, peer_did: str) -> SequencedFrameQueue:
        q = self._queues.get(peer_did)
        if q is None:
            q = SequencedFrameQueue(clock=self._clock)
            self._queues[peer_did] = q
        return q

    def _next_tier(self, current: str) -> str | None:
        idx = TIER_ORDER.index(current)
        for cand in TIER_ORDER[idx + 1:]:
            if cand in self._senders:
                return cand
        return None

    def _higher_tiers(self, current: str) -> list[str]:
        idx = TIER_ORDER.index(current)
        return [t for t in TIER_ORDER[:idx] if t in self._senders]

    def _is_e2ee_envelope(self, frame: dict) -> bool:
        """Relay tier is a blind pipe: refuse unencrypted frames lacking signature/ciphertext markers."""
        if frame.get("encrypted") is True:
            return True
        if frame.get("sig") or frame.get("ciphertext") or frame.get("sealed"):
            return True
        return False

    def send(self, peer_did: str, payload: dict, *, seq_id: int | None = None) -> SendOutcome:
        """Send a frame to ``peer_did``, auto-degrading through tiers on failure."""
        st = self.status_of(peer_did)
        q = self.queue_of(peer_did)
        if seq_id is None:
            framed = q.enqueue(payload)
            wire = framed.wire()
            assigned_seq = framed.seq_id
        else:
            wire = dict(payload)
            wire["seq_id"] = int(seq_id)
            assigned_seq = int(seq_id)

        start_tier = st.tier
        last_error = "no sender configured"

        while True:
            tier = st.tier
            sender = self._senders.get(tier)
            if sender is None:
                nxt = self._next_tier(tier)
                if nxt is None:
                    return SendOutcome(ok=False, tier=tier, seq_id=assigned_seq, error=last_error)
                st.tier = nxt
                st.consecutive_failures = 0
                st.degraded_reason = f"{tier}: unavailable"
                st.history.append(f"{tier}->{nxt}")
                continue

            if tier == TIER_RELAY and self.require_e2ee_on_relay and not self._is_e2ee_envelope(wire):
                return SendOutcome(
                    ok=False,
                    tier=tier,
                    seq_id=assigned_seq,
                    error="relay refuses plaintext frame: E2EE/DID-signed envelope required",
                )

            try:
                ok, rtt_ms, reason = sender(peer_did, wire)
            except Exception as exc:  # noqa: BLE001 - transport errors trigger fallback
                ok, rtt_ms, reason = False, None, str(exc)

            if ok:
                st.consecutive_failures = 0
                st.rtt_ms = rtt_ms
                st.last_success = float(self._clock())
                degraded_from = start_tier if st.tier != start_tier else None
                return SendOutcome(
                    ok=True,
                    tier=tier,
                    seq_id=assigned_seq,
                    rtt_ms=rtt_ms,
                    degraded_from=degraded_from,
                )

            st.consecutive_failures += 1
            last_error = reason or f"{tier} send failed"
            if st.consecutive_failures >= self.failure_threshold:
                nxt = self._next_tier(tier)
                if nxt is not None:
                    st.tier = nxt
                    st.consecutive_failures = 0
                    st.degraded_reason = f"{tier}: {last_error}"
                    st.history.append(f"{tier}->{nxt}")
                    # Immediately attempt delivery on the newly degraded tier so this frame is not lost.
                    continue
            return SendOutcome(ok=False, tier=tier, seq_id=assigned_seq, error=last_error)

    def ack(self, peer_did: str, last_ack_seq: int) -> int:
        return self.queue_of(peer_did).ack(last_ack_seq)

    def replay_pending(self, peer_did: str, last_ack_seq: int) -> list[SendOutcome]:
        """Replay every unacknowledged frame after ``last_ack_seq`` on reconnect."""
        frames = self.queue_of(peer_did).replay_after(last_ack_seq)
        outcomes: list[SendOutcome] = []
        for frame in frames:
            outcomes.append(self.send(peer_did, frame.payload, seq_id=frame.seq_id))
        return outcomes

    def probe_upgrade(self, peer_did: str, *, force: bool = False) -> str:
        """Probe higher tiers (e.g. relay -> webrtc -> lan) and promote on success."""
        st = self.status_of(peer_did)
        now = float(self._clock())
        if not force and (now - st.last_probe) < self.upgrade_cooldown:
            return st.tier
        st.last_probe = now
        for candidate in self._higher_tiers(st.tier):
            probe = self._probes.get(candidate)
            if probe is None:
                continue
            try:
                ok, rtt_ms = probe(peer_did)
            except Exception:  # noqa: BLE001
                ok, rtt_ms = False, None
            if ok:
                prev = st.tier
                st.tier = candidate
                st.rtt_ms = rtt_ms
                st.consecutive_failures = 0
                st.degraded_reason = ""
                st.last_success = now
                st.history.append(f"{prev}->{candidate}")
                return st.tier
        return st.tier

    def status_snapshot(self, *, lang: str = "zh") -> dict[str, dict]:
        out: dict[str, dict] = {}
        for did, st in self._peers.items():
            q = self._queues.get(did)
            out[did] = {
                "tier": st.tier,
                "tier_label": tier_label(st.tier, lang),
                "rtt_ms": st.rtt_ms,
                "encrypted": st.encrypted,
                "degraded_reason": st.degraded_reason,
                "unacked": q.pending_count if q is not None else 0,
                "history": list(st.history),
            }
        return out


__all__ = [
    "HybridTunnel",
    "PeerLinkStatus",
    "SendOutcome",
    "TIER_LABELS",
    "TIER_LAN",
    "TIER_ORDER",
    "TIER_RELAY",
    "TIER_WEBRTC",
    "tier_label",
]
