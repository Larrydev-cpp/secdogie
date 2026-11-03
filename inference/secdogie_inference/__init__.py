"""secdogie-inference: the inference mesh -- the "new arc".

Users' signed requests travel over the P2P mesh to model nodes that hold short
key leases from a key authority; the answers come back signed end to end and
are judged by a deterministic safety layer at the decider *and* at the user.

  * normal   -- serving nodes (the decision layer) route over confirmed links;
  * degraded -- failed and misbehaving nodes are routed around, the
    coordinator advises repairs and carries the central fallback;
  * lockdown -- self-verifying mutiny evidence latches the culprit, suspends
    the decision layer and the central path, rotates exposed keys, and users
    pin two independent model nodes through a standby forwarder until an
    operator signs a reset.

Protocol v2 makes the mode an explicit state machine (state.py), every request
a bounded session (session.py), heartbeats a Merkle DAG (forks isolate a node,
links must be witnessed, ledgers converge), and puts a history-aware decision
gate (gate.py) in front of the decision layer.

See README.md for the architecture and PROTOCOL.md for the wire.
"""
from __future__ import annotations

from .breaker import CircuitBreaker
from .broker import Assignment, KeyBroker, assignments_from_bindings
from .client import OUTCOME_ACCEPTED, OUTCOME_INTERCEPTED, OUTCOME_REFUSED, Outcome, UserClient
from .coordinator import Coordinator
from .gate import DecisionGate, GateVerdict, History
from .health import FaultTracker, Suspicion
from .lease import KeySource, LeaseBook, MemoryKeySource
from .mutiny import KINDS, MutinyLedger, make_incident, make_reset, verify_incident
from .node import MeshNode
from .participant import Config, Participant
from .protocol import (
    CHANNEL,
    MODE_DEGRADED,
    MODE_LOCKDOWN,
    MODE_NORMAL,
    ROLE_BROKER,
    ROLE_COORDINATOR,
    ROLE_FORWARD,
    ROLE_MODEL,
    ROLE_SERVE,
    TrustRoots,
    make_request,
)
from .router import Leg, NoRoute, Router, View
from .safety import SafetyLayer, Verdict, default_agree
from .session import BoundedCache, Session, SessionTable
from .state import IllegalTransition, ModeInputs, ModeMachine, NodeHealth, Transition
from .wire import MuxWire, Wire

__version__ = "0.2.0"

__all__ = [
    "TrustRoots",
    "Config",
    "Participant",
    "MeshNode",
    "Coordinator",
    "KeyBroker",
    "Assignment",
    "assignments_from_bindings",
    "UserClient",
    "Outcome",
    "OUTCOME_ACCEPTED",
    "OUTCOME_INTERCEPTED",
    "OUTCOME_REFUSED",
    "Router",
    "View",
    "Leg",
    "NoRoute",
    "SafetyLayer",
    "Verdict",
    "DecisionGate",
    "GateVerdict",
    "History",
    "Session",
    "SessionTable",
    "BoundedCache",
    "default_agree",
    "MutinyLedger",
    "KINDS",
    "make_incident",
    "make_reset",
    "verify_incident",
    "LeaseBook",
    "KeySource",
    "MemoryKeySource",
    "CircuitBreaker",
    "Suspicion",
    "FaultTracker",
    "ModeMachine",
    "ModeInputs",
    "Transition",
    "IllegalTransition",
    "NodeHealth",
    "Wire",
    "MuxWire",
    "make_request",
    "CHANNEL",
    "MODE_NORMAL",
    "MODE_DEGRADED",
    "MODE_LOCKDOWN",
    "ROLE_SERVE",
    "ROLE_FORWARD",
    "ROLE_MODEL",
    "ROLE_COORDINATOR",
    "ROLE_BROKER",
]
