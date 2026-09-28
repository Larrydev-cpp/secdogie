"""The dialogue protocol (v1): the wire between an operator's Dialogue App and an
Agent node.

Every packet travels inside one signed envelope::

    {"header": {...}, "kind": "...", "payload": {...}, "signer": did, "sig": b64}

``signer`` / ``sig`` come from ``secdogie_identity.sign_payload`` over the
canonical bytes of ``{header, kind, payload}``. No new crypto.

The receiving side, ``open_envelope``, authenticates **before** it looks inside:
the signature must verify and the signer must be on the trust policy (an
allowlist or a ``TrustPolicy``, so a revoked DID is refused exactly like an
unknown one). Only then are the header and payload parsed -- strictly: every
field is required, unknown fields are refused, types are exact (a bool is not an
int, a non-finite float is not a time). So an unauthenticated peer can never make
this side run its parser, and a trusted one can never smuggle an extra field
(pixels, say) past the schema: the structural view is structural by construction.

Freshness and replay: each header names its sender, its recipient, a session id,
a sequence number and a send time. A packet is admitted only if it is addressed
to this node, its time is within ``max_skew`` of our clock, and its sequence
number has not been admitted before for that (sender, session) and is not too
far behind the newest (a sliding window: UDP reorders, and a retransmission may
arrive after newer packets). A packet that fails any check is dropped; nothing
is sent back.

Pure and deterministic given a clock; unit-tested headless.
"""
from __future__ import annotations

import math
import secrets
import threading
import time
import typing
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum

from secdogie_identity import sign_payload, verify_payload

PROTOCOL_VERSION = "secdogie/dialogue/v1"

DEFAULT_MAX_SKEW_NS = 30 * 1_000_000_000  # 30 s either way
DEFAULT_MAX_SESSIONS = 1024
DEFAULT_REPLAY_WINDOW = 256  # sequence numbers a packet may arrive behind the newest

_ENVELOPE_KEYS = frozenset({"header", "kind", "payload"})


class ProtocolError(ValueError):
    """A packet that does not match the v1 schema."""


# ---- envelope ---------------------------------------------------------------


@dataclass(frozen=True)
class Header:
    version: str
    sender_did: str
    recipient_did: str  # binds the packet to one node: it cannot be re-aimed at another
    session_id: str
    seq: int  # strictly increasing per (sender, session)
    timestamp_ns: int  # sender's clock; checked against ours within max_skew

    def __post_init__(self):
        if self.seq < 0 or self.timestamp_ns < 0:
            raise ProtocolError("seq and timestamp_ns must be non-negative")


class PacketKind(str, Enum):
    DIALOGUE = "dialogue"
    STATE_SNAPSHOT = "state_snapshot"
    GATE2_CHALLENGE = "gate2_challenge"
    GATE2_RESPONSE = "gate2_response"
    SESSION = "session"
    CONTROL = "control"


# ---- (1) Socratic dialogue --------------------------------------------------


class DialogueType(str, Enum):
    SOCRATIC_QUESTION = "SocraticQuestion"  # Agent -> operator: a probe
    USER_CLARIFICATION = "UserClarification"  # operator -> Agent: the answer to one probe
    SYSTEM_STATUS = "SystemStatus"  # Agent -> operator: status


@dataclass(frozen=True)
class DialoguePacket:
    probe_id: str
    dialogue_type: DialogueType
    content: str
    in_reply_to: str = ""  # a clarification names the probe it answers
    suggested_options: tuple[str, ...] = ()
    gate_finding: str = ""  # the action_gate finding that raised the probe, if any

    def __post_init__(self):
        if self.dialogue_type is DialogueType.USER_CLARIFICATION and not self.in_reply_to:
            raise ProtocolError("a clarification must name the probe it answers (in_reply_to)")
        if self.dialogue_type is DialogueType.SOCRATIC_QUESTION and not self.probe_id:
            raise ProtocolError("a probe needs a probe_id")


# ---- (2) structural view (incremental) --------------------------------------


class NodeOp(str, Enum):
    ADD = "add"
    UPDATE = "update"
    REMOVE = "remove"


