"""Carry the journal's anti-entropy (``sync.py``) over a real transport, so signed
state converges across the mesh (Replication.1).

``sync.py`` builds the have/want messages; this drives them over an injected
``send(to_did, payload)`` callback -- wired to the DID-authenticated transport
(``DirectUDPTransport`` / ``HubTransport``) at the call site -- so two nodes'
journals, and thus their ``StateStore``s, converge. The exchange terminates: an
initiator offers its have-vector; the responder returns the events the initiator
lacks plus one counter-HAVE; the initiator returns the events the responder lacks
(no further HAVE). It is idempotent and order-independent because
``journal.merge()`` is.

Authenticity is doubly covered and needs nothing new here: the transport already
DID-signs and allowlist-gates every datagram, and ``journal.merge()``
independently verifies and allowlist-gates every event. So a relayed or replayed
event that isn't a genuine, authorized author's is dropped on merge. No new
crypto, no obfuscation, no detection-evasion. Transport-agnostic (only ``sync``
is imported), so citadel keeps no hard network dependency.

Batching. The events a peer lacks can be arbitrarily many, so with
``max_batch_bytes`` they are sent as several EVENTS messages of bounded size.
Each batch is independently mergeable (``events_since`` yields every author's
events in seq order, and ``merge`` sorts by (author, seq)); if one batch is lost,
later batches for that author are rejected as a gap and the next round resends
from the new heads -- so every round makes progress instead of retrying one huge
message forever.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from . import sync

SendFn = Callable[[str, dict], None]


def _have(journal: Any, *, reply: bool) -> dict:
    """A have-message tagged with whether it is itself a reply, so a HAVE cannot
    ping-pong forever."""
    return {**sync.have_message(journal), "reply": reply}


def _batches(events: list[dict], max_bytes: int | None) -> list[list[dict]]:
    """Split `events` (order kept) into lists whose JSON stays under `max_bytes`;
    a single event larger than that travels alone."""
    if not max_bytes:
        return [events]
    out: list[list[dict]] = []
    cur: list[dict] = []
    size = 0
    for e in events:
        n = len(json.dumps(e, separators=(",", ":"))) + 1
        if cur and size + n > max_bytes:
            out.append(cur)
            cur, size = [], 0
        cur.append(e)
        size += n
    if cur or not out:
        out.append(cur)
    return out


class ReplicationPeer:
    """One node's replication endpoint. Feed inbound messages to ``on_message``;
    it emits via the injected ``send(to_did, payload)``. The journal it wraps is
    the single source of truth -- merges go straight through ``journal.merge()``
    (self-verifying), so this class holds no security decisions of its own."""

    def __init__(self, journal: Any, send: SendFn, *, max_batch_bytes: int | None = None):
        self.journal = journal
        self._send = send
        self.max_batch_bytes = max_batch_bytes

    def initiate(self, to_did: str) -> None:
        """Start a sync with ``to_did`` by offering our have-vector."""
        self._send(to_did, _have(self.journal, reply=False))

    def on_message(self, from_did: str, payload: dict) -> int:
        """Handle one inbound replication message. Returns how many events were
        newly merged (0 for a HAVE, which only triggers replies)."""
        if not isinstance(payload, dict):
            return 0
        kind = payload.get("kind")
        if kind == sync.HAVE:
            # Send what they lack; and -- unless this HAVE is itself a reply --
            # one counter-HAVE so they send what we lack. That bounds the whole
            # thing to a two-round exchange that converges and then stops.
            heads = payload.get("heads")
            missing = sync.events_since(self.journal, heads if isinstance(heads, dict) else {})
            for batch in _batches(missing, self.max_batch_bytes):
                self._send(from_did, sync.events_message(batch))
            if not payload.get("reply"):
                self._send(from_did, _have(self.journal, reply=True))
            return 0
        if kind == sync.EVENTS:
            return sync.apply_events_message(self.journal, payload)
        return 0


def attach(node: Any, journal: Any, *, channel: str = "repl",
           max_batch_bytes: int | None = 16_000) -> ReplicationPeer:
    """Run replication over a mesh node (``secdogie_transport.node.MeshNode``, duck
    typed: ``send(peer_did, channel, body)`` + ``add_protocol(channel, on_message,
    on_sync=...)``). The node then syncs with every reachable peer each sync
    interval, over the direct path or the relay."""
    peer = ReplicationPeer(journal, lambda to, payload: node.send(to, channel, payload),
                           max_batch_bytes=max_batch_bytes)
    node.add_protocol(channel, peer.on_message, on_sync=peer.initiate)
    return peer


__all__ = ["ReplicationPeer", "SendFn", "attach"]
