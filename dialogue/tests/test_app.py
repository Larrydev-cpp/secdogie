"""The operator App's controller, headless: what it shows, what it signs, and
what it refuses to sign; the headless script runner; and the whole path
App <-> node over an in-memory wire with the real OperatorBridge."""
from __future__ import annotations

import queue
import threading

import pytest

pytest.importorskip("nacl")

from secdogie_citadel.action_gate import IntentContract, PlannedAction  # noqa: E402
from secdogie_citadel.authz import action_hash, verify_authorization  # noqa: E402
from secdogie_citadel.consolidate import verify_confirmation  # noqa: E402
from secdogie_citadel.lessons import MemoryClass, candidate_id  # noqa: E402
from secdogie_dialogue.agent_bridge import OperatorBridge  # noqa: E402
from secdogie_dialogue.app import (  # noqa: E402
    AppController,
    AppError,
    ScriptError,
    check_step,
    review_memory,
    run_script,
)
from secdogie_dialogue.guard import GuardRefusal  # noqa: E402
from secdogie_dialogue.keystore import KeystoreError  # noqa: E402
from secdogie_dialogue.protocol import (  # noqa: E402
    PROTOCOL_VERSION,
    ControlOp,
    ControlPacket,
    DialoguePacket,
    DialogueType,
    Envelope,
    Gate2ChallengePacket,
    Gate2ResponsePacket,
    Header,
    MemoryCandidatePacket,
    NodeDelta,
    NodeOp,
    RiskLevel,
    SessionEvent,
    SessionPacket,
    StateSnapshotPacket,
    TargetAction,
    Verdict,
    kind_of,
)
from secdogie_dialogue.session import DialogueSession  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402

APP, NODE, OPERATOR, OTHER = (Identity.generate() for _ in range(4))


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeSession:
    """Records what the controller sends; ``react`` may answer synchronously."""

    def __init__(self, react=None):
        self.identity, self.peer_did = APP, NODE.did
        self.sent: list = []
        self.closed = False
        self.react = react
        self.on_envelope = self.on_undeliverable = self.on_peer_down = self.on_peer_up = None

    def send(self, packet, *, reliable=None):
        self.sent.append(packet)
        if self.react is not None:
            self.react(packet)
        return len(self.sent)

    def close(self):
        self.closed = True

    def of(self, cls):
        return [p for p in self.sent if isinstance(p, cls)]


_seq = iter(range(1, 10**9))


def deliver(ctl, packet, signer=NODE.did):
    hdr = Header(PROTOCOL_VERSION, signer, APP.did, "s", next(_seq), 0)
    ctl.on_envelope(Envelope(hdr, kind_of(packet), packet, signer))


DELETE = TargetAction("delete", "f-1", "file", "report.txt", "", True)


def challenge(action=DELETE, *, cid="ch1", subject=None, expires=1100.0, claimed=None):
    return Gate2ChallengePacket(cid, action, RiskLevel.HIGH, "the file goes to the bin",
                                claimed or action_hash(action), subject or NODE.did, expires)


def memory(key="report-folder", value="reports go to ~/Reports", mclass="fact", mid=None, source="model"):
    return MemoryCandidatePacket(mid or candidate_id(MemoryClass(mclass), "global", key, value),
                                 mclass, "global", key, value, source)


def make(**kw):
    clock = kw.pop("clock", None) or Clock()
    s = FakeSession(kw.pop("react", None))
    return AppController(s, clock=clock, **kw), s, clock


class Unlock:
    def __init__(self, identity=OPERATOR, raises=None, then=None):
        self.identity, self.raises, self.then, self.calls = identity, raises, then, 0

    def __call__(self):
        self.calls += 1
        if self.then is not None:
            self.then()
        if self.raises is not None:
            raise self.raises
        return self.identity


# ---- dialogue and view --------------------------------------------------------------


def test_start_says_hello_and_asks_for_the_view():
    ctl, s, _ = make()
    ctl.start()
    assert [p.event for p in s.of(SessionPacket)] == [SessionEvent.HELLO, SessionEvent.RESYNC]


def test_a_probe_is_shown_and_answered():
    ctl, s, _ = make()
    deliver(ctl, DialoguePacket("p1", DialogueType.SOCRATIC_QUESTION, "Which folder?",
                                suggested_options=("Downloads", "Desktop")))
    assert "Agent: Which folder?" in ctl.conversation_lines()
    ctl.answer("p1", option=2)
    (ans,) = s.of(DialoguePacket)
    assert ans.in_reply_to == "p1" and ans.content == "Desktop"
    with pytest.raises(AppError):
        ctl.answer("p1", "again")  # answered once, closed
    assert len(s.of(DialoguePacket)) == 1


