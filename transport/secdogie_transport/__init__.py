"""secdogie-transport: a P2P-ready peer/session/endpoint model.

Upper layers depend on an authenticated peer *session* (a DID bound to a
transport key, reachable at migratable endpoints), not on a hub socket. Today's
only implementation is an in-memory HubTransport (the current hub-and-spoke
topology, kept as fallback/rendezvous/relay); a DirectUDPTransport (Phase 2.10)
implements the same interface for true peer-to-peer.
"""
from __future__ import annotations

from .endpoint import Endpoint, EndpointSet
from .peer import PeerIdentity
from .session import Session
from .transport import HubTransport, Transport
from .udp import DirectUDPTransport, UDPChannel

__version__ = "0.5.0"

__all__ = [
    "PeerIdentity",
    "Endpoint",
    "EndpointSet",
    "Session",
    "Transport",
    "HubTransport",
    "DirectUDPTransport",
    "UDPChannel",
]
