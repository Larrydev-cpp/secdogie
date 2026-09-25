"""secdogie-node: a running node of the mesh -- the DID-authenticated UDP
transport, membership gossip and signed-journal replication, assembled into one
process. It syncs state; it does not execute goals."""
from __future__ import annotations

from .config import NodeConfig, NodeConfigError, load_config
from .node import BINDING_KIND, Node, NodeStatus, offline_status

__version__ = "0.1.0"

__all__ = ["Node", "NodeConfig", "NodeConfigError", "NodeStatus", "BINDING_KIND", "load_config",
           "offline_status"]
