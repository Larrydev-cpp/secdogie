"""The node's operator bridge, end to end over an in-memory wire: a destructive
step gets its Gate 2 token from the App and that signature is its confirmation;
a denial, a timeout, a wrong key or a vanished peer refuses; ask_user becomes a
probe whose answer comes back as text; control requests are answered."""
from __future__ import annotations

import queue
import threading
import time

import pytest

pytest.importorskip("nacl")

from secdogie_citadel.action_gate import ALLOW, GateDecision  # noqa: E402
from secdogie_citadel.authz import action_hash  # noqa: E402
from secdogie_citadel.loop_gate import make_plan_gate, to_planned  # noqa: E402
from secdogie_dialogue.agent_bridge import OperatorBridge  # noqa: E402
from secdogie_dialogue.dialogue import Conversation  # noqa: E402
from secdogie_dialogue.guard import respond  # noqa: E402
from secdogie_dialogue.protocol import (  # noqa: E402
    ControlOp,
    ControlPacket,
    DialoguePacket,
    DialogueType,
    Gate2ChallengePacket,
    SessionEvent,
    SessionPacket,
    Verdict,
)
from secdogie_dialogue.session import DialogueSession  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402

NODE, APP, OPERATOR = Identity.generate(), Identity.generate(), Identity.generate()

DELETE = {"kind": "key", "element": None, "x": None, "y": None, "text": "", "keys": ["delete"], "path": "",
          "high_risk": True, "rollback": "restore from Trash", "irreversible": False}
CLICK = {**DELETE, "kind": "left_click", "x": 1, "y": 2, "keys": [], "high_risk": False, "rollback": ""}


class Wire:
    """Delivers every frame, on its own thread, as soon as it is sent."""

    def __init__(self):
        self.ends: dict[str, DialogueSession] = {}
        self._q: queue.Queue = queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()

    def sender(self, to: str):
        return lambda frame: self._q.put((to, frame)) or True

    def _run(self):
        while True:
            to, frame = self._q.get()
            self.ends[to].receive(frame)


class FakeApp:
    """Answers challenges with ``verdict`` (signing with ``operator``) and
    probes with ``answer``; records everything it sees."""

    def __init__(self, session, *, verdict=Verdict.APPROVE, operator=OPERATOR, answer="Downloads",
                 answer_challenges=True):
        self.session, self.verdict, self.operator, self.answer = session, verdict, operator, answer
        self.answer_challenges = answer_challenges
        self.conv = Conversation()
        self.seen: list = []
        session.on_envelope = self.on_envelope

    def on_envelope(self, env):
        pkt = env.packet
        self.seen.append(pkt)
        if isinstance(pkt, Gate2ChallengePacket) and self.answer_challenges:
            op = self.operator if self.verdict is Verdict.APPROVE else None
            self.session.send(respond(pkt, self.verdict, peer_did=env.signer, operator=op))
        elif isinstance(pkt, DialoguePacket):
            self.conv.receive(pkt)
            if pkt.dialogue_type is DialogueType.SOCRATIC_QUESTION and self.answer is not None:
                self.session.send(self.conv.answer(pkt.probe_id, self.answer))


def _pair(**bridge_kw):
    wire = Wire()
    node_s = DialogueSession(NODE, APP.did, wire.sender("app"), trust=Allowlist({APP.did}), heartbeat_interval=1e9)
    app_s = DialogueSession(APP, NODE.did, wire.sender("node"), trust=Allowlist({NODE.did}), heartbeat_interval=1e9)
    wire.ends.update(node=node_s, app=app_s)
    # short TTLs: a missed answer fails a test in seconds, never hangs it
    kw = {"challenge_ttl": 3.0, "probe_ttl": 3.0, **bridge_kw}
    bridge = OperatorBridge(NODE, node_s, operators=Allowlist({OPERATOR.did}), **kw)
    return bridge, node_s, app_s


def _gate(bridge):
    return make_plan_gate((), enforce=False, authorize=bridge.authorize, operators=bridge.operators,
                          subject_did=NODE.did, observer=bridge.observe)


