"""Channel multiplexing + fragmentation over one peer transport (P2P mesh, 2B).

A `DirectUDPTransport` (or a relay) delivers one opaque byte string per datagram
to one handler. The mesh node runs several protocols over that single pipe --
membership gossip, journal replication, and whatever an upper layer adds -- so
each message is wrapped in a small envelope naming its channel:

    {"t": "secdogie/mux/v1", "ch": <channel>, "body": <JSON value>}

Upgrade traffic (PROBE / PROBE-ACK / CONNECT, upgrade.py) is NOT wrapped: it
keeps its own `t`, so the upgrader's wire format is unchanged.

Fragmentation. A datagram that crosses the internet should stay under the path
MTU (~1400 bytes is the usual safe size) or it is split by IP and silently lost
far more often. A message whose envelope is longer than `max_message` is cut into
pieces of the envelope's JSON text (cut by escaped length, so a piece full of
quotes still fits):

    {"t": "secdogie/mux/frag/v1", "id": <random>, "i": <index>, "n": <count>, "d": <text>}

`encode` emits ASCII-only JSON, so slicing the text is slicing bytes. With the
defaults, every direct frame (signed v1 or sealed v2) stays under ~1400 bytes.

Reassembly is bounded, because a buffer an allowlisted-but-buggy peer can grow
without limit is still a denial of service: at most `max_fragments` pieces per
message, `max_pending` incomplete messages per peer, and incomplete messages are
dropped after `timeout` seconds. A lost piece loses that one message; protocols
on top are anti-entropy (re-sent on the next round), so nothing needs
retransmission here.

Authenticity is not this layer's job: every datagram is already a DID-signed,
timestamped, replay-checked frame by the time it reaches `Reassembler.feed`, and
reassembly is keyed by the authenticated sender DID, so one peer cannot inject
pieces into another peer's message.
"""
from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, field

MUX_TYPE = "secdogie/mux/v1"
FRAG_TYPE = "secdogie/mux/frag/v1"

# Envelope text above this length is fragmented; pieces carry this much text.
# Chosen so a signed v1 or sealed v2 frame carrying one piece stays < 1400 bytes.
DEFAULT_MAX_MESSAGE = 560
DEFAULT_FRAGMENT_SIZE = 480
DEFAULT_MAX_FRAGMENTS = 8192
DEFAULT_MAX_PENDING = 32
DEFAULT_TIMEOUT = 30.0


def _dumps(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=True)


def _split(text: str, budget: int) -> list[str]:
    """Cut ASCII JSON text into pieces whose JSON-escaped length is <= `budget`.
    Inside a JSON string `"` and `\\` cost two characters; everything else in
    ASCII-only JSON text costs one."""
    out: list[str] = []
    i = 0
    while i < len(text):
        n = budget
        while True:
            piece = text[i:i + n]
            cost = len(piece) + piece.count('"') + piece.count("\\")
            if cost <= budget or n == 1:
                break
            n = max(1, min(n - 1, n * budget // cost))
        out.append(piece)
        i += len(piece)
    return out


def encode(ch: str, body, *, max_message: int = DEFAULT_MAX_MESSAGE,
           fragment_size: int = DEFAULT_FRAGMENT_SIZE) -> list[bytes]:
    """The datagrams that carry `body` on channel `ch`: one when it fits in
    `max_message`, else several fragments."""
    text = _dumps({"t": MUX_TYPE, "ch": ch, "body": body})
    if len(text) <= max_message:
        return [text.encode("ascii")]
    pieces = _split(text, fragment_size)
    msg_id = secrets.token_hex(8)
    return [
        _dumps({"t": FRAG_TYPE, "id": msg_id, "i": i, "n": len(pieces), "d": piece}).encode("ascii")
        for i, piece in enumerate(pieces)
    ]


def _parse(data: bytes) -> dict | None:
    try:
        obj = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return None
    if isinstance(obj, dict) and obj.get("t") in (MUX_TYPE, FRAG_TYPE):
        return obj
    return None


def is_mux(data: bytes) -> bool:
    return _parse(data) is not None


def _unwrap(obj: dict) -> tuple[str, object] | None:
    ch = obj.get("ch")
    if obj.get("t") != MUX_TYPE or not isinstance(ch, str) or "body" not in obj:
        return None
    return ch, obj["body"]


@dataclass
class _Partial:
    n: int
    started: float
    pieces: dict[int, str] = field(default_factory=dict)


class Reassembler:
    """Turns inbound datagrams from authenticated peers back into (channel, body)
    messages. Bounded per peer (see the module docstring)."""

    def __init__(self, *, max_fragments: int = DEFAULT_MAX_FRAGMENTS,
                 max_pending: int = DEFAULT_MAX_PENDING, timeout: float = DEFAULT_TIMEOUT,
                 clock=time.time):
        self.max_fragments = max_fragments
        self.max_pending = max_pending
        self.timeout = timeout
        self._clock = clock
        self._partial: dict[str, dict[str, _Partial]] = {}  # peer DID -> msg id -> pieces

    def feed(self, from_did: str, data: bytes) -> tuple[str, object] | None:
        """Process one datagram. Returns (channel, body) when a message is
        complete, else None (not a mux datagram, malformed, or still partial)."""
        obj = _parse(data)
        if obj is None:
            return None
        if obj["t"] == MUX_TYPE:
            return _unwrap(obj)
        return self._feed_fragment(from_did, obj)

    def _feed_fragment(self, from_did: str, obj: dict) -> tuple[str, object] | None:
        msg_id, i, n, piece = obj.get("id"), obj.get("i"), obj.get("n"), obj.get("d")
        if not (isinstance(msg_id, str) and isinstance(piece, str)
                and isinstance(i, int) and isinstance(n, int)
                and not isinstance(i, bool) and not isinstance(n, bool)
                and 0 <= i < n <= self.max_fragments):
            return None
        now = self._clock()
        pending = self._partial.setdefault(from_did, {})
        part = pending.get(msg_id)
        if part is None:
            if len(pending) >= self.max_pending:
                oldest = min(pending, key=lambda k: pending[k].started)
                del pending[oldest]  # make room: the oldest incomplete message loses
            part = pending[msg_id] = _Partial(n=n, started=now)
        if part.n != n:
            del pending[msg_id]  # inconsistent piece count: drop the whole message
            return None
        part.pieces[i] = piece
        if len(part.pieces) < n:
            return None
        del pending[msg_id]
        inner = _parse("".join(part.pieces[k] for k in range(n)).encode("ascii", "replace"))
        return _unwrap(inner) if inner is not None else None

    def expire(self, now: float | None = None) -> int:
        """Drop incomplete messages older than `timeout`. Returns how many."""
        t = self._clock() if now is None else now
        dropped = 0
        for did in list(self._partial):
            pending = self._partial[did]
            for msg_id in [m for m, p in pending.items() if t - p.started > self.timeout]:
                del pending[msg_id]
                dropped += 1
            if not pending:
                del self._partial[did]
        return dropped

    def pending(self, from_did: str | None = None) -> int:
        if from_did is not None:
            return len(self._partial.get(from_did, {}))
        return sum(len(p) for p in self._partial.values())


__all__ = [
    "MUX_TYPE",
    "FRAG_TYPE",
    "DEFAULT_MAX_MESSAGE",
    "DEFAULT_FRAGMENT_SIZE",
    "encode",
    "is_mux",
    "Reassembler",
]
