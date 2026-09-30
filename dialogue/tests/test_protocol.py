"""The dialogue wire: signed envelopes, authenticate-before-parse, strict schema,
recipient binding, freshness and replay. Headless: signing + parsing only."""
from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from secdogie_dialogue.protocol import (
    PROTOCOL_VERSION,
    ControlOp,
    ControlPacket,
    DialoguePacket,
    DialogueType,
    DibRef,
    Gate2ChallengePacket,
    Gate2ResponsePacket,
    Header,
    MemoryCandidatePacket,
    NodeDelta,
    NodeOp,
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
    to_wire,
)
from secdogie_identity import (
    Allowlist,
    Identity,
    MasterSet,
    TrustPolicy,
    cosign,
    create_revocation,
    sign_payload,
)

S = 1_000_000_000  # one second in ns
T0 = 1_000_000 * S


class Clock:
    def __init__(self, t: int = T0):
        self.t = t

    def __call__(self) -> int:
        return self.t


OP = Identity.generate()  # the operator's App
NODE = Identity.generate()  # the Agent node


def _packets():
    return [
        DialoguePacket("p1", DialogueType.SOCRATIC_QUESTION, "Which Save?",
                       suggested_options=("toolbar", "dialog"), gate_finding="ambiguous-target"),
        DialoguePacket("c1", DialogueType.USER_CLARIFICATION, "toolbar", in_reply_to="p1"),
        StateSnapshotPacket(
            window_id=42, app_pid=7, generation=3, full=True, focused_node_index=2,
            nodes=(NodeDelta(NodeOp.ADD, 0, role="AXWindow", name="Drawing"),
                   NodeDelta(NodeOp.ADD, 2, automation_id="canvas", path_index=(0, 1), role="canvas",
                             bounds=(0, 0, 800, 600), parent_index=0)),
            dib_references=(DibRef(2, 800, 600, "BGRA8", "ab" * 32),)),
        Gate2ChallengePacket("ch1", TargetAction("delete", "file-42", "button", "Delete", "", True),
                             RiskLevel.IRREVERSIBLE, "no undo", "00" * 32, NODE.did, 1234.5),
        Gate2ResponsePacket("ch1", "00" * 32, Verdict.DENY),
        Gate2ResponsePacket("ch1", "00" * 32, Verdict.APPROVE, {"type": "x", "sig": "y"}),
        SessionPacket(SessionEvent.HEARTBEAT),
        StateSnapshotPacket(42, 7, 4, (NodeDelta(NodeOp.REMOVE, 2),), base_generation=3),
        ControlPacket("r1", ControlOp.ADD_GOAL, goal_id="g1", title="tidy the desktop"),
        ControlPacket("r2", ControlOp.CONFIRM_MEMORY, memory_id="m1", confirmation={"type": "x", "sig": "y"}),
        MemoryCandidatePacket("m1", "fact", "global", "report-folder", "reports go to ~/Reports", "model"),
    ]


def _pair(clock=None):
    """App -> node: a sender on the App and what the node needs to open."""
    clock = clock or Clock()
    sender = Sender(OP, NODE.did, clock_ns=clock)
    replay = ReplayGuard(clock_ns=clock)
    return sender, replay, clock


def _open(obj, replay, trust=None, self_did=None):
    return open_envelope(obj, trust=trust if trust is not None else Allowlist({OP.did}),
                         self_did=self_did or NODE.did, replay=replay)


def _raw(signer, header: dict, kind="session", payload=None):
    """Hand-build a signed envelope (for cases `seal` refuses to produce)."""
    return sign_payload(signer, {"header": header, "kind": kind,
                                 "payload": payload if payload is not None else {"event": "hello", "note": ""}})


def _hdr(**kw):
    base = dict(version=PROTOCOL_VERSION, sender_did=OP.did, recipient_did=NODE.did,
                session_id="s1", seq=1, timestamp_ns=T0)
    base.update(kw)
    return base


# ---- round trip -------------------------------------------------------------


@pytest.mark.parametrize("packet", _packets(), ids=lambda p: type(p).__name__)
def test_every_packet_round_trips(packet):
    sender, replay, _ = _pair()
    opened = _open(sender.seal(packet), replay)
    assert opened.ok, opened.reason
    assert opened.envelope.packet == packet
    assert opened.envelope.signer == OP.did
    assert opened.envelope.header.recipient_did == NODE.did


