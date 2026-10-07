"""The dialogue session: one operator App <-> one Agent node, over a lossy link.

Signed envelopes (``protocol.seal`` / ``open_envelope``) say *who* and *what*;
this module makes them arrive over a transport that loses, duplicates and
reorders datagrams (UDP, a relay):

  * **Fragmentation.** An envelope can be large (a full structural snapshot).
    It is cut into fragments of at most ``fragment_size`` bytes and reassembled
    on the other side, under caps on message size, concurrent reassemblies and
    reassembly age.
  * **A reliable control channel.** Dialogue, Gate 2, control and session
    packets are acknowledged once reassembled and retransmitted with exponential
    backoff until acknowledged. After ``retry_max`` attempts the sender gives up
    and reports it (``on_undeliverable``) -- the caller fails closed; nothing is
    ever assumed delivered. A duplicate is acknowledged again but not delivered
    again (the envelope's replay window refuses it).
  * **Snapshots are unreliable.** A newer one supersedes a lost one, and a lost
    delta is detected by its ``base_generation`` (the inspector asks for a
    resync) rather than retransmitted.
  * **Liveness.** Heartbeats every ``heartbeat_interval``; a peer not heard from
    for ``dead_after`` intervals is reported down (``on_peer_down``), and up
    again when it is heard from.

Everything time-based happens in ``tick(now)``, so tests drive it with a fake
clock; ``start()`` runs the ticks on a daemon thread in production. The link is
just ``send(bytes) -> bool`` out and ``receive(bytes)`` in; ``SessionRouter``
binds sessions to a ``ChannelMux`` channel by the transport-verified peer DID.

Frames (inside the transport's signed frame):
    b"D" msg_id:u64 idx:u16 total:u16 flags:u8 chunk     a fragment (flags bit0 = reliable)
    b"A" msg_id:u64                                    an acknowledgment
"""
from __future__ import annotations

import json
import logging
import secrets
import struct
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .protocol import (
    Envelope,
    PacketKind,
    ReplayGuard,
    Sender,
    SessionEvent,
    SessionPacket,
    kind_of,
    open_envelope,
)

CHANNEL = "dialogue/v1"

FRAGMENT_SIZE = 16 * 1024
MAX_MESSAGE = 4 * 1024 * 1024
MAX_REASSEMBLIES = 32
REASSEMBLY_TIMEOUT = 10.0
RETRY_BASE = 0.25
RETRY_MAX = 6
HEARTBEAT_INTERVAL = 2.0
DEAD_AFTER = 3

_FRAG = struct.Struct(">QHHB")  # msg_id, idx, total, flags
_ACK = struct.Struct(">Q")
_RELIABLE = 1

log = logging.getLogger("secdogie_dialogue.session")