@dataclass(frozen=True)
class NodeDelta:
    """One change to the structural tree. ``index`` is the node's stable handle in
    this window's stream (assigned by the Agent, kept across snapshots); every
    cross-reference -- ``parent_index``, a snapshot's ``focused_node_index``, a
    ``DibRef.node_index`` -- names a handle. ``automation_id`` / ``path_index``
    describe where the node came from; they are not keys."""

    op: NodeOp
    index: int
    automation_id: str = ""
    path_index: tuple[int, ...] = ()
    role: str = ""
    name: str = ""
    bounds: tuple[int, int, int, int] = (0, 0, 0, 0)  # x, y, w, h
    enabled: bool = True
    is_interactive: bool = False
    parent_index: int = -1  # -1 = a root

    def __post_init__(self):
        if self.index < 0:
            raise ProtocolError("node index must be non-negative")
        if self.parent_index < -1 or self.parent_index == self.index:
            raise ProtocolError("parent_index must be -1 or another node's index")


@dataclass(frozen=True)
class DibRef:
    """Metadata about a DIB-covered node. Never pixels: the App shows the shape and
    the hash, so it can tell that the region changed, not what it looks like."""

    node_index: int
    width: int
    height: int
    pixel_format: str
    content_hash: str

    def __post_init__(self):
        if self.node_index < 0 or self.width < 0 or self.height < 0:
            raise ProtocolError("DibRef index and size must be non-negative")


@dataclass(frozen=True)
class StateSnapshotPacket:
    window_id: int
    app_pid: int
    generation: int  # observation generation; older-or-equal snapshots are stale
    nodes: tuple[NodeDelta, ...]
    dib_references: tuple[DibRef, ...] = ()
    focused_node_index: int = -1  # the node the Agent is about to act on; -1 = none
    full: bool = False  # True = replace the whole tree (first frame, or a resync)
    # A delta is built on one earlier generation and applies only to exactly
    # that tree -- so a lost delta is detected (and resynced), never silently
    # skipped over. -1 for a full snapshot.
    base_generation: int = -1

    def __post_init__(self):
        if self.generation < 0:
            raise ProtocolError("generation must be non-negative")
        if self.full and any(d.op is NodeOp.REMOVE for d in self.nodes):
            raise ProtocolError("a full snapshot lists the tree; it cannot remove nodes")
        if self.full and self.base_generation != -1:
            raise ProtocolError("a full snapshot has no base generation")
        if not self.full and not 0 <= self.base_generation < self.generation:
            raise ProtocolError("a delta names the earlier generation it applies to")


# ---- (3) Gate 2 challenge / response ----------------------------------------


class RiskLevel(str, Enum):
    LOW = "low"
    HIGH = "high"
    IRREVERSIBLE = "irreversible"


@dataclass(frozen=True)
class TargetAction:
    """Exactly the fields ``secdogie_citadel.authz.action_hash`` commits to, with
    the same defaults as ``PlannedAction`` -- so the App recomputes the hash
    locally from what it is shown, byte for byte as the node does."""

    kind: str
    target_id: str = ""
    target_role: str = ""
    target_name: str = ""
    text: str = ""
    high_risk: bool = False

    @classmethod
    def from_action(cls, action) -> TargetAction:
        return cls(
            kind=action.kind,
            target_id=action.target_id,
            target_role=action.target_role,
            target_name=action.target_name,
            text=action.text,
            high_risk=bool(action.high_risk),
        )


@dataclass(frozen=True)
class Gate2ChallengePacket:
    challenge_id: str
    target_action: TargetAction
    risk_level: RiskLevel
    risk_explanation: str
    action_hash: str  # what the node claims; the App recomputes and compares
    subject_did: str  # the node the authorization would be for
    expires_at: float


class Verdict(str, Enum):
    APPROVE = "Approve"
    DENY = "Deny"


@dataclass(frozen=True)
class Gate2ResponsePacket:
    challenge_id: str
    action_hash: str  # the hash the App computed locally
    user_verdict: Verdict
    authorization: dict = field(default_factory=dict)  # the authz token on Approve

    def __post_init__(self):
        if self.user_verdict is Verdict.DENY and self.authorization:
            raise ProtocolError("a Deny carries no authorization")
        if self.user_verdict is Verdict.APPROVE and not self.authorization:
            raise ProtocolError("an Approve must carry the signed authorization")


# ---- (4) session control ----------------------------------------------------


