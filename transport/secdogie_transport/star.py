"""Hub-and-Spoke star topology and resumable idempotent frame queue (V1.2 Phase 3).

SecDogie V1.2 shifts multi-device networking from flat full-mesh to a local
primary-controller star topology:

- ``ROLE_HUB`` ("hub"): the user's PC running the local SNN cortex, memory,
  RSI evolution lock, and policy authority. Exactly one active Hub per mesh.
- ``ROLE_SPOKE`` ("spoke"): phone / tablet / secondary light nodes that send
  goals, approvals, and notes to the Hub, and only accept control / policy /
  evolution snapshots signed/originated by their pinned ``hub_did``.

Mobile spokes routinely suspend in the background and drop sockets. To keep
every control frame and Gate 2 approval lossless and exactly-once:

- ``SequencedFrameQueue`` assigns strictly monotonic ``seq_id`` (1, 2, ...),
  buffers unacknowledged frames in a bounded ring, trims on ``ack(last_ack_seq)``,
  and replays ``seq_id > last_ack_seq`` on reconnect.
- ``IdempotentReceiver`` tracks delivered ``seq_id`` values so replayed frames
  are acknowledged cleanly without double-executing any action or approval.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

ROLE_HUB = "hub"
ROLE_SPOKE = "spoke"
VALID_ROLES = frozenset({ROLE_HUB, ROLE_SPOKE})

DEFAULT_MAX_BUFFER = 512
DEFAULT_SEEN_WINDOW = 2048


class StarAuthorityError(ValueError):
    """Raised when a star-topology role or single-Hub authority rule is violated."""


@dataclass
class SequencedFrame:
    seq_id: int
    payload: dict
    created_at: float
    frame_id: str = ""

    def wire(self) -> dict:
        out = dict(self.payload)
        out["seq_id"] = self.seq_id
        if self.frame_id:
            out["frame_id"] = self.frame_id
        return out


class SequencedFrameQueue:
    """Outbound monotonic ``seq_id`` buffer supporting ``ack`` and ``replay_after``."""

    def __init__(self, *, max_buffer: int = DEFAULT_MAX_BUFFER, clock=time.time):
        if max_buffer < 1:
            raise ValueError("max_buffer must be >= 1")
        self.max_buffer = int(max_buffer)
        self._clock = clock
        self._next_seq = 0
        self._last_ack = 0
        self._pending: deque[SequencedFrame] = deque()

    @property
    def last_seq(self) -> int:
        return self._next_seq

    @property
    def last_ack_seq(self) -> int:
        return self._last_ack

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def enqueue(self, payload: dict, *, frame_id: str = "") -> SequencedFrame:
        """Assign the next monotonic ``seq_id`` and buffer the frame."""
        self._next_seq += 1
        frame = SequencedFrame(
            seq_id=self._next_seq,
            payload=dict(payload),
            created_at=float(self._clock()),
            frame_id=frame_id or f"seq-{self._next_seq}",
        )
        self._pending.append(frame)
        while len(self._pending) > self.max_buffer:
            self._pending.popleft()
        return frame

    def ack(self, last_ack_seq: int) -> int:
        """Acknowledge all frames with ``seq_id <= last_ack_seq``. Returns count trimmed."""
        seq = int(last_ack_seq)
        if seq <= self._last_ack:
            return 0
        self._last_ack = min(seq, self._next_seq) if self._next_seq else seq
        trimmed = 0
        while self._pending and self._pending[0].seq_id <= seq:
            self._pending.popleft()
            trimmed += 1
        return trimmed

    def replay_after(self, last_ack_seq: int) -> list[SequencedFrame]:
        """Trim acknowledged frames and return ordered frames with ``seq_id > last_ack_seq``."""
        seq = max(0, int(last_ack_seq))
        if seq > self._last_ack:
            self.ack(seq)
        return [f for f in self._pending if f.seq_id > seq]

    def resume_message(self, last_received_seq: int) -> dict:
        return {"m": "resume", "last_ack_seq": max(0, int(last_received_seq))}


class IdempotentReceiver:
    """Inbound deduplicator guaranteeing at-most-once execution for replayed frames."""

    def __init__(self, *, max_seen: int = DEFAULT_SEEN_WINDOW):
        self.max_seen = max(16, int(max_seen))
        self._last_delivered_seq = 0
        self._seen_order: deque[int] = deque()
        self._seen_set: set[int] = set()

    @property
    def last_delivered_seq(self) -> int:
        return self._last_delivered_seq

    def accept(self, seq_id: int) -> tuple[bool, int]:
        """Check whether ``seq_id`` is fresh.

        Returns ``(is_new, ack_seq)``:
        - ``is_new == True``: caller should execute the frame once.
        - ``is_new == False``: duplicate replay (already processed); caller should
          NOT re-execute it, and should still acknowledge ``ack_seq``.
        """
        seq = int(seq_id)
        if seq <= 0:
            return False, self._last_delivered_seq
        if seq in self._seen_set or seq <= self._last_delivered_seq:
            return False, max(self._last_delivered_seq, seq)
        self._seen_set.add(seq)
        self._seen_order.append(seq)
        while len(self._seen_order) > self.max_seen:
            old = self._seen_order.popleft()
            self._seen_set.discard(old)
        if seq > self._last_delivered_seq:
            self._last_delivered_seq = seq
        return True, self._last_delivered_seq

    def ack_message(self) -> dict:
        return {"m": "ack", "seq_id": self._last_delivered_seq}


@dataclass
class SpokeRecord:
    did: str
    name: str = ""
    role: str = ROLE_SPOKE
    tier: str = "lan"
    online: bool = False
    rtt_ms: int | None = None
    last_seen: float = 0.0
    outbound: SequencedFrameQueue = field(default_factory=SequencedFrameQueue)
    inbound: IdempotentReceiver = field(default_factory=IdempotentReceiver)


class StarTopology:
    """Enforces local Hub-and-Spoke star topology and single-Hub authority."""

    def __init__(self, self_did: str, *, role: str = ROLE_HUB, hub_did: str | None = None, clock=time.time):
        if role not in VALID_ROLES:
            raise StarAuthorityError(f"invalid role {role!r}; expected 'hub' or 'spoke'")
        if role == ROLE_HUB:
            if hub_did and hub_did != self_did:
                raise StarAuthorityError("a Hub node must be its own hub_did")
            resolved_hub = self_did
        else:
            if not hub_did or hub_did == self_did:
                raise StarAuthorityError("a Spoke node must pin a distinct hub_did")
            resolved_hub = hub_did

        self.self_did = self_did
        self.role = role
        self.hub_did = resolved_hub
        self._clock = clock
        self.spokes: dict[str, SpokeRecord] = {}
        self._hub_outbound = SequencedFrameQueue(clock=clock)
        self._hub_inbound = IdempotentReceiver()

    @property
    def is_hub(self) -> bool:
        return self.role == ROLE_HUB

    def register_peer(self, did: str, *, role: str = ROLE_SPOKE, name: str = "", tier: str = "lan") -> SpokeRecord:
        """Register a connected peer while enforcing single-Hub authority."""
        if role not in VALID_ROLES:
            raise StarAuthorityError(f"invalid peer role {role!r}")
        if did == self.self_did:
            raise StarAuthorityError("cannot register self as remote peer")
        if self.is_hub and role == ROLE_HUB:
            raise StarAuthorityError(
                f"single-Hub authority violation: hub {self.self_did} refuses second active hub {did}"
            )
        if not self.is_hub and role == ROLE_HUB and did != self.hub_did:
            raise StarAuthorityError(
                f"spoke {self.self_did} is pinned to hub {self.hub_did} and refuses foreign hub {did}"
            )
        rec = self.spokes.get(did)
        if rec is None:
            rec = SpokeRecord(
                did=did,
                name=name,
                role=role,
                tier=tier,
                online=True,
                last_seen=float(self._clock()),
                outbound=SequencedFrameQueue(clock=self._clock),
                inbound=IdempotentReceiver(),
            )
            self.spokes[did] = rec
        else:
            rec.name = name or rec.name
            rec.role = role
            rec.tier = tier or rec.tier
            rec.online = True
            rec.last_seen = float(self._clock())
        return rec

    def can_accept_control_from(self, sender_did: str) -> bool:
        """Spokes only accept policy/control/evolution snapshots from their pinned Hub."""
        if self.is_hub:
            return sender_did in self.spokes
        return sender_did == self.hub_did

    def can_accept_evolution_from(self, sender_did: str) -> bool:
        """Evolution snapshots are strictly Hub -> Spoke (never Spoke -> Hub)."""
        if self.is_hub:
            return False
        return sender_did == self.hub_did

    def queue_for(self, peer_did: str) -> SequencedFrameQueue:
        if not self.is_hub and peer_did == self.hub_did:
            return self._hub_outbound
        rec = self.spokes.get(peer_did)
        if rec is None:
            rec = self.register_peer(peer_did, role=ROLE_HUB if peer_did == self.hub_did else ROLE_SPOKE)
        return rec.outbound

    def receiver_for(self, peer_did: str) -> IdempotentReceiver:
        if not self.is_hub and peer_did == self.hub_did:
            return self._hub_inbound
        rec = self.spokes.get(peer_did)
        if rec is None:
            rec = self.register_peer(peer_did, role=ROLE_HUB if peer_did == self.hub_did else ROLE_SPOKE)
        return rec.inbound

    def snapshot(self) -> dict:
        return {
            "role": self.role,
            "self_did": self.self_did,
            "hub_did": self.hub_did,
            "peers": [
                {
                    "did": rec.did,
                    "name": rec.name,
                    "role": rec.role,
                    "tier": rec.tier,
                    "online": rec.online,
                    "rtt_ms": rec.rtt_ms,
                    "unacked": rec.outbound.pending_count,
                    "last_delivered_seq": rec.inbound.last_delivered_seq,
                }
                for rec in self.spokes.values()
            ],
        }


__all__ = [
    "DEFAULT_MAX_BUFFER",
    "DEFAULT_SEEN_WINDOW",
    "IdempotentReceiver",
    "ROLE_HUB",
    "ROLE_SPOKE",
    "SequencedFrame",
    "SequencedFrameQueue",
    "SpokeRecord",
    "StarAuthorityError",
    "StarTopology",
    "VALID_ROLES",
]