def fragments(msg_id: int, data: bytes, *, reliable: bool, size: int = FRAGMENT_SIZE) -> list[bytes]:
    total = max(1, -(-len(data) // size))
    if total > 0xFFFF:
        raise ValueError("message too large to fragment")
    flags = _RELIABLE if reliable else 0
    return [b"D" + _FRAG.pack(msg_id, i, total, flags) + data[i * size:(i + 1) * size] for i in range(total)]


def default_reliable(packet) -> bool:
    """Snapshots and heartbeats are fire-and-forget; everything else must arrive."""
    kind = kind_of(packet)
    if kind is PacketKind.STATE_SNAPSHOT:
        return False
    if kind is PacketKind.SESSION and packet.event is SessionEvent.HEARTBEAT:
        return False
    return True


@dataclass
class _Outstanding:
    packet: object
    frames: list[bytes]
    attempts: int
    next_at: float


@dataclass
class _Reassembly:
    total: int
    reliable: bool
    started: float
    parts: dict[int, bytes] = field(default_factory=dict)
    size: int = 0


def encode_envelope(env: dict) -> bytes:
    """A sealed envelope's bytes inside the session's D fragments: compact JSON,
    insertion order, ASCII-escaped (``json.dumps`` defaults otherwise). The
    signature covers the canonical form, so a receiver re-encodes; these are
    only the bytes on the wire."""
    return json.dumps(env, separators=(",", ":")).encode("utf-8")


class DialogueSession:
    """One side of one App <-> node session. ``identity`` signs our envelopes;
    ``peer_did`` is the only sender we deliver from; ``trust`` (allowlist /
    ``TrustPolicy``) must also contain it, so a revoked peer stops being heard."""

    def __init__(self, identity, peer_did: str, send_bytes: Callable[[bytes], bool], *, trust,
                 replay: ReplayGuard | None = None, clock=time.monotonic, wall_ns=time.time_ns,
                 fragment_size: int = FRAGMENT_SIZE, max_message: int = MAX_MESSAGE,
                 max_reassemblies: int = MAX_REASSEMBLIES, reassembly_timeout: float = REASSEMBLY_TIMEOUT,
                 retry_base: float = RETRY_BASE, retry_max: int = RETRY_MAX,
                 heartbeat_interval: float = HEARTBEAT_INTERVAL, dead_after: int = DEAD_AFTER):
        if trust is None:
            raise ValueError("a dialogue session needs a trust policy for its peer")
        self.identity = identity
        self.peer_did = peer_did
        self._send_bytes = send_bytes
        self._trust = trust
        self._replay = replay or ReplayGuard(clock_ns=wall_ns)
        self._sender = Sender(identity, peer_did, clock_ns=wall_ns)
        self._clock = clock
        self._fragment_size = int(fragment_size)
        self._max_message = int(max_message)
        self._max_reassemblies = int(max_reassemblies)
        self._reassembly_timeout = float(reassembly_timeout)
        self._retry_base = float(retry_base)
        self._retry_max = int(retry_max)
        self._heartbeat_interval = float(heartbeat_interval)
        self._dead_after = int(dead_after)
        self._next_msg_id = secrets.randbits(62)
        self._outstanding: dict[int, _Outstanding] = {}
        self._reassembly: dict[int, _Reassembly] = {}
        now = float(clock())
        self._last_heard = now
        self._next_heartbeat = now + self._heartbeat_interval  # the owner says HELLO; heartbeats follow
        self._alive = True
        self._lock = threading.RLock()
        self._stop: threading.Event | None = None
        # callbacks (set by the owner)
        self.on_envelope: Callable[[Envelope], None] | None = None
        self.on_undeliverable: Callable[[int, object], None] | None = None
        self.on_peer_down: Callable[[], None] | None = None
        self.on_peer_up: Callable[[], None] | None = None

    # -- sending ---------------------------------------------------------------

    def send(self, packet, *, reliable: bool | None = None) -> int:
        """Seal ``packet`` for the peer and send it; returns its message id.
        Reliable packets are retransmitted until acknowledged or given up on."""
        reliable = default_reliable(packet) if reliable is None else bool(reliable)
        data = encode_envelope(self._sender.seal(packet))
        if len(data) > self._max_message:
            raise ValueError("packet exceeds the session's message size limit")
        with self._lock:
            msg_id = self._next_msg_id
            self._next_msg_id += 1
            frames = fragments(msg_id, data, reliable=reliable, size=self._fragment_size)
            if reliable:
                self._outstanding[msg_id] = _Outstanding(packet, frames, 1,
                                                         float(self._clock()) + self._retry_base)
        self._emit(frames)
        return msg_id

    @property
    def pending(self) -> int:
        """Reliable messages not yet acknowledged."""
        with self._lock:
            return len(self._outstanding)

    @property
    def alive(self) -> bool:
        return self._alive

    # -- receiving -------------------------------------------------------------

    def receive(self, frame: bytes) -> None:
        """One frame from the peer (already authenticated by the transport as
        coming from ``peer_did``). Never raises for bad input."""
        if not frame:
            return
        now = float(self._clock())
        self._heard(now)
        tag = frame[:1]
        if tag == b"A" and len(frame) == 1 + _ACK.size:
            (msg_id,) = _ACK.unpack_from(frame, 1)
            with self._lock:
                self._outstanding.pop(msg_id, None)
            return
        if tag != b"D" or len(frame) < 1 + _FRAG.size:
            return
        msg_id, idx, total, flags = _FRAG.unpack_from(frame, 1)
        chunk = frame[1 + _FRAG.size:]
        done = self._reassemble(now, msg_id, idx, total, bool(flags & _RELIABLE), chunk)
        if done is None:
            return
        data, reliable = done
        if reliable:
            self._emit([b"A" + _ACK.pack(msg_id)])
        self._open(data)

    def _reassemble(self, now, msg_id, idx, total, reliable, chunk) -> tuple[bytes, bool] | None:
        if total == 0 or idx >= total or total * self._fragment_size > self._max_message + self._fragment_size:
            return None
        if len(chunk) > self._fragment_size:
            return None
        with self._lock:
            r = self._reassembly.get(msg_id)
            if r is None:
                if len(self._reassembly) >= self._max_reassemblies:
                    oldest = min(self._reassembly, key=lambda m: self._reassembly[m].started)
                    del self._reassembly[oldest]
                r = self._reassembly[msg_id] = _Reassembly(total, reliable, now)
            if r.total != total or r.reliable != reliable or idx in r.parts:
                return None
            if r.size + len(chunk) > self._max_message:
                del self._reassembly[msg_id]
                return None
            r.parts[idx] = chunk
            r.size += len(chunk)
            if len(r.parts) < r.total:
                return None
            del self._reassembly[msg_id]
            return b"".join(r.parts[i] for i in range(r.total)), r.reliable

    def _open(self, data: bytes) -> None:
        try:
            obj = json.loads(data)
        except (UnicodeDecodeError, ValueError):
            return
        opened = open_envelope(obj, trust=self._trust, self_did=self.identity.did, replay=self._replay)
        if not opened.ok:
            log.debug("dropped a packet from %s: %s", self.peer_did, opened.reason)
            return
        env = opened.envelope
        if env.signer != self.peer_did:
            return  # a trusted key, but not this session's peer
        if env.kind is PacketKind.SESSION and env.packet.event is SessionEvent.HEARTBEAT:
            return  # liveness only
        cb = self.on_envelope
        if cb is not None:
            cb(env)

    # -- time ------------------------------------------------------------------

    def tick(self, now: float | None = None) -> None:
        """Retransmit, give up, expire reassemblies, heartbeat, judge liveness."""
        t = float(now) if now is not None else float(self._clock())
        resend: list[bytes] = []
        failed: list[tuple[int, object]] = []
        with self._lock:
            for msg_id, o in list(self._outstanding.items()):
                if t < o.next_at:
                    continue
                if o.attempts >= self._retry_max:
                    del self._outstanding[msg_id]
                    failed.append((msg_id, o.packet))
                    continue
                o.attempts += 1
                o.next_at = t + self._retry_base * (2 ** (o.attempts - 1))
                resend.extend(o.frames)
            for msg_id in [m for m, r in self._reassembly.items() if t - r.started > self._reassembly_timeout]:
                del self._reassembly[msg_id]
            heartbeat = t >= self._next_heartbeat
            if heartbeat:
                self._next_heartbeat = t + self._heartbeat_interval
            went_down = self._alive and t - self._last_heard > self._dead_after * self._heartbeat_interval
            if went_down:
                self._alive = False
        self._emit(resend)
        if heartbeat:
            self.send(SessionPacket(SessionEvent.HEARTBEAT))
        for msg_id, packet in failed:
            cb = self.on_undeliverable
            if cb is not None:
                cb(msg_id, packet)
        if went_down and self.on_peer_down is not None:
            self.on_peer_down()

    def _heard(self, now: float) -> None:
        came_back = False
        with self._lock:
            self._last_heard = now
            if not self._alive:
                self._alive = True
                came_back = True
        if came_back and self.on_peer_up is not None:
            self.on_peer_up()

    def start(self, interval: float = 0.1) -> threading.Event:
        """Run ``tick`` on a daemon thread every ``interval`` s until stopped."""
        stop = threading.Event()
        self._stop = stop

        def run() -> None:
            while not stop.wait(interval):
                try:
                    self.tick()
                except Exception:  # noqa: BLE001 - keep ticking; a callback bug must not end the session
                    log.exception("session tick failed")

        threading.Thread(target=run, daemon=True, name="dialogue-session").start()
        return stop

    def close(self) -> None:
        """Say goodbye (best effort) and stop ticking."""
        try:
            self.send(SessionPacket(SessionEvent.BYE), reliable=False)
        except Exception:  # noqa: BLE001 - closing must not fail
            pass
        if self._stop is not None:
            self._stop.set()

    def _emit(self, frames: list[bytes]) -> None:
        for f in frames:
            try:
                self._send_bytes(f)
            except Exception:  # noqa: BLE001 - a failed send is a lost datagram: retransmission covers it
                log.debug("send failed", exc_info=True)


class SessionRouter:
    """Binds dialogue sessions to one ``ChannelMux`` channel: frames are routed
    by the transport-verified sender DID. A frame from a DID with no session
    goes to ``accept(did)``, which may create one (the node side, when an App
    says hello) or return None to ignore it."""

    def __init__(self, mux, *, channel: str = CHANNEL, accept: Callable[[str], DialogueSession | None] | None = None):
        self._mux = mux
        self._channel = channel
        self._accept = accept
        self._sessions: dict[str, DialogueSession] = {}
        self._lock = threading.Lock()
        mux.channel(channel, self._on_frame)

    def sender_for(self, peer_did: str) -> Callable[[bytes], bool]:
        return lambda data: self._mux.send(peer_did, self._channel, data)

    def add(self, session: DialogueSession) -> DialogueSession:
        with self._lock:
            self._sessions[session.peer_did] = session
        return session

    def remove(self, peer_did: str) -> None:
        with self._lock:
            self._sessions.pop(peer_did, None)

    def get(self, peer_did: str) -> DialogueSession | None:
        with self._lock:
            return self._sessions.get(peer_did)

    def sessions(self) -> list[DialogueSession]:
        with self._lock:
            return list(self._sessions.values())

    def _on_frame(self, from_did: str, data: bytes) -> None:
        session = self.get(from_did)
        if session is None and self._accept is not None:
            session = self._accept(from_did)
            if session is not None:
                self.add(session)
        if session is not None:
            session.receive(data)


__all__ = [
    "CHANNEL",
    "encode_envelope",
    "DialogueSession",
    "SessionRouter",
    "fragments",
    "default_reliable",
]