class SessionEvent(str, Enum):
    HELLO = "hello"
    HEARTBEAT = "heartbeat"
    BYE = "bye"
    RESYNC = "resync"  # App -> Agent: send a full snapshot


@dataclass(frozen=True)
class SessionPacket:
    event: SessionEvent
    note: str = ""


# ---- (5) operator control ------------------------------------------------------


class ControlOp(str, Enum):
    ADD_GOAL = "add_goal"
    STOP = "stop"
    PAUSE = "pause"
    RESUME = "resume"
    CONFIRM_MEMORY = "confirm_memory"  # carries the signed memory-confirmation
    RETRACT_MEMORY = "retract_memory"


_GOAL_OPS = frozenset({ControlOp.ADD_GOAL, ControlOp.STOP, ControlOp.PAUSE, ControlOp.RESUME})


@dataclass(frozen=True)
class ControlPacket:
    """App -> node: one operator request. The node answers with a
    ``SystemStatus`` whose ``in_reply_to`` is ``request_id``."""

    request_id: str
    op: ControlOp
    goal_id: str = ""
    title: str = ""
    memory_id: str = ""
    confirmation: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.request_id:
            raise ProtocolError("a control request needs a request_id")
        if self.op in _GOAL_OPS and not self.goal_id:
            raise ProtocolError(f"{self.op.value} needs a goal_id")
        if self.op is ControlOp.ADD_GOAL and not self.title.strip():
            raise ProtocolError("add_goal needs a title (the task)")
        if self.op in (ControlOp.CONFIRM_MEMORY, ControlOp.RETRACT_MEMORY) and not self.memory_id:
            raise ProtocolError(f"{self.op.value} needs a memory_id")
        if (self.op is ControlOp.CONFIRM_MEMORY) != bool(self.confirmation):
            raise ProtocolError("only confirm_memory carries a confirmation, and it always does")


PACKET_TYPES: dict[PacketKind, type] = {
    PacketKind.DIALOGUE: DialoguePacket,
    PacketKind.STATE_SNAPSHOT: StateSnapshotPacket,
    PacketKind.GATE2_CHALLENGE: Gate2ChallengePacket,
    PacketKind.GATE2_RESPONSE: Gate2ResponsePacket,
    PacketKind.SESSION: SessionPacket,
    PacketKind.CONTROL: ControlPacket,
}
_KIND_OF = {cls: kind for kind, cls in PACKET_TYPES.items()}


def kind_of(packet) -> PacketKind:
    try:
        return _KIND_OF[type(packet)]
    except KeyError:
        raise ProtocolError(f"not a dialogue packet: {type(packet).__name__}") from None


# ---- wire encoding ----------------------------------------------------------


def to_wire(obj):
    """JSON-native form: enums by value, tuples as lists, dataclasses as dicts."""
    if isinstance(obj, Enum):
        return obj.value
    if is_dataclass(obj):
        return {f.name: to_wire(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, (tuple, list)):
        return [to_wire(x) for x in obj]
    return obj


def from_wire(kind: PacketKind, obj) -> object:
    """Parse ``obj`` as the packet type for ``kind``. Raises ``ProtocolError``."""
    return _parse(PACKET_TYPES[kind], obj, kind.value)


def _parse(cls, obj, where: str):
    if not isinstance(obj, dict):
        raise ProtocolError(f"{where}: expected an object")
    names = [f.name for f in fields(cls)]
    unknown = set(obj) - set(names)
    if unknown:
        raise ProtocolError(f"{where}: unknown field(s) {sorted(unknown)}")
    hints = typing.get_type_hints(cls)
    kwargs = {}
    for name in names:
        if name not in obj:  # every field is explicit on the wire; no defaults are implied
            raise ProtocolError(f"{where}: missing field {name!r}")
        kwargs[name] = _value(hints[name], obj[name], f"{where}.{name}")
    return cls(**kwargs)


def _value(tp, v, where: str):
    if tp is bool:
        if type(v) is not bool:
            raise ProtocolError(f"{where}: expected a bool")
        return v
    if tp is int:
        if type(v) is not int:  # excludes bool
            raise ProtocolError(f"{where}: expected an int")
        return v
    if tp is float:
        if type(v) not in (int, float) or not math.isfinite(v):
            raise ProtocolError(f"{where}: expected a finite number")
        return float(v)
    if tp is str:
        if not isinstance(v, str):
            raise ProtocolError(f"{where}: expected a string")
        return v
    if tp is dict:
        if not isinstance(v, dict):
            raise ProtocolError(f"{where}: expected an object")
        return v
    if isinstance(tp, type) and issubclass(tp, Enum):
        if not isinstance(v, str):
            raise ProtocolError(f"{where}: expected a string")
        try:
            return tp(v)
        except ValueError:
            raise ProtocolError(f"{where}: unknown value {v!r}") from None
    if is_dataclass(tp):
        return _parse(tp, v, where)
    if typing.get_origin(tp) is tuple:
        args = typing.get_args(tp)
        if not isinstance(v, list):
            raise ProtocolError(f"{where}: expected a list")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_value(args[0], x, f"{where}[{i}]") for i, x in enumerate(v))
        if len(v) != len(args):
            raise ProtocolError(f"{where}: expected {len(args)} items")
        return tuple(_value(a, x, f"{where}[{i}]") for i, (a, x) in enumerate(zip(args, v, strict=True)))
    raise ProtocolError(f"{where}: unsupported field type {tp!r}")