def _until(pred, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not pred():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


# ---- Gate 2 through the bridge ------------------------------------------------------


def test_the_operators_signature_authorizes_the_step_and_counts_as_its_confirmation():
    bridge, _, app_s = _pair()
    app = FakeApp(app_s)
    allowed, note = _gate(bridge)(DELETE, [])
    assert allowed, note
    (ch,) = [p for p in app.seen if isinstance(p, Gate2ChallengePacket)]
    assert ch.subject_did == NODE.did and ch.action_hash == action_hash(to_planned(DELETE))
    assert ch.risk_explanation == "key on (no target); rollback: restore from Trash"
    assert bridge.confirm("Execute HIGH-RISK key(delete)?", True) is True
    assert not any(isinstance(p, DialoguePacket) for p in app.seen)  # no second question
    # the marker is one-shot: the next confirmation is a real question
    assert bridge.confirm("and again?", True) is False
    (probe,) = [p for p in app.seen if isinstance(p, DialoguePacket) and p.dialogue_type is DialogueType.SOCRATIC_QUESTION]
    assert probe.suggested_options == ("Approve", "Deny")


def test_a_denial_refuses_the_step_and_the_confirmation_then_asks_the_operator():
    bridge, _, app_s = _pair()
    app = FakeApp(app_s, verdict=Verdict.DENY, answer="Deny")
    allowed, note = _gate(bridge)(DELETE, [])
    assert not allowed and "authorization" in note
    assert bridge.confirm("Execute HIGH-RISK key(delete)?", True) is False
    assert any(isinstance(p, DialoguePacket) and p.dialogue_type is DialogueType.SOCRATIC_QUESTION for p in app.seen)


def test_only_a_literal_approve_confirms():
    bridge, _, app_s = _pair()
    FakeApp(app_s, answer="approve")  # not the option offered
    assert bridge.confirm("approve plan: 1. open the file", False) is False
    bridge2, _, app_s2 = _pair()
    FakeApp(app_s2, answer="Approve")
    assert bridge2.confirm("approve plan: 1. open the file", False) is True


def test_no_answer_in_time_refuses_quickly():
    bridge, _, app_s = _pair(challenge_ttl=0.3, probe_ttl=0.3)
    FakeApp(app_s, answer=None, answer_challenges=False)
    t = time.monotonic()
    allowed, note = _gate(bridge)(DELETE, [])
    assert not allowed and "authorization" in note
    assert bridge.confirm("x", True) is False
    assert time.monotonic() - t < 2.0


def test_a_token_from_a_key_that_is_not_an_operator_is_refused_by_the_gate():
    bridge, _, app_s = _pair()
    FakeApp(app_s, operator=APP)  # the session key, not an operator
    allowed, note = _gate(bridge)(DELETE, [])
    assert not allowed and "authorization" in note
    assert bridge.confirm("x", True) is False  # nothing was marked confirmed


def test_a_non_destructive_step_asks_the_operator_for_nothing():
    bridge, _, app_s = _pair()
    app = FakeApp(app_s)
    assert _gate(bridge)(CLICK, [])[0]
    assert app.seen == []


def test_a_signature_for_one_action_does_not_confirm_another():
    bridge, _, app_s = _pair()
    FakeApp(app_s, answer="Deny")
    assert bridge.authorize(to_planned(DELETE)) is not None  # signed for DELETE
    bridge.observe(to_planned({**DELETE, "keys": ["ctrl", "s"]}), GateDecision(ALLOW))  # a different action allowed
    assert bridge.confirm("Execute HIGH-RISK key(ctrl+s)?", True) is False  # had to ask, and was denied


def test_a_peer_that_goes_away_fails_every_pending_wait_at_once():
    bridge, node_s, app_s = _pair(challenge_ttl=10.0, probe_ttl=10.0)  # only the peer going away can end these
    app = FakeApp(app_s, answer=None, answer_challenges=False)
    results = {}
    threading.Thread(target=lambda: results.setdefault("token", bridge.authorize(to_planned(DELETE))), daemon=True).start()
    threading.Thread(target=lambda: results.setdefault("answer", bridge.ask("where?")), daemon=True).start()
    _until(lambda: len(app.seen) == 2)
    node_s.on_peer_down()
    _until(lambda: len(results) == 2, timeout=1.0)  # at once, not at the timeout
    assert results == {"token": None, "answer": None}


def test_a_peer_that_goes_away_takes_a_fresh_signature_with_it():
    bridge, node_s, app_s = _pair()
    app = FakeApp(app_s)
    assert _gate(bridge)(DELETE, [])[0]  # signed and allowed: the step is marked confirmed
    node_s.on_peer_down()
    app.answer = "Deny"
    assert bridge.confirm("Execute HIGH-RISK key(delete)?", True) is False  # had to ask again


# ---- ask_user as a probe ----------------------------------------------------------------


def test_ask_returns_the_operators_answer_and_closes_the_probe_on_the_app():
    bridge, _, app_s = _pair()
    app = FakeApp(app_s, answer="the Downloads folder")
    assert bridge.ask("which folder should old files go to?") == "the Downloads folder"
    _until(lambda: any(isinstance(p, DialoguePacket) and p.dialogue_type is DialogueType.SYSTEM_STATUS for p in app.seen))
    status = [p for p in app.seen if isinstance(p, DialoguePacket) and p.dialogue_type is DialogueType.SYSTEM_STATUS][0]
    assert status.in_reply_to and app.conv.pending() == ()


def test_the_hooks_object_carries_all_five():
    bridge, _, _ = _pair()
    h = bridge.hooks()
    assert (h.confirm, h.ask, h.authorize, h.observe) == (bridge.confirm, bridge.ask, bridge.authorize, bridge.observe)
    assert h.operators is bridge.operators


# ---- control and session events ----------------------------------------------------------


def test_control_requests_are_answered_with_a_status_naming_the_request():
    bridge, _, app_s = _pair(on_control=lambda pkt, signer: f"{pkt.op.value} {pkt.goal_id} by {signer[-4:]}")
    app = FakeApp(app_s)
    app_s.send(ControlPacket("r1", ControlOp.STOP, goal_id="g1"))
    _until(lambda: any(isinstance(p, DialoguePacket) for p in app.seen))
    (status,) = [p for p in app.seen if isinstance(p, DialoguePacket)]
    assert status.in_reply_to == "r1" and status.content == f"stop g1 by {APP.did[-4:]}"


def test_a_node_without_a_control_handler_refuses_and_a_failing_one_is_reported():
    bridge, _, app_s = _pair()
    app = FakeApp(app_s)
    app_s.send(ControlPacket("r2", ControlOp.PAUSE, goal_id="g1"))
    _until(lambda: any(isinstance(p, DialoguePacket) for p in app.seen))
    assert app.seen[-1].content.startswith("refused")

    def boom(pkt, signer):
        raise RuntimeError("no such goal")

    bridge2, _, app_s2 = _pair(on_control=boom)
    app2 = FakeApp(app_s2)
    app_s2.send(ControlPacket("r3", ControlOp.RESUME, goal_id="g9"))
    _until(lambda: any(isinstance(p, DialoguePacket) for p in app2.seen))
    assert app2.seen[-1].content == "refused: no such goal" and app2.seen[-1].in_reply_to == "r3"


def test_a_resync_request_reaches_the_publisher_and_a_bye_is_a_peer_down():
    events = []
    bridge, node_s, app_s = _pair(on_resync=lambda: events.append("resync"), probe_ttl=10.0)
    FakeApp(app_s, answer=None)  # leaves the probe pending so the BYE is what ends it
    app_s.send(SessionPacket(SessionEvent.RESYNC))
    _until(lambda: events == ["resync"])
    results = {}
    threading.Thread(target=lambda: results.setdefault("a", bridge.ask("?")), daemon=True).start()
    _until(lambda: bridge.ledger.pending() != ())
    app_s.send(SessionPacket(SessionEvent.BYE), reliable=False)
    _until(lambda: "a" in results, timeout=1.0)  # the BYE ends it, long before the 10 s TTL would
    assert results["a"] is None


def test_the_bridge_needs_an_operator_trust_set():
    wire = Wire()
    s = DialogueSession(NODE, APP.did, wire.sender("app"), trust=Allowlist({APP.did}))
    with pytest.raises(ValueError):
        OperatorBridge(NODE, s, operators=None)


# ---- a resident node: the page comes and goes ------------------------------------------------


def test_holding_bridge_keeps_waiting_through_a_disconnect_and_resends_on_hello():
    bridge, node_s, app_s = _pair(hold_on_disconnect=True, challenge_ttl=10.0, probe_ttl=10.0)
    app = FakeApp(app_s, answer=None, answer_challenges=False)  # the first page just closes
    results = {}
    threading.Thread(target=lambda: results.setdefault("token", bridge.authorize(to_planned(DELETE))), daemon=True).start()
    threading.Thread(target=lambda: results.setdefault("answer", bridge.ask("where?")), daemon=True).start()
    _until(lambda: len(app.seen) == 2)
    node_s.on_peer_down()
    app_s.send(SessionPacket(SessionEvent.BYE), reliable=False)
    time.sleep(0.2)
    assert results == {} and bridge.waiting()  # neither a BYE nor peer-down ends them
    (first,) = [p for p in app.seen if isinstance(p, Gate2ChallengePacket)]
    # the page comes back and says hello: it is shown the same challenge and question again
    app.seen.clear()
    app.answer, app.answer_challenges = "Downloads", True
    app_s.send(SessionPacket(SessionEvent.HELLO))
    _until(lambda: len(results) == 2)
    again = [p for p in app.seen if isinstance(p, Gate2ChallengePacket)]
    assert again and (again[0].challenge_id, again[0].action_hash, again[0].expires_at) == \
        (first.challenge_id, first.action_hash, first.expires_at)
    assert results["token"] is not None and results["answer"] == "Downloads"
    assert not bridge.waiting()


def test_holding_bridge_still_fails_closed_at_expiry():
    bridge, node_s, app_s = _pair(hold_on_disconnect=True, challenge_ttl=0.3, probe_ttl=0.3)
    FakeApp(app_s, answer=None, answer_challenges=False)
    node_s.on_peer_down()
    assert bridge.authorize(to_planned(DELETE)) is None
    assert bridge.ask("anyone?") is None


def test_a_bridge_with_no_app_yet_waits_and_a_rebind_delivers():
    bridge = OperatorBridge(NODE, None, operators=Allowlist({OPERATOR.did}), hold_on_disconnect=True,
                            challenge_ttl=5.0, probe_ttl=5.0)
    results = {}
    threading.Thread(target=lambda: results.setdefault("answer", bridge.ask("which folder?")), daemon=True).start()
    _until(lambda: bridge.waiting())
    wire = Wire()
    node_s = DialogueSession(NODE, APP.did, wire.sender("app"), trust=Allowlist({APP.did}), heartbeat_interval=1e9)
    app_s = DialogueSession(APP, NODE.did, wire.sender("node"), trust=Allowlist({NODE.did}), heartbeat_interval=1e9)
    wire.ends.update(node=node_s, app=app_s)
    FakeApp(app_s, answer="Reports")
    bridge.rebind(node_s)
    _until(lambda: "answer" in results)
    assert results["answer"] == "Reports"
    with pytest.raises(ValueError):
        OperatorBridge(NODE, None, operators=Allowlist({OPERATOR.did}))


def test_hello_reaches_the_owner_first():
    order = []
    bridge, _, app_s = _pair(hold_on_disconnect=True, on_hello=lambda: order.append("hello"))
    app_s.send(SessionPacket(SessionEvent.HELLO))
    _until(lambda: order == ["hello"])
