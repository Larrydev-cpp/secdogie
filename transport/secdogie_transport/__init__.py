"""secdogie-transport: a P2P-ready peer/session/endpoint model.

Upper layers depend on an authenticated peer *session* (a DID bound to a
transport key, reachable at migratable endpoints), not on a hub socket. Today's
only implementation is an in-memory HubTransport (the current hub-and-spoke
topology, kept as fallback/rendezvous/relay); a DirectUDPTransport (Phase 2.10)
implements the same interface for true peer-to-peer.
"""
from __future__ import annotations

from .dht import RoutingTable, find_node, find_peer, node_id, xor_distance
from .endpoint import Endpoint, EndpointSet
from .membership import MembershipView, PeerRecord, gossip_round
from .peer import PeerIdentity
from .rendezvous import RendezvousClient, RendezvousServer
from .sealed import ReplayWindow, load_transport_key
from .session import Session
from .transport import HubTransport, Transport
from .udp import DirectUDPTransport, UDPChannel
from .upgrade import DirectUpgrader, UpgradeState

__version__ = "0.8.0"

__all__ = [
    "PeerIdentity",
    "Endpoint",
    "EndpointSet",
    "Session",
    "Transport",
    "HubTransport",
    "DirectUDPTransport",
    "UDPChannel",
    "ReplayWindow",
    "load_transport_key",
    "RendezvousServer",
    "RendezvousClient",
    "DirectUpgrader",
    "UpgradeState",
    "MembershipView",
    "PeerRecord",
    "gossip_round",
    "RoutingTable",
    "find_node",
    "find_peer",
    "node_id",
    "xor_distance",
]