def test_the_node_cannot_answer_for_the_operator():
    ctl, _, _ = make()
    deliver(ctl, DialoguePacket("x", DialogueType.USER_CLARIFICATION, "yes", in_reply_to="p1"))
    assert ctl.conversation.transcript() == ()
    assert any("ignored" in e for e in ctl.events())


def test_a_gap_in_the_view_asks_for_a_resync_at_most_once_per_interval():
    ctl, s, clock = make(resync_every=2.0)
    full = StateSnapshotPacket(1, 7, 1, (NodeDelta(NodeOp.ADD, 0, role="AXWindow", name="Drawing"),), full=True)
    deliver(ctl, full)
    assert not ctl.view.needs_resync and s.of(SessionPacket) == []
    gap = StateSnapshotPacket(1, 7, 5, (NodeDelta(NodeOp.ADD, 1, parent_index=0, role="AXButton"),),
                              base_generation=4)
    deliver(ctl, gap)
    assert ctl.view.needs_resync
    assert [p.event for p in s.of(SessionPacket)] == [SessionEvent.RESYNC]
    deliver(ctl, StateSnapshotPacket(1, 7, 6, (), base_generation=5))  # within the interval: no second ask
    assert len(s.of(SessionPacket)) == 1
    clock.t += 2.0
    deliver(ctl, StateSnapshotPacket(1, 7, 7, (), base_generation=6))
    assert len(s.of(SessionPacket)) == 2
    assert any("Drawing" in ln for ln in ctl.inspector_lines())  # the last consistent tree stays shown


# ---- Gate 2 --------------------------------------------------------------------------


def test_approve_signs_exactly_the_action_shown_for_this_node():
    ctl, s, clock = make()
    deliver(ctl, challenge())
    (pc,) = ctl.challenges()
    assert pc.review.signable
    unlock = Unlock()
    resp = ctl.approve("ch1", unlock)
    assert unlock.calls == 1
    assert s.of(Gate2ResponsePacket) == [resp] and resp.user_verdict is Verdict.APPROVE
    planned = PlannedAction("delete", "f-1", "file", "report.txt", "", True)
    res = verify_authorization(resp.authorization, planned, operators=Allowlist({OPERATOR.did}),
                               subject=NODE.did, now=clock.t)
    assert res.ok, res.reason
    assert ctl.challenges() == ()


@pytest.mark.parametrize("bad, why", [
    (challenge(claimed="00" * 32), "does not match"),
    (challenge(subject=OTHER.did), "different node"),
    (challenge(expires=999.0), "expired"),
])
def test_an_unsignable_challenge_never_asks_for_the_key(bad, why):
    ctl, s, _ = make()
    deliver(ctl, bad)
    unlock = Unlock()
    with pytest.raises(GuardRefusal, match=why):
        ctl.approve("ch1", unlock)
    assert unlock.calls == 0 and s.sent == []
    assert len(ctl.challenges()) == 1  # still there, for a Deny
    resp = ctl.deny("ch1")
    assert resp.user_verdict is Verdict.DENY and not resp.authorization
    assert s.sent == [resp] and ctl.challenges() == ()


def test_expiry_while_unlocking_signs_nothing():
    ctl, s, clock = make()
    deliver(ctl, challenge(expires=1010.0))

    def slow():
        clock.t = 1011.0

    with pytest.raises(GuardRefusal, match="expired"):
        ctl.approve("ch1", Unlock(then=slow))
    assert s.sent == []


def test_a_failed_unlock_signs_nothing_and_keeps_the_challenge():
    ctl, s, _ = make()
    deliver(ctl, challenge())
    with pytest.raises(KeystoreError):
        ctl.approve("ch1", Unlock(raises=KeystoreError("wrong passphrase")))
    assert s.sent == [] and len(ctl.challenges()) == 1


def test_a_challenge_is_answered_once():
    ctl, s, _ = make()
    deliver(ctl, challenge())
    ctl.approve("ch1", Unlock())
    with pytest.raises(AppError):
        ctl.approve("ch1", Unlock())
    with pytest.raises(AppError):
        ctl.deny("ch1")
    deliver(ctl, challenge())  # the same id again is not asked again
    assert ctl.challenges() == () and len(s.sent) == 1


