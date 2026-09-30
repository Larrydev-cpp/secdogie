"""Membership gossip on the wire (T4): anti-entropy of signed membership records
between the mesh's nodes, over a `ChannelMux` channel.

`membership.gossip_round` already reconciles two views in one process; this
carries the same have/want exchange between nodes. Every round a node refreshes
its own record, picks one known peer at random and offers its digest
(``{did: last_seen}``); the peer answers with the records the offerer lacks and
-- unless the offer was itself a reply -- its own digest, so the offerer can
answer in turn. Two messages each way at most, then quiet: the same bounded
shape as the journal's replication.

It rides the node's application channel, so every message is already
DID-signed, allowlist-checked and replay-windowed by the transport; this layer
adds only that the sender must be one of the mesh ``peers``. Records themselves
are self-signed by the node they describe (``membership.verify_record``), so a
peer relaying another node's record cannot alter it. Records go out in batches
of at most ``max_bytes`` so each message fits a datagram. No new crypto, no
obfuscation: an authenticated directory of the operator's own nodes, converging.
"""
from __future__ import annotations

import json
import logging
import math
import random
import threading
import time
from collections.abc import Callable

from secdogie_identity import require_trust

from .membership import MembershipView, PeerRecord

log = logging.getLogger("secdogie_transport.gossip")

MEMBERSHIP_CHANNEL = "membership/v1"
DEFAULT_EVERY = 5.0        # seconds between rounds
DEFAULT_MAX_BYTES = 24_000  # records per message, so one fits a datagram (and a relay hop)
_MAX_DIGEST = 4096          # entries accepted in one offered digest


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


class MembershipGossip:
    """Gossip ``view`` with the mesh ``peers`` over ``mux``.

    ``self_record()``, when given, returns this node's freshly signed record;
    it is merged into the view at the start of every round, so the node's own
    reachability is always what it gossips. ``on_learn(record)`` is called with
    each record that changed the view (a new peer, or a peer's newer record)."""

    def __init__(self, mux, view: MembershipView, *, peers, self_record: Callable[[], dict] | None = None,
                 on_learn: Callable[[PeerRecord], None] | None = None, max_bytes: int = DEFAULT_MAX_BYTES,
                 rng: random.Random | None = None, clock=time.time):
        self.mux = mux
        self.view = view
        self._peers = require_trust(peers, "MembershipGossip")
        self._self_record = self_record
        self._on_learn = on_learn
        self._max_bytes = int(max_bytes)
        self._rng = rng or random.Random()
        self._clock = clock
        self._lock = threading.Lock()
        self._stop: threading.Event | None = None
        mux.channel(MEMBERSHIP_CHANNEL, self._on_message)

    # -- rounds ------------------------------------------------------------------

    def targets(self) -> list[str]:
        """The peers a round may pick: known, on the mesh allowlist, not us."""
        with self._lock:
            known = self.view.known()
        return [d for d in known if d != self.mux.local_did and self._peers.contains(d)]

    def tick(self) -> str | None:
        """One round: refresh our record, offer our digest to one random peer.
        Returns the peer picked, or None when there is none yet."""
        if self._self_record is not None:
            self._merge([self._self_record()])
        targets = self.targets()
        if not targets:
            return None
        peer = self._rng.choice(targets)
        self._offer(peer, reply=False)
        return peer

    def start(self, every: float = DEFAULT_EVERY) -> threading.Event:
        """Run a round now and every ``every`` seconds on a daemon thread."""
        stop = threading.Event()
        self._stop = stop

        def run() -> None:
            while True:
                try:
                    self.tick()
                except Exception:  # noqa: BLE001 - a failed round retries on the next tick
                    log.exception("membership gossip round failed")
                if stop.wait(every):
                    return

        threading.Thread(target=run, daemon=True, name="membership-gossip").start()
        return stop

    def close(self) -> None:
        if self._stop is not None:
            self._stop.set()
        self.mux.channel(MEMBERSHIP_CHANNEL, None)

    # -- messages ------------------------------------------------------------------

    def _send(self, to_did: str, msg: dict) -> bool:
        return self.mux.send(to_did, MEMBERSHIP_CHANNEL, json.dumps(msg, separators=(",", ":")).encode())

    def _offer(self, to_did: str, *, reply: bool) -> None:
        with self._lock:
            digest = self.view.digest()
        self._send(to_did, {"kind": "digest", "digest": digest, "reply": reply})

    def _on_message(self, from_did: str, payload: bytes) -> None:
        if not self._peers.contains(from_did):
            return  # only the mesh gossips (an App on the same transport does not)
        try:
            msg = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(msg, dict):
            return
        kind = msg.get("kind")
        if kind == "digest":
            digest = msg.get("digest")
            if not isinstance(digest, dict) or len(digest) > _MAX_DIGEST:
                return
            theirs = {d: float(t) for d, t in digest.items() if isinstance(d, str) and _num(t)}
            with self._lock:
                records = self.view.records_for(theirs)
            self._send_records(from_did, records)
            if not msg.get("reply"):
                self._offer(from_did, reply=True)
        elif kind == "records":
            records = msg.get("records")
            if isinstance(records, list):
                self._merge(records)

    def _send_records(self, to_did: str, records: list[dict]) -> None:
        batch: list[dict] = []
        size = 0
        for rec in records:
            cost = len(json.dumps(rec, separators=(",", ":"))) + 1
            if batch and size + cost > self._max_bytes:
                self._send(to_did, {"kind": "records", "records": batch})
                batch, size = [], 0
            batch.append(rec)
            size += cost
        if batch:
            self._send(to_did, {"kind": "records", "records": batch})

    def _merge(self, records: list) -> None:
        now = self._clock()
        learned = []
        with self._lock:
            for obj in records:
                if self.view.merge_record(obj, now=now):
                    rec = self.view.get(obj.get("did")) if isinstance(obj, dict) else None
                    if rec is not None:
                        learned.append(rec)
        if self._on_learn is not None:
            for rec in learned:
                try:
                    self._on_learn(rec)
                except Exception:  # noqa: BLE001 - the caller's hook must not break gossip
                    log.exception("on_learn failed for %s", rec.did)


__all__ = ["MembershipGossip", "MEMBERSHIP_CHANNEL", "DEFAULT_EVERY", "DEFAULT_MAX_BYTES"]