def test_target_action_hashes_like_the_planned_action_it_came_from():
    pytest.importorskip("secdogie_citadel")
    from secdogie_citadel.action_gate import PlannedAction
    from secdogie_citadel.authz import action_hash

    planned = PlannedAction(kind="delete", target_id="f1", target_name="Delete", expected_observation="gone")
    assert action_hash(TargetAction.from_action(planned)) == action_hash(planned)
    # and the defaults agree: the same explicit arguments give the same hash
    assert action_hash(TargetAction("delete", "f1")) == action_hash(PlannedAction("delete", "f1"))


# ---- authentication comes first ---------------------------------------------


def test_unsigned_or_tampered_is_refused():
    sender, replay, _ = _pair()
    good = sender.seal(SessionPacket(SessionEvent.HELLO))
    unsigned = {k: v for k, v in good.items() if k not in ("signer", "sig")}
    assert _open(unsigned, replay).reason == "invalid or missing signature"
    tampered = {**good, "payload": {"event": "bye", "note": ""}}
    assert _open(tampered, replay).reason == "invalid or missing signature"


def test_untrusted_and_revoked_senders_are_refused():
    sender, replay, _ = _pair()
    obj = sender.seal(SessionPacket(SessionEvent.HELLO))
    res = _open(obj, replay, trust=Allowlist({Identity.generate().did}))
    assert not res.ok and res.reason == "sender not trusted or revoked" and res.signer == OP.did

    master = Identity.generate()
    policy = TrustPolicy(Allowlist({OP.did}), masters=MasterSet([master.did]))
    assert _open(sender.seal(SessionPacket(SessionEvent.HEARTBEAT)), replay, trust=policy).ok
    policy.apply(cosign(master, create_revocation([OP.did])))
    res = _open(sender.seal(SessionPacket(SessionEvent.HEARTBEAT)), replay, trust=policy)
    assert not res.ok and res.reason == "sender not trusted or revoked"


def test_no_trust_policy_means_nothing_opens():
    sender, replay, _ = _pair()
    res = open_envelope(sender.seal(SessionPacket(SessionEvent.HELLO)), trust=None,
                        self_did=NODE.did, replay=replay)
    assert not res.ok and res.reason == "no trust policy configured"


def test_payload_is_never_parsed_before_the_sender_is_authenticated():
    # A malformed payload from an untrusted signer, and an unsigned one: both are
    # refused for who sent them, never for what they contain -- the parser is not
    # reached.
    _, replay, _ = _pair()
    stranger = Identity.generate()
    garbage = {"event": "nope", "pixels": "AAAA"}
    from_stranger = _raw(stranger, _hdr(sender_did=stranger.did), payload=garbage)
    assert _open(from_stranger, replay).reason == "sender not trusted or revoked"
    unsigned = {"header": _hdr(), "kind": "session", "payload": garbage}
    assert _open(unsigned, replay).reason == "invalid or missing signature"


# ---- header binding -----------------------------------------------------------


def test_header_sender_must_be_the_signer():
    _, replay, _ = _pair()
    other = Identity.generate()
    obj = _raw(OP, _hdr(sender_did=other.did))
    res = _open(obj, replay, trust=Allowlist({OP.did, other.did}))
    assert not res.ok and res.reason == "header sender is not the signer"
    with pytest.raises(ValueError):
        seal(OP, Header(**_hdr(sender_did=other.did)), SessionPacket(SessionEvent.HELLO))


def test_a_packet_for_another_node_does_not_open_here():
    sender, _, clock = _pair()
    obj = sender.seal(SessionPacket(SessionEvent.HELLO))  # addressed to NODE
    elsewhere = Identity.generate()
    res = _open(obj, ReplayGuard(clock_ns=clock), self_did=elsewhere.did)
    assert not res.ok and res.reason == "addressed to a different node"


def test_a_validly_signed_object_of_another_protocol_is_refused():
    _, replay, _ = _pair()
    res = _open(_raw(OP, _hdr(version="secdogie/something-else/v1")), replay)
    assert not res.ok and "domain separation" in res.reason
    # an authorization token (or any other signed statement) is not an envelope
    res = _open(sign_payload(OP, {"type": "secdogie/action-authorization/v1", "subject": NODE.did}), replay)
    assert not res.ok and res.reason == "not a dialogue envelope"


# ---- freshness and replay -----------------------------------------------------


def test_an_in_order_stream_is_admitted_and_each_packet_only_once():
    sender, replay, _ = _pair()
    stream = [sender.seal(SessionPacket(SessionEvent.HEARTBEAT)) for _ in range(5)]
    assert all(_open(p, replay).ok for p in stream)
    assert all(_open(p, replay).reason == "replayed sequence number" for p in stream)