def test_a_challenge_settled_while_signing_is_not_sent():
    ctl, s, _ = make()
    deliver(ctl, challenge())
    with pytest.raises(AppError, match="meanwhile"):
        ctl.approve("ch1", Unlock(then=lambda: ctl.deny("ch1")))
    assert [p.user_verdict for p in s.of(Gate2ResponsePacket)] == [Verdict.DENY]


def test_unknown_challenge():
    ctl, _, _ = make()
    with pytest.raises(AppError):
        ctl.approve("nope", Unlock())
    with pytest.raises(AppError):
        ctl.deny("nope")


def test_tick_drops_expired_challenges():
    ctl, _, clock = make()
    deliver(ctl, challenge(expires=1005.0))
    ctl.tick()
    assert len(ctl.challenges()) == 1
    clock.t = 1005.0
    ctl.tick()
    assert ctl.challenges() == () and any("expired" in e for e in ctl.events())


def test_challenge_lines_show_the_local_hash_check_and_clean_node_text():
    ctl, _, _ = make()
    evil = TargetAction("delete", "", "file", "a\x1b[2Jb\nc", "", True)
    deliver(ctl, challenge(evil, claimed="00" * 32))
    lines = ctl.challenge_lines(ctl.challenges()[0])
    text = "\n".join(lines)
    assert "DOES NOT MATCH" in text and "cannot be signed" in text
    assert "\x1b" not in text and all("\n" not in ln for ln in lines)
    ctl2, _, _ = make()
    deliver(ctl2, challenge())
    assert "matches" in "\n".join(ctl2.challenge_lines(ctl2.challenges()[0]))


# ---- the node going away ----------------------------------------------------------


def test_peer_down_drops_pending_approvals_and_questions():
    ctl, s, _ = make()
    deliver(ctl, challenge())
    deliver(ctl, DialoguePacket("p1", DialogueType.SOCRATIC_QUESTION, "Which?"))
    ctl.on_peer_down()
    assert ctl.challenges() == () and ctl.conversation.pending() == ()
    assert any(ln.startswith("App: the node stopped answering") for ln in ctl.conversation_lines())
    assert "UNREACHABLE" in ctl.status_line()
    with pytest.raises(AppError):
        ctl.approve("ch1", Unlock())
    ctl.on_peer_up()
    assert "connected" in ctl.status_line()
    assert s.of(SessionPacket)[-1].event is SessionEvent.RESYNC


def test_bye_is_the_node_going_away():
    ctl, _, _ = make()
    deliver(ctl, challenge())
    deliver(ctl, SessionPacket(SessionEvent.BYE))
    assert ctl.challenges() == () and not ctl.peer_up


def test_packets_the_node_may_not_send_are_ignored():
    ctl, _, _ = make()
    deliver(ctl, ControlPacket("r", ControlOp.STOP, goal_id="g"))
    deliver(ctl, Gate2ResponsePacket("c", "h", Verdict.DENY))
    assert sum("ignored" in e for e in ctl.events()) == 2


# ---- memory ------------------------------------------------------------------------------


def test_confirm_memory_signs_a_confirmation_bound_to_the_content_and_this_node():
    ctl, s, _ = make()
    deliver(ctl, memory())
    (m,) = ctl.memories()
    assert m.confirmable
    pkt = ctl.confirm_memory(m.packet.memory_id)
    assert s.of(ControlPacket) == [pkt] and pkt.op is ControlOp.CONFIRM_MEMORY
    res = verify_confirmation(pkt.confirmation, memory_id=m.local_id, subject=NODE.did,
                              confirmers=Allowlist({APP.did}))
    assert res.ok, res.reason
    assert ctl.memories() == ()
    deliver(ctl, DialoguePacket("st", DialogueType.SYSTEM_STATUS, "remembered", in_reply_to=pkt.request_id))
    assert ctl.requests()[pkt.request_id].reply == "remembered"


@pytest.mark.parametrize("pkt, why", [
    (memory(mid="ab" * 32), "does not match"),  # the node shows one note, claims another id
    (memory(key="api_key", value="sk-live-abcdefghijklmnopqrstuvwx"), "credential"),
    (memory(source="someone"), "unknown source"),
])
def test_an_unconfirmable_memory_is_never_signed(pkt, why):
    ctl, s, _ = make()
    deliver(ctl, pkt)
    (m,) = ctl.memories()
    assert not m.confirmable and why in " ".join(m.problems)
    with pytest.raises(AppError):
        ctl.confirm_memory(pkt.memory_id)
    assert s.sent == []
    ctl.dismiss_memory(pkt.memory_id)
    assert ctl.memories() == ()


