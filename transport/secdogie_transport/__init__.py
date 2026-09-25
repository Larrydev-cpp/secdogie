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
from .freshness import DEFAULT_MAX_SKEW, is_fresh
from .membership import MembershipView, PeerRecord, gossip_round
from .node import MeshNode
from .peer import PeerIdentity
from .rendezvous import RendezvousClient, RendezvousServer
from .sealed import ReplayWindow, load_transport_key
from .session import PATH_DIRECT, PATH_RELAY, Session
from .transport import HubTransport, Transport
from .udp import DirectUDPTransport, UDPChannel
from .upgrade import DirectUpgrader, UpgradeState

__version__ = "0.9.0"

__all__ = [
    "PeerIdentity",
    "Endpoint",
    "EndpointSet",
    "Session",
    "PATH_RELAY",
    "PATH_DIRECT",
    "DEFAULT_MAX_SKEW",
    "is_fresh",
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
    "MeshNode",
    "MembershipView",
    "PeerRecord",
    "gossip_round",
    "RoutingTable",
    "find_node",
    "find_peer",
    "node_id",
    "xor_distance",
]
