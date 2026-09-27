"""Spread Master-signed revocations across the mesh (R1.2b).

A revocation record (identity/revocation.py) is self-authenticating: it carries
the master signatures, so any node can forward it and a receiver trusts the
signatures, not the forwarder. This wires that record onto the existing UDP
channel via ``DirectUDPTransport.on_frame`` and floods it with de-duplication,
so a revocation reaches every node within a few hops.

It holds no security decisions of its own: applying a record goes through
``TrustPolicy.apply`` (which verifies it against the master set), and a record
that revokes nothing new is not re-flooded. There is no new crypto and no
obfuscation; this is the same "gossip a signed fact" pattern as membership
records. A richer store-and-forward path (revocations carried in the journal so
a node that was offline still catches up) is future work; this is the fast
channel for nodes that are online.
"""
from __future__ import annotations

import json

from .membership import MembershipView
from .udp import DirectUDPTransport

REVOCATION_GOSSIP = "secdogie/revocation/gossip/v1"


class RevocationGossip:
    """Floods revocation records over a node's transport, applying each to the
    node's ``TrustPolicy``.

    ``policy`` is the node's ``TrustPolicy`` (``apply`` verifies + merges);
    ``membership`` supplies the peers to flood to, or ``None`` for a node that
    only receives and applies (a leaf, or a relay) without re-flooding. Construct
    it once per node."""

    def __init__(self, transport: DirectUDPTransport, policy, membership: MembershipView | None = None):
        self.transport = transport
        self.policy = policy
        self._membership = membership
        transport.on_frame(REVOCATION_GOSSIP, self._on_frame)

    def close(self) -> None:
        self.transport.on_frame(REVOCATION_GOSSIP, None)

    def broadcast(self, record: dict) -> None:
        """Send one revocation record to every peer we have an endpoint for.
        A no-op for an apply-only node (no membership view)."""
        if self._membership is None:
            return
        frame = json.dumps({"t": REVOCATION_GOSSIP, "record": record}).encode("utf-8")
        for did in self._membership.known():
            if did == self.transport.identity.did:
                continue
            endpoints = self._membership.endpoints_for(did)
            best = endpoints.best() if endpoints is not None else None
            if best is not None:
                self.transport.channel.send(best.host, best.port, frame)

    def announce(self, record: dict) -> frozenset:
        """Apply a record locally and, if it revoked anything new, flood it.
        Returns the newly revoked DIDs. Use this to originate a revocation."""
        newly = self.policy.apply(record)
        if newly:
            self.broadcast(record)
        return newly

    def _on_frame(self, obj: dict, addr: tuple) -> None:
        record = obj.get("record")
        if not isinstance(record, dict):
            return
        # apply() verifies against the master set and de-duplicates by record_id;
        # it returns the DIDs this record newly revoked (empty for a forgery, a
        # record under threshold, or one already seen). Re-flood only then, so a
        # record loops the mesh once, not forever.
        if self.policy.apply(record):
            self.broadcast(record)


__all__ = ["REVOCATION_GOSSIP", "RevocationGossip"]