def test_review_memory_rejects_an_unknown_class():
    assert not review_memory(MemoryCandidatePacket("m", "belief", "global", "k", "v", "model")).confirmable


def test_an_undelivered_request_is_marked():
    ctl, _, _ = make()
    pkt = ctl.add_goal("tidy the desktop", "g1")
    ctl.on_undeliverable(1, pkt)
    assert ctl.requests()[pkt.request_id].undelivered
    assert any("not delivered" in e for e in ctl.events())


def test_goal_controls():
    ctl, s, _ = make()
    ctl.add_goal("  tidy the desktop ", "g1")
    ctl.pause("g1")
    ctl.resume("g1")
    ctl.stop("g1")
    ctl.retract_memory("m1")
    assert [(p.op, p.goal_id or p.memory_id) for p in s.of(ControlPacket)] == [
        (ControlOp.ADD_GOAL, "g1"), (ControlOp.PAUSE, "g1"), (ControlOp.RESUME, "g1"),
        (ControlOp.STOP, "g1"), (ControlOp.RETRACT_MEMORY, "m1")]
    assert s.of(ControlPacket)[0].title == "tidy the desktop"
    with pytest.raises(ValueError):
        ctl.add_goal("   ")


# ---- headless script -------------------------------------------------------------------


@pytest.mark.parametrize("step", [
    {"op": "approve_all"},
    {"op": "approve", "action": {"kind": "delete"}},  # which target?
    {"op": "approve", "action": {"target_name": "x"}},  # which kind?
    {"op": "approve", "action": {"kind": "delete", "target_name": "x", "hash": "y"}},
    {"op": "approve"},
    {"op": "answer", "match": "?", "text": "a", "option": 1},
    {"op": "answer", "match": "?"},
    {"op": "add_goal", "title": "t", "extra": 1},
    {"op": "stop", "goal_id": "g", "timeout": 0},
    {"op": "stop", "goal_id": "g", "timeout": True},
    "stop",
])
def test_check_step_is_strict(step):
    with pytest.raises(ScriptError):
        check_step(step)


class FakeNode:
    """Answers control requests on the controller's own thread."""

    def __init__(self, reply="accepted"):
        self.ctl = None
        self.reply = reply

    def __call__(self, packet):
        if isinstance(packet, ControlPacket):
            deliver(self.ctl, DialoguePacket("st-" + packet.request_id, DialogueType.SYSTEM_STATUS,
                                             self.reply, in_reply_to=packet.request_id))


def scripted(reply="accepted"):
    node = FakeNode(reply)
    ctl, s, clock = make(react=node)
    node.ctl = ctl
    return ctl, s, clock


def test_a_script_runs_every_step_and_reports_each():
    ctl, s, _ = scripted()
    deliver(ctl, challenge())
    deliver(ctl, memory())
    deliver(ctl, DialoguePacket("p1", DialogueType.SOCRATIC_QUESTION, "Which folder?",
                                suggested_options=("Downloads", "Desktop")))
    deliver(ctl, DialoguePacket("s1", DialogueType.SYSTEM_STATUS, "step 3 done"))
    out = []
    rc = run_script(ctl, [
        {"op": "add_goal", "title": "file the report", "goal_id": "g1"},
        {"op": "answer", "match": "folder", "option": 1},
        {"op": "approve", "action": {"kind": "delete", "target_name": "report.txt"}},
        {"op": "confirm_memory", "key": "report-folder"},
        {"op": "expect_status", "match": "step 3"},
    ], unlock=Unlock(), emit=out.append, default_timeout=1)
    assert rc == 0, out
    assert [r["op"] for r in out] == ["add_goal", "answer", "approve", "confirm_memory", "expect_status"]
    assert all(r["ok"] for r in out)
    assert out[0]["reply"] == "accepted" and out[1]["probe_id"] == "p1"
    assert s.of(Gate2ResponsePacket)[0].user_verdict is Verdict.APPROVE
    assert s.of(DialoguePacket)[0].content == "Downloads"


def test_a_script_approves_only_the_action_it_names():
    ctl, s, _ = scripted()
    deliver(ctl, challenge(TargetAction("delete", "f-9", "file", "payroll.xlsx", "", True)))
    out = []
    rc = run_script(ctl, [{"op": "approve", "action": {"kind": "delete", "target_name": "report.txt"},
                           "timeout": 0.2}], unlock=Unlock(), emit=out.append)
    assert rc == 1 and "timed out" in out[0]["error"]
    assert s.sent == [] and len(ctl.challenges()) == 1