# ---- sealing ------------------------------------------------------------------


def seal(identity, header: Header, packet) -> dict:
    """Sign ``packet`` under ``header`` with ``identity``. The header must name
    this protocol version and the signing identity as its sender."""
    if header.version != PROTOCOL_VERSION:
        raise ValueError(f"header.version must be {PROTOCOL_VERSION!r}")
    if header.sender_did != identity.did:
        raise ValueError("header.sender_did must be the signing identity's DID")
    body = {"header": to_wire(header), "kind": kind_of(packet).value, "payload": to_wire(packet)}
    return sign_payload(identity, body)


class Sender:
    """One direction of one session: stamps each packet with the next sequence
    number and the current time, then seals it."""

    def __init__(self, identity, recipient_did: str, *, session_id: str | None = None,
                 clock_ns=time.time_ns):
        self._identity = identity
        self.recipient_did = recipient_did
        self.session_id = session_id or secrets.token_hex(16)
        self._clock_ns = clock_ns
        self._seq = 0
        self._lock = threading.Lock()

    def seal(self, packet) -> dict:
        with self._lock:
            self._seq += 1
            seq = self._seq
        header = Header(PROTOCOL_VERSION, self._identity.did, self.recipient_did,
                        self.session_id, seq, int(self._clock_ns()))
        return seal(self._identity, header, packet)


# ---- opening ------------------------------------------------------------------


class _SeqWindow:
    """The highest sequence number seen plus a bitmap of the ``size`` below it
    (the scheme the transport's frame counters use). Each number is admitted
    once; one at or beyond ``size`` behind the highest is too old. Out-of-order
    arrival inside the window is fine -- UDP reorders, and a retransmission may
    land after newer packets."""

    def __init__(self, size: int):
        self.size = size
        self.highest: int | None = None
        self._bits = 0  # bit i set -> (highest - i) seen

    def check(self, seq: int) -> str | None:
        if self.highest is None or seq > self.highest:
            return None
        diff = self.highest - seq
        if diff >= self.size:
            return "sequence number too old (outside the replay window)"
        if (self._bits >> diff) & 1:
            return "replayed sequence number"
        return None

    def commit(self, seq: int) -> None:
        if self.highest is None:
            self.highest, self._bits = seq, 1
        elif seq > self.highest:
            shift = seq - self.highest
            self._bits = ((self._bits << shift) | 1) & ((1 << self.size) - 1) if shift < self.size else 1
            self.highest = seq
        else:
            self._bits |= 1 << (self.highest - seq)