def test_replays_are_refused_but_reordering_inside_the_window_is_not():
    sender, replay, _ = _pair()
    first = sender.seal(SessionPacket(SessionEvent.HELLO))
    second = sender.seal(SessionPacket(SessionEvent.HEARTBEAT))
    assert _open(second, replay).ok
    assert _open(first, replay).ok  # UDP reordered it: still admitted, once
    assert _open(second, replay).reason == "replayed sequence number"
    assert _open(first, replay).reason == "replayed sequence number"


def test_a_packet_too_far_behind_the_newest_is_refused():
    clock = Clock()
    replay = ReplayGuard(window=4, clock_ns=clock)
    s = Sender(OP, NODE.did, clock_ns=clock)
    held = [s.seal(SessionPacket(SessionEvent.HEARTBEAT)) for _ in range(5)]  # seq 1..5, delayed
    assert _open(s.seal(SessionPacket(SessionEvent.HEARTBEAT)), replay).ok  # seq 6 arrives first
    # a 4-wide window behind seq 6 holds 3..6
    assert _open(held[1], replay).reason == "sequence number too old (outside the replay window)"  # seq 2
    assert _open(held[2], replay).ok  # seq 3: just inside



def test_sessions_are_sequenced_independently():
    clock = Clock()
    replay = ReplayGuard(clock_ns=clock)
    a = Sender(OP, NODE.did, session_id="A", clock_ns=clock)
    b = Sender(OP, NODE.did, session_id="B", clock_ns=clock)
    a.seal(SessionPacket(SessionEvent.HELLO))
    assert _open(a.seal(SessionPacket(SessionEvent.HELLO)), replay).ok  # A seq 2
    assert _open(b.seal(SessionPacket(SessionEvent.HELLO)), replay).ok  # B seq 1 is fine


def test_stale_and_future_dated_packets_are_refused():
    clock = Clock()
    replay = ReplayGuard(max_skew_ns=30 * S, clock_ns=clock)
    past = Sender(OP, NODE.did, clock_ns=lambda: T0 - 31 * S)
    future = Sender(OP, NODE.did, clock_ns=lambda: T0 + 31 * S)
    edge = Sender(OP, NODE.did, clock_ns=lambda: T0 - 30 * S)
    assert "clock-skew" in _open(past.seal(SessionPacket(SessionEvent.HELLO)), replay).reason
    assert "clock-skew" in _open(future.seal(SessionPacket(SessionEvent.HELLO)), replay).reason
    assert _open(edge.seal(SessionPacket(SessionEvent.HELLO)), replay).ok


def test_a_refused_packet_does_not_use_up_its_sequence_number():
    _, replay, _ = _pair()
    bad = _raw(OP, _hdr(seq=5), payload={"event": "hello"})  # missing field
    assert _open(bad, replay).reason.startswith("bad payload")
    good = _raw(OP, _hdr(seq=5))
    assert _open(good, replay).ok


def test_live_sessions_are_never_evicted_to_make_room():
    clock = Clock()
    replay = ReplayGuard(max_skew_ns=10 * S, max_sessions=2, clock_ns=clock)
    for sid in ("A", "B"):
        assert _open(Sender(OP, NODE.did, session_id=sid, clock_ns=clock).seal(
            SessionPacket(SessionEvent.HELLO)), replay).ok
    res = _open(Sender(OP, NODE.did, session_id="C", clock_ns=clock).seal(
        SessionPacket(SessionEvent.HELLO)), replay)
    assert not res.ok and res.reason == "too many live sessions"
    clock.t += 21 * S  # A and B idle for more than twice the skew window
    assert _open(Sender(OP, NODE.did, session_id="C", clock_ns=clock).seal(
        SessionPacket(SessionEvent.HELLO)), replay).ok


def test_forgetting_an_idle_session_cannot_reopen_a_replay():
    # The sender's clock runs ahead by nearly the whole skew window, so its
    # packet stays "fresh" here for up to twice the window after we admit it. A
    # session must not be forgotten before then, or that packet replays.
    clock = Clock()
    skew = 10 * S
    replay = ReplayGuard(max_skew_ns=skew, max_sessions=1, clock_ns=clock)
    ahead = Sender(OP, NODE.did, session_id="A", clock_ns=lambda: clock.t + 9 * S)
    captured = ahead.seal(SessionPacket(SessionEvent.HELLO))
    assert _open(captured, replay).ok
    clock.t += 15 * S  # past one skew window, inside two
    # a new session asks for room; A must not be forgotten yet
    res = _open(Sender(OP, NODE.did, session_id="B", clock_ns=clock).seal(
        SessionPacket(SessionEvent.HELLO)), replay)
    assert res.reason == "too many live sessions"
    assert _open(captured, replay).reason == "replayed sequence number"