def test_a_script_cannot_approve_without_the_operator_key():
    ctl, s, _ = scripted()
    deliver(ctl, challenge())
    out = []
    rc = run_script(ctl, [{"op": "approve", "action": {"kind": "delete", "target_id": "f-1"}}],
                    unlock=None, emit=out.append, default_timeout=1)
    assert rc == 1 and "operator key" in out[0]["error"] and s.sent == []


def test_a_bad_step_fails_the_script_before_anything_runs():
    ctl, s, _ = scripted()
    out = []
    rc = run_script(ctl, [{"op": "add_goal", "title": "t"}, {"op": "nope"}], emit=out.append)
    assert rc == 1 and out[0]["step"] == 1 and s.sent == []


def test_a_failed_step_stops_the_script():
    ctl, s, _ = scripted(reply="refused: no such goal")
    out = []
    rc = run_script(ctl, [{"op": "stop", "goal_id": "g9"}, {"op": "add_goal", "title": "t"}],
                    emit=out.append, default_timeout=1)
    assert rc == 1 and len(out) == 1 and "refused" in out[0]["error"]
    assert len(s.of(ControlPacket)) == 1


def test_expect_status_reads_the_transcript_in_order():
    ctl, _, _ = scripted()
    deliver(ctl, DialoguePacket("a", DialogueType.SYSTEM_STATUS, "phase one"))
    out = []
    rc = run_script(ctl, [{"op": "expect_status", "match": "phase"},
                          {"op": "expect_status", "match": "phase", "timeout": 0.2}], emit=out.append)
    assert rc == 1 and out[0]["ok"] and "timed out" in out[1]["error"]  # the same line is not matched twice


def test_expect_view():
    ctl, _, _ = scripted()
    deliver(ctl, StateSnapshotPacket(1, 7, 1, (NodeDelta(NodeOp.ADD, 0, role="AXWindow", name="Drawing"),),
                                     full=True))
    out = []
    assert run_script(ctl, [{"op": "expect_view", "match": "Drawing"}], emit=out.append, default_timeout=1) == 0


# ---- App <-> node over a wire, with the real bridge ------------------------------------------------


class Wire:
    def __init__(self):
        self.ends: dict[str, DialogueSession] = {}
        self._q: queue.Queue = queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()

    def sender(self, to):
        return lambda frame: self._q.put((to, frame)) or True

    def _run(self):
        while True:
            to, frame = self._q.get()
            self.ends[to].receive(frame)


def test_app_and_bridge_end_to_end():
    wire = Wire()
    node_s = DialogueSession(NODE, APP.did, wire.sender("app"), trust=Allowlist({APP.did}), heartbeat_interval=1e9)
    app_s = DialogueSession(APP, NODE.did, wire.sender("node"), trust=Allowlist({NODE.did}), heartbeat_interval=1e9)
    wire.ends.update(node=node_s, app=app_s)
    controls = []

    def on_control(pkt, signer):
        controls.append((pkt.op, signer))
        return "accepted"

    bridge = OperatorBridge(NODE, node_s, operators=Allowlist({OPERATOR.did}), challenge_ttl=5.0, probe_ttl=5.0,
                            on_control=on_control)
    ctl = AppController(app_s)
    planned = PlannedAction("key", "element:7", "file", "report.txt", "delete", True,
                            intent=IntentContract(rollback="restore from Trash"))
    results = {}

    def node_side():
        results["token"] = bridge.authorize(planned)
        results["answer"] = bridge.ask("Which folder?", options=("Downloads", "Desktop"))

    t = threading.Thread(target=node_side)
    t.start()
    out = []
    rc = run_script(ctl, [
        {"op": "add_goal", "title": "file the report", "goal_id": "g1"},
        {"op": "approve", "action": {"kind": "key", "target_name": "report.txt"}},
        {"op": "answer", "match": "folder", "text": "Desktop"},
    ], unlock=Unlock(), emit=out.append, default_timeout=5)
    t.join(10)
    assert rc == 0, out
    assert controls == [(ControlOp.ADD_GOAL, APP.did)]
    res = verify_authorization(results["token"], planned, operators=Allowlist({OPERATOR.did}), subject=NODE.did)
    assert res.ok, res.reason
    assert results["answer"] == "Desktop"
