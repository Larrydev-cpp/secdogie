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

Replies are split into size-bounded EVENTS messages (``sync.events_messages``) so
they fit a datagram; a chunk that is lost or reordered is simply re-sent by the
next round (``initiate`` again), so convergence is eventual over rounds. A send
that fails is logged, never swallowed silently, and does not stop the other
chunks.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from . import sync

log = logging.getLogger("secdogie_citadel.replication")

SendFn = Callable[[str, dict], None]


def _have(journal: Any, *, reply: bool) -> dict:
    """A have-message tagged with whether it is itself a reply, so a HAVE cannot
    ping-pong forever."""
    return {**sync.have_message(journal), "reply": reply}


class ReplicationPeer:
    """One node's replication endpoint. Feed inbound messages to ``on_message``;
    it emits via the injected ``send(to_did, payload)``. The journal it wraps is
    the single source of truth -- merges go straight through ``journal.merge()``
    (self-verifying), so this class holds no security decisions of its own."""

    def __init__(self, journal: Any, send: SendFn, *, max_bytes: int = sync.DEFAULT_MAX_BYTES):
        self.journal = journal
        self._send_fn = send
        self.max_bytes = max_bytes
        self.send_failures = 0

    def _send(self, to_did: str, payload: dict) -> None:
        try:
            self._send_fn(to_did, payload)
        except Exception as exc:  # noqa: BLE001 -- report, keep going with the rest
            self.send_failures += 1
            log.warning("replication send to %s failed (%s): %s",
                        to_did, payload.get("kind"), exc)

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
            for msg in sync.respond_to_have_chunked(self.journal, payload, max_bytes=self.max_bytes):
                self._send(from_did, msg)
            if not payload.get("reply"):
                self._send(from_did, _have(self.journal, reply=True))
            return 0
        if kind == sync.EVENTS:
            return sync.apply_events_message(self.journal, payload)
        return 0


__all__ = ["ReplicationPeer", "SendFn"]