# ---- strict schema ------------------------------------------------------------


def _signed_payload(kind, payload, seq=1):
    return _raw(OP, _hdr(seq=seq), kind=kind, payload=payload)


def _snapshot_wire(**over):
    p = to_wire(_packets()[2])
    p.update(over)
    return p


@pytest.mark.parametrize("payload,why", [
    (_snapshot_wire(pixels="AAAA"), "unknown field"),
    ({k: v for k, v in _snapshot_wire().items() if k != "full"}, "missing field"),
    (_snapshot_wire(generation=True), "expected an int"),
    (_snapshot_wire(generation="3"), "expected an int"),
    (_snapshot_wire(full=1), "expected a bool"),
    (_snapshot_wire(nodes="x"), "expected a list"),
    (_snapshot_wire(nodes=[{**to_wire(_packets()[2].nodes[0]), "op": "explode"}]), "unknown value"),
    (_snapshot_wire(nodes=[{**to_wire(_packets()[2].nodes[0]), "bounds": [0, 0, 1]}]), "expected 4 items"),
])
def test_snapshot_schema_is_strict(payload, why):
    _, replay, _ = _pair()
    res = _open(_signed_payload("state_snapshot", payload), replay)
    assert not res.ok and why in res.reason


def test_a_challenge_cannot_carry_a_non_finite_expiry():
    _, replay, _ = _pair()
    wire = to_wire(_packets()[3])
    for bad in (float("inf"), float("nan")):
        res = _open(_signed_payload("gate2_challenge", {**wire, "expires_at": bad}), replay)
        assert not res.ok and "finite" in res.reason


def test_unknown_kind_is_refused():
    _, replay, _ = _pair()
    res = _open(_signed_payload("screenshot", {}), replay)
    assert not res.ok and "unknown value" in res.reason


# ---- packet invariants ---------------------------------------------------------


def test_packet_invariants():
    with pytest.raises(ProtocolError):
        Gate2ResponsePacket("c", "h", Verdict.DENY, {"sig": "x"})  # a Deny never carries a token
    with pytest.raises(ProtocolError):
        Gate2ResponsePacket("c", "h", Verdict.APPROVE)  # an Approve always does
    with pytest.raises(ProtocolError):
        DialoguePacket("x", DialogueType.USER_CLARIFICATION, "yes")  # answers which probe?
    with pytest.raises(ProtocolError):
        StateSnapshotPacket(1, 1, 1, (NodeDelta(NodeOp.REMOVE, 3),), full=True)
    with pytest.raises(ProtocolError):
        NodeDelta(NodeOp.ADD, 3, parent_index=3)
    with pytest.raises(ProtocolError):
        Header(**_hdr(seq=-1))


@pytest.mark.parametrize("make", [
    lambda: StateSnapshotPacket(1, 1, 3, (), full=True, base_generation=2),  # full has no base
    lambda: StateSnapshotPacket(1, 1, 3, ()),  # a delta must name its base
    lambda: StateSnapshotPacket(1, 1, 3, (), base_generation=3),  # base must be earlier
    lambda: ControlPacket("", ControlOp.STOP, goal_id="g"),  # no request id
    lambda: ControlPacket("r", ControlOp.STOP),  # which goal?
    lambda: ControlPacket("r", ControlOp.ADD_GOAL, goal_id="g", title="  "),  # no task
    lambda: ControlPacket("r", ControlOp.CONFIRM_MEMORY, memory_id="m"),  # confirmation missing
    lambda: ControlPacket("r", ControlOp.CONFIRM_MEMORY, confirmation={"sig": "x"}),  # which memory?
    lambda: ControlPacket("r", ControlOp.RETRACT_MEMORY, memory_id="m", confirmation={"sig": "x"}),
    lambda: MemoryCandidatePacket("", "fact", "global", "k", "v", "model"),  # which memory?
    lambda: MemoryCandidatePacket("m", "fact", "global", " ", "v", "model"),  # no key
    lambda: MemoryCandidatePacket("m", "fact", "global", "k", "", "model"),  # nothing to remember
])
def test_snapshot_and_control_invariants(make):
    with pytest.raises(ProtocolError):
        make()


def test_invariants_hold_on_the_wire_too():
    _, replay, _ = _pair()
    deny_with_token = {"challenge_id": "c", "action_hash": "h", "user_verdict": "Deny",
                       "authorization": {"sig": "x"}}
    res = _open(_signed_payload("gate2_response", deny_with_token), replay)
    assert not res.ok and "Deny carries no authorization" in res.reason
