"""secdogie-dialogue: the operator's Dialogue & Alignment App.

The App is where an operator and an Agent node align on intent (Socratic probes
and clarifications), watch the same structural view of the screen (AX / UIA tree
plus DIB metadata -- never pixels), and where a destructive action gets its
Gate 2 operator signature. It is a consumer, a signer and a dialogue peer: it
never captures the screen, never reads process memory, and never talks to a
central server. See DESIGN.zh.md.
"""
from __future__ import annotations

from .protocol import (
    PROTOCOL_VERSION,
    DialoguePacket,
    DialogueType,
    DibRef,
    Envelope,
    Gate2ChallengePacket,
    Gate2ResponsePacket,
    Header,
    NodeDelta,
    NodeOp,
    Opened,
    PacketKind,
    ProtocolError,
    ReplayGuard,
    RiskLevel,
    Sender,
    SessionEvent,
    SessionPacket,
    StateSnapshotPacket,
    TargetAction,
    Verdict,
    open_envelope,
    seal,
)

__version__ = "0.1.0"

__all__ = [
    "PROTOCOL_VERSION",
    "DialoguePacket",
    "DialogueType",
    "DibRef",
    "Envelope",
    "Gate2ChallengePacket",
    "Gate2ResponsePacket",
    "Header",
    "NodeDelta",
    "NodeOp",
    "Opened",
    "PacketKind",
    "ProtocolError",
    "ReplayGuard",
    "RiskLevel",
    "Sender",
    "SessionEvent",
    "SessionPacket",
    "StateSnapshotPacket",
    "TargetAction",
    "Verdict",
    "open_envelope",
    "seal",
]