class ReplayGuard:
    """Admits a header only if its time is within ``max_skew_ns`` of our clock and
    its sequence number has not been admitted before for its (sender, session)
    and is not more than ``window`` behind the highest one admitted. One per
    receiving node.

    It remembers at most ``max_sessions`` sessions. When full it forgets only
    sessions idle for more than twice the skew window: every packet it admitted
    from such a session is by then outside the freshness window, so forgetting
    it cannot reopen a replay. If every remembered session is still live, a new
    session is refused (fail closed) rather than evicting a live one."""

    def __init__(self, *, max_skew_ns: int = DEFAULT_MAX_SKEW_NS,
                 max_sessions: int = DEFAULT_MAX_SESSIONS, window: int = DEFAULT_REPLAY_WINDOW,
                 clock_ns=time.time_ns):
        self._skew = int(max_skew_ns)
        self._max = int(max_sessions)
        self._window = int(window)
        self._clock_ns = clock_ns
        self._sessions: dict[tuple[str, str], tuple[_SeqWindow, int]] = {}  # key -> (window, last admit)
        self._lock = threading.Lock()

    def admit(self, header: Header) -> str | None:
        """Admit ``header`` and return None, or return why it is refused."""
        now = int(self._clock_ns())
        if abs(now - header.timestamp_ns) > self._skew:
            return "stale or future-dated packet (outside the clock-skew window)"
        key = (header.sender_did, header.session_id)
        with self._lock:
            entry = self._sessions.get(key)
            if entry is None:
                if len(self._sessions) >= self._max:
                    self._forget_idle(now)
                    if len(self._sessions) >= self._max:
                        return "too many live sessions"
                entry = (_SeqWindow(self._window), now)
            reason = entry[0].check(header.seq)
            if reason:
                return reason
            entry[0].commit(header.seq)
            self._sessions[key] = (entry[0], now)
        return None

    def _forget_idle(self, now: int) -> None:
        idle = [k for k, (_, seen) in self._sessions.items() if now - seen > 2 * self._skew]
        for k in idle:
            del self._sessions[k]


@dataclass(frozen=True)
class Envelope:
    header: Header
    kind: PacketKind
    packet: object
    signer: str


@dataclass(frozen=True)
class Opened:
    ok: bool
    reason: str | None = None
    envelope: Envelope | None = None
    signer: str | None = None


def open_envelope(obj, *, trust, self_did: str, replay: ReplayGuard) -> Opened:
    """Authenticate, then parse, then admit one received envelope.

    ``trust`` is required (an allowlist or a ``TrustPolicy``); without one nothing
    opens. On any failure returns ``Opened(ok=False, reason=...)`` -- the caller
    drops the packet silently."""
    if trust is None:
        return Opened(False, "no trust policy configured")
    if not isinstance(obj, dict):
        return Opened(False, "not an object")
    ok, signer = verify_payload(obj, trust)
    if not ok:
        reason = "sender not trusted or revoked" if signer else "invalid or missing signature"
        return Opened(False, reason, signer=signer)
    # Authenticated. Only from here on is anything inside the envelope parsed.
    if set(obj) - {"signer", "sig"} != _ENVELOPE_KEYS:
        return Opened(False, "not a dialogue envelope", signer=signer)
    try:
        header = _parse(Header, obj["header"], "header")
    except ProtocolError as e:
        return Opened(False, f"bad header: {e}", signer=signer)
    if header.version != PROTOCOL_VERSION:
        return Opened(False, "wrong protocol version (domain separation)", signer=signer)
    if header.sender_did != signer:
        return Opened(False, "header sender is not the signer", signer=signer)
    if header.recipient_did != self_did:
        return Opened(False, "addressed to a different node", signer=signer)
    try:
        kind = _value(PacketKind, obj["kind"], "kind")
        packet = from_wire(kind, obj["payload"])
    except ProtocolError as e:
        return Opened(False, f"bad payload: {e}", signer=signer)
    # Admit last, so a packet refused for any reason above never uses up its seq.
    reason = replay.admit(header)
    if reason:
        return Opened(False, reason, signer=signer)
    return Opened(True, envelope=Envelope(header, kind, packet, signer), signer=signer)


__all__ = [
    "PROTOCOL_VERSION",
    "DEFAULT_MAX_SKEW_NS",
    "ProtocolError",
    "Header",
    "PacketKind",
    "DialogueType",
    "DialoguePacket",
    "NodeOp",
    "NodeDelta",
    "DibRef",
    "StateSnapshotPacket",
    "RiskLevel",
    "TargetAction",
    "Gate2ChallengePacket",
    "Verdict",
    "Gate2ResponsePacket",
    "SessionEvent",
    "SessionPacket",
    "ControlOp",
    "ControlPacket",
    "DEFAULT_REPLAY_WINDOW",
    "PACKET_TYPES",
    "kind_of",
    "to_wire",
    "from_wire",
    "seal",
    "Sender",
    "ReplayGuard",
    "Envelope",
    "Opened",
    "open_envelope",
]
