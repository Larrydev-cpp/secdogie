"""The operator's App without a screen: one controller over the pure models.

``AppController`` holds what the operator sees and does everything the operator
can do, for one session with one node:

  * the conversation -- Socratic probes and status lines (``dialogue.Conversation``);
  * the structural view -- ``inspector.apply``, and a request for a full
    snapshot (``SessionEvent.RESYNC``) whenever the view has a gap;
  * Gate 2 challenges waiting for a verdict -- reviewed on arrival
    (``guard.review_challenge``) and again at the moment of signing;
  * memory the node offers for confirmation -- its id is recomputed here from
    the content shown (``lessons.candidate_id``), as the action hash is for
    Gate 2, so a node cannot show one note and collect a confirmation for
    another;
  * control requests (add_goal / stop / pause / resume / confirm_memory /
    retract_memory) and the node's replies to them.

Two keys, kept apart. The session key (the session's identity) signs envelopes
and memory confirmations. The operator key is never held here: ``approve``
takes an ``unlock`` callable, calls it once for that one signature, and drops
the key when it returns. Nothing is approved without a named challenge and an
explicit ``approve`` call; there is no default verdict and no "approve all".

The secdogie window (the ``app`` package) and the headless script runner below
only call these methods and read these views; nothing here imports a UI. Thread-safe:
the session delivers from its own threads while the UI acts from another.
Every change bumps ``version`` and wakes ``wait_for``; a UI polls ``version``
rather than being called back, so no UI code ever runs under this lock.
"""
from __future__ import annotations

import secrets
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from secdogie_citadel.consolidate import create_confirmation
from secdogie_citadel.lessons import candidate_id, validate

from .dialogue import Conversation, DialogueError
from .guard import ChallengeReview, GuardRefusal, respond, review_challenge
from .inspector import EMPTY, InspectorState, apply, clean, render_lines
from .protocol import (
    ControlOp,
    ControlPacket,
    DialoguePacket,
    DialogueType,
    Envelope,
    Gate2ChallengePacket,
    Gate2ResponsePacket,
    MemoryCandidatePacket,
    PacketKind,
    SessionEvent,
    SessionPacket,
    Verdict,
)

DEFAULT_RESYNC_EVERY = 2.0  # at most one resync request per this many seconds
LOG_SIZE = 200
SETTLED_MEMORY = 1024  # challenge ids already answered, so a repeat is not asked again


class AppError(ValueError):
    """An operator action the App refuses (unknown challenge, bad input)."""


def _new_id() -> str:
    return secrets.token_hex(8)


def short_did(did: str) -> str:
    return did if len(did) <= 24 else f"{did[:14]}…{did[-6:]}"


@dataclass(frozen=True)
class PendingChallenge:
    challenge: Gate2ChallengePacket
    review: ChallengeReview  # as of arrival; approve() reviews again when signing
    received_at: float


@dataclass(frozen=True)
class OfferedMemory:
    packet: MemoryCandidatePacket
    local_id: str  # recomputed here from the content shown; "" when the content is invalid
    problems: tuple[str, ...]

    @property
    def confirmable(self) -> bool:
        return not self.problems


@dataclass(frozen=True)
class Request:
    packet: ControlPacket
    reply: str | None = None  # the node's SystemStatus answering it
    undelivered: bool = False  # the session gave up: the node never got it


def review_memory(pkt: MemoryCandidatePacket) -> OfferedMemory:
    """Check an offered memory before the operator may confirm it."""
    try:
        mclass = validate(pkt.mclass, pkt.scope, pkt.key, pkt.value, pkt.source)
    except ValueError as e:
        return OfferedMemory(pkt, "", (f"not a valid memory: {e}",))
    local = candidate_id(mclass, pkt.scope, pkt.key, pkt.value)
    if local != pkt.memory_id:
        return OfferedMemory(pkt, local, ("the node's memory_id does not match the content shown "
                                          "(recomputed locally)",))
    return OfferedMemory(pkt, local, ())


def describe(packet) -> str:
    """One line naming an outbound packet, for the event log."""
    if isinstance(packet, Gate2ResponsePacket):
        return f"your {packet.user_verdict.value} of challenge {packet.challenge_id}"
    if isinstance(packet, ControlPacket):
        return f"{packet.op.value} request {packet.request_id}"
    if isinstance(packet, DialoguePacket):
        return f"your answer to probe {packet.in_reply_to}"
    if isinstance(packet, SessionPacket):
        return f"session {packet.event.value}"
    return type(packet).__name__


class AppController:
    """``session`` is the App's ``DialogueSession`` with one node; its
    ``identity`` is the App's session key and ``peer_did`` the node."""

    def __init__(self, session, *, clock=time.time, resync_every: float = DEFAULT_RESYNC_EVERY,
                 log_size: int = LOG_SIZE):
        self.session = session
        self.peer_did: str = session.peer_did
        self._clock = clock
        self._resync_every = float(resync_every)
        self._cond = threading.Condition(threading.RLock())
        self.conversation = Conversation()
        self.view: InspectorState = EMPTY
        self._challenges: dict[str, PendingChallenge] = {}
        self._settled: deque[str] = deque(maxlen=SETTLED_MEMORY)
        self._memories: dict[str, OfferedMemory] = {}
        self._requests: dict[str, Request] = {}
        self._events: deque[str] = deque(maxlen=int(log_size))
        self._last_resync = float("-inf")
        self.peer_up = True
        self.version = 0
        session.on_envelope = self.on_envelope
        session.on_undeliverable = self.on_undeliverable
        session.on_peer_down = self.on_peer_down
        session.on_peer_up = self.on_peer_up

    # -- lifecycle -------------------------------------------------------------

    def start(self) -> None:
        """Say hello and ask for the current view."""
        self.session.send(SessionPacket(SessionEvent.HELLO))
        self.request_resync()

    def close(self) -> None:
        self.session.close()

    def tick(self, now: float | None = None) -> None:
        """Drop challenges past their expiry: the node refuses those actions."""
        t = float(now) if now is not None else float(self._clock())
        with self._cond:
            gone = [cid for cid, pc in self._challenges.items() if t >= pc.challenge.expires_at]
            for cid in gone:
                del self._challenges[cid]
                self._settled.append(cid)
                self._note(f"challenge {cid} expired unanswered; the node refuses that action")
            if gone:
                self._changed()

    # -- inbound ---------------------------------------------------------------

    def on_envelope(self, env: Envelope) -> None:
        pkt = env.packet
        resync = False
        with self._cond:
            if env.kind is PacketKind.DIALOGUE:
                self._dialogue(pkt)
            elif env.kind is PacketKind.STATE_SNAPSHOT:
                self.view = apply(self.view, pkt)
                resync = self.view.needs_resync
            elif env.kind is PacketKind.GATE2_CHALLENGE:
                self._challenge(pkt)
            elif env.kind is PacketKind.MEMORY_CANDIDATE:
                self._memory(pkt)
            elif env.kind is PacketKind.SESSION:
                if pkt.event is SessionEvent.BYE:
                    self._gone("the node said goodbye")
                elif pkt.event is SessionEvent.HELLO:
                    self._note("the node said hello")
            else:  # GATE2_RESPONSE / CONTROL: the node has no business sending these
                self._note(f"ignored a {env.kind.value} packet from the node")
            self._changed()
        if resync:
            self._maybe_resync()

    def _dialogue(self, pkt: DialoguePacket) -> None:
        if not self.conversation.receive(pkt):
            self._note("ignored a dialogue packet the node may not send (or a repeated probe)")
            return
        if pkt.dialogue_type is DialogueType.SYSTEM_STATUS and pkt.in_reply_to in self._requests:
            req = self._requests[pkt.in_reply_to]
            self._requests[pkt.in_reply_to] = Request(req.packet, pkt.content, req.undelivered)

    def _challenge(self, pkt: Gate2ChallengePacket) -> None:
        if pkt.challenge_id in self._challenges or pkt.challenge_id in self._settled:
            return
        review = review_challenge(pkt, peer_did=self.peer_did, now=float(self._clock()))
        self._challenges[pkt.challenge_id] = PendingChallenge(pkt, review, float(self._clock()))
        self._note(f"Gate 2 challenge {pkt.challenge_id}: {clean(pkt.target_action.kind, 40)} "
                   f"({pkt.risk_level.value})" + ("" if review.signable else " -- cannot be signed"))

    def _memory(self, pkt: MemoryCandidatePacket) -> None:
        offered = review_memory(pkt)
        self._memories[pkt.memory_id] = offered
        self._note(f"memory offered: {clean(pkt.key, 60)}" + ("" if offered.confirmable else
                                                              " -- cannot be confirmed"))

    def on_undeliverable(self, msg_id: int, packet) -> None:
        with self._cond:
            if isinstance(packet, ControlPacket) and packet.request_id in self._requests:
                req = self._requests[packet.request_id]
                self._requests[packet.request_id] = Request(req.packet, req.reply, True)
            what = describe(packet)
            if isinstance(packet, Gate2ResponsePacket):
                what += "; the node refuses the action when its challenge expires"
            elif isinstance(packet, DialoguePacket):
                what += "; the step stays suspended"
            self._note(f"not delivered: {what}")
            self._changed()

    def on_peer_down(self) -> None:
        with self._cond:
            self._gone("the node stopped answering")
            self._changed()

    def on_peer_up(self) -> None:
        with self._cond:
            self.peer_up = True
            self._note("the node is reachable again")
            self._changed()
        self.request_resync()

    def _gone(self, why: str) -> None:
        """The node is gone and has failed every pending wait on its side."""
        self.peer_up = False
        for cid in list(self._challenges):
            del self._challenges[cid]
            self._settled.append(cid)
        self.conversation.drop_pending(f"{why}: open questions were closed; those steps stay suspended")
        self._note(f"{why}; pending approvals were dropped (the node refuses those actions)")

    # -- operator actions ----------------------------------------------------------

    def answer(self, probe_id: str, text: str | None = None, *, option: int | None = None) -> DialoguePacket:
        with self._cond:
            try:
                pkt = self.conversation.answer(probe_id, text, option=option)
            except DialogueError as e:
                raise AppError(str(e)) from None
            self._changed()
        self.session.send(pkt)
        return pkt

    def approve(self, challenge_id: str, unlock: Callable[[], object]) -> Gate2ResponsePacket:
        """Sign challenge ``challenge_id`` with the operator key ``unlock()``
        returns. The challenge is reviewed before the key is unlocked (nothing
        unsignable ever asks for the passphrase) and again when signing (it may
        have expired meanwhile). Raises ``GuardRefusal`` -- nothing signed, the
        challenge stays pending for a Deny -- or whatever ``unlock`` raises."""
        with self._cond:
            pc = self._challenges.get(challenge_id)
        if pc is None:
            raise AppError(f"no pending challenge {challenge_id!r}")
        review = review_challenge(pc.challenge, peer_did=self.peer_did, now=float(self._clock()))
        if not review.signable:
            raise GuardRefusal("; ".join(review.problems))
        operator = unlock()
        try:
            resp = respond(pc.challenge, Verdict.APPROVE, peer_did=self.peer_did, operator=operator,
                           now=float(self._clock()))
        finally:
            del operator  # the key lives no longer than this one signature
        self._settle(challenge_id, "approved")
        self.session.send(resp)
        return resp

    def deny(self, challenge_id: str) -> Gate2ResponsePacket:
        with self._cond:
            pc = self._challenges.get(challenge_id)
        if pc is None:
            raise AppError(f"no pending challenge {challenge_id!r}")
        resp = respond(pc.challenge, Verdict.DENY, peer_did=self.peer_did, now=float(self._clock()))
        self._settle(challenge_id, "denied")
        self.session.send(resp)
        return resp

    def _settle(self, challenge_id: str, how: str) -> None:
        with self._cond:
            if self._challenges.pop(challenge_id, None) is None:
                raise AppError(f"challenge {challenge_id!r} was settled meanwhile")
            self._settled.append(challenge_id)
            self._note(f"challenge {challenge_id} {how}")
            self._changed()

    def confirm_memory(self, memory_id: str) -> ControlPacket:
        """Confirm an offered memory: a memory-confirmation signed with the
        session key, bound to the locally recomputed id and to this node."""
        with self._cond:
            offered = self._memories.get(memory_id)
            if offered is None:
                raise AppError(f"no offered memory {memory_id!r}")
            if not offered.confirmable:
                raise AppError("; ".join(offered.problems))
            del self._memories[memory_id]
        conf = create_confirmation(self.session.identity, offered.local_id, self.peer_did, clock=self._clock)
        return self._request(ControlPacket(_new_id(), ControlOp.CONFIRM_MEMORY, memory_id=offered.local_id,
                                           confirmation=conf))

    def dismiss_memory(self, memory_id: str) -> None:
        """Not confirmed: it stays in the node's quarantine until it expires."""
        with self._cond:
            if self._memories.pop(memory_id, None) is None:
                raise AppError(f"no offered memory {memory_id!r}")
            self._note(f"memory {memory_id[:12]} left unconfirmed")
            self._changed()

    def retract_memory(self, memory_id: str) -> ControlPacket:
        return self._request(ControlPacket(_new_id(), ControlOp.RETRACT_MEMORY, memory_id=memory_id.strip()))

    def add_goal(self, title: str, goal_id: str | None = None) -> ControlPacket:
        return self._request(ControlPacket(_new_id(), ControlOp.ADD_GOAL, goal_id=goal_id or f"g-{_new_id()}",
                                           title=title.strip()))

    def stop(self, goal_id: str) -> ControlPacket:
        return self._request(ControlPacket(_new_id(), ControlOp.STOP, goal_id=goal_id))

    def pause(self, goal_id: str) -> ControlPacket:
        return self._request(ControlPacket(_new_id(), ControlOp.PAUSE, goal_id=goal_id))

    def resume(self, goal_id: str) -> ControlPacket:
        return self._request(ControlPacket(_new_id(), ControlOp.RESUME, goal_id=goal_id))

    def _request(self, pkt: ControlPacket) -> ControlPacket:
        with self._cond:
            self._requests[pkt.request_id] = Request(pkt)
            self._note(f"sent {describe(pkt)}")
            self._changed()
        self.session.send(pkt)
        return pkt

    def request_resync(self) -> None:
        with self._cond:
            self._last_resync = float(self._clock())
        self.session.send(SessionPacket(SessionEvent.RESYNC))

    def _maybe_resync(self) -> None:
        with self._cond:
            due = float(self._clock()) - self._last_resync >= self._resync_every
        if due:
            self.request_resync()

    # -- views ---------------------------------------------------------------------

    def challenges(self) -> tuple[PendingChallenge, ...]:
        with self._cond:
            return tuple(self._challenges.values())

    def memories(self) -> tuple[OfferedMemory, ...]:
        with self._cond:
            return tuple(self._memories.values())

    def requests(self) -> dict[str, Request]:
        with self._cond:
            return dict(self._requests)

    def events(self) -> tuple[str, ...]:
        with self._cond:
            return tuple(self._events)

    def status_line(self) -> str:
        with self._cond:
            state = "connected" if self.peer_up else "UNREACHABLE"
            return (f"node {short_did(self.peer_did)} · {state} · "
                    f"{len(self._challenges)} to sign · {len(self.conversation.pending())} to answer · "
                    f"{len(self._memories)} to confirm")

    def transcript(self) -> tuple:
        """The conversation so far (``dialogue.Entry``), read under the lock."""
        with self._cond:
            return self.conversation.transcript()

    def pending_probes(self) -> tuple[DialoguePacket, ...]:
        """The questions still waiting for an answer, oldest first."""
        with self._cond:
            return self.conversation.pending()

    def conversation_lines(self) -> list[str]:
        with self._cond:
            return self.conversation.lines()

    def inspector_lines(self) -> list[str]:
        with self._cond:
            return render_lines(self.view)

    def challenge_lines(self, pc: PendingChallenge, now: float | None = None) -> list[str]:
        """What the operator reads before pressing Approve. Everything the node
        sent is cleaned for display; the hash check is this App's own."""
        t = float(now) if now is not None else float(self._clock())
        c, a = pc.challenge, pc.challenge.target_action
        review = review_challenge(c, peer_did=self.peer_did, now=t)
        target = " ".join(p for p in (clean(a.target_role, 40), f'"{clean(a.target_name, 60)}"' if a.target_name
                                      else "", f"#{clean(a.target_id, 40)}" if a.target_id else "") if p)
        lines = [
            f"⚠ {c.risk_level.value.upper()}: {clean(a.kind, 40)} {target}".rstrip(),
            f"  why it is dangerous: {clean(c.risk_explanation, 300)}",
            f"  action hash {review.local_hash[:16]}… "
            + ("(recomputed here: matches)" if review.hash_matches else "(recomputed here: DOES NOT MATCH)"),
            f"  expires in {max(0, int(c.expires_at - t))} s",
        ]
        if a.text:
            lines.insert(1, f"  text: {clean(a.text, 200)}")
        lines.extend(f"  ✗ {p}" for p in review.problems)
        lines.append("  approve (unlocks the operator key for this one signature) or deny" if review.signable
                     else "  cannot be signed: deny only")
        return lines

    def memory_lines(self, m: OfferedMemory) -> list[str]:
        p = m.packet
        lines = [f"remember ({clean(p.mclass, 12)}, {clean(p.scope, 40)}, from {clean(p.source, 16)}): "
                 f"{clean(p.key, 60)} = {clean(p.value, 300)}"]
        lines.extend(f"  ✗ {x}" for x in m.problems)
        return lines

    # -- waiting (for the headless runner and tests) ------------------------------

    def wait_for(self, predicate: Callable[[AppController], object], timeout: float):
        """Block until ``predicate(self)`` is truthy (returned) or ``timeout``
        seconds pass (None). ``predicate`` runs with the controller locked."""
        deadline = time.monotonic() + float(timeout)
        with self._cond:
            while True:
                got = predicate(self)
                if got:
                    return got
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                self._cond.wait(min(left, 0.25))  # also re-checks time-based state

    # -- internals -------------------------------------------------------------------

    def _note(self, text: str) -> None:
        self._events.append(clean(text, 300))

    def _changed(self) -> None:
        self.version += 1
        self._cond.notify_all()


# ---- headless: a script of operator steps ------------------------------------------

DEFAULT_STEP_TIMEOUT = 30.0
_ACTION_FIELDS = ("kind", "target_id", "target_role", "target_name", "text", "high_risk")
_STEP_KEYS = {
    "add_goal": ({"title"}, {"goal_id"}),
    "stop": ({"goal_id"}, set()),
    "pause": ({"goal_id"}, set()),
    "resume": ({"goal_id"}, set()),
    "answer": ({"match"}, {"text", "option"}),
    "approve": ({"action"}, set()),
    "deny": ({"action"}, set()),
    "confirm_memory": ({"key"}, {"value"}),
    "retract_memory": ({"memory_id"}, set()),
    "expect_status": ({"match"}, set()),
    "expect_view": ({"match"}, set()),
    "resync": (set(), set()),
}


class ScriptError(ValueError):
    """A headless script step that is malformed, or failed."""


def check_step(step) -> None:
    """Strict: a known op, its required keys, nothing unknown. An approve or
    deny must name the action -- its kind and what it acts on (a target id or
    name, or for a key press its text) -- so a script can never approve
    "whatever comes"."""
    if not isinstance(step, dict) or not isinstance(step.get("op"), str):
        raise ScriptError("each step is an object with an 'op'")
    op = step["op"]
    if op not in _STEP_KEYS:
        raise ScriptError(f"unknown op {op!r}")
    required, optional = _STEP_KEYS[op]
    keys = set(step) - {"op", "timeout"}
    if required - keys:
        raise ScriptError(f"{op}: missing {sorted(required - keys)}")
    if keys - required - optional:
        raise ScriptError(f"{op}: unknown {sorted(keys - required - optional)}")
    if "timeout" in step and (type(step["timeout"]) not in (int, float) or not 0 < step["timeout"] <= 3600):
        raise ScriptError(f"{op}: timeout must be a number of seconds in (0, 3600]")
    if op == "answer" and ("text" in step) == ("option" in step):
        raise ScriptError("answer: give text or option, exactly one")
    if op in ("approve", "deny"):
        action = step["action"]
        if not isinstance(action, dict) or set(action) - set(_ACTION_FIELDS):
            raise ScriptError(f"{op}: action has fields {list(_ACTION_FIELDS)} only")
        if not action.get("kind") or not (action.get("target_id") or action.get("target_name")
                                          or action.get("text")):
            raise ScriptError(f"{op}: name the action: its kind and its target_id, target_name or text")


def _action_matches(action: dict, pc: PendingChallenge) -> bool:
    a = pc.challenge.target_action
    return all(getattr(a, k) == v for k, v in action.items())


def run_script(ctl: AppController, steps: Iterable[dict], *, unlock: Callable[[], object] | None = None,
               emit: Callable[[dict], None] = lambda r: None, default_timeout: float = DEFAULT_STEP_TIMEOUT) -> int:
    """Run operator steps in order against ``ctl``; ``emit`` gets one result
    object per step. Stops at the first failure. Returns 0 when every step
    succeeded, 1 otherwise. ``unlock`` yields the operator key for an approve
    (None: approve steps fail)."""
    steps = list(steps)
    for i, step in enumerate(steps):
        try:
            check_step(step)
        except ScriptError as e:
            emit({"step": i, "ok": False, "error": str(e)})
            return 1
    seen_status = 0  # expect_status consumes the transcript in order
    for i, step in enumerate(steps):
        op, timeout = step["op"], float(step.get("timeout", default_timeout))
        try:
            result, seen_status = _run_step(ctl, op, step, timeout, unlock, seen_status)
        except (ScriptError, AppError, GuardRefusal, DialogueError) as e:
            emit({"step": i, "op": op, "ok": False, "error": str(e)})
            return 1
        except Exception as e:  # noqa: BLE001 - e.g. a wrong passphrase: report it, stop
            emit({"step": i, "op": op, "ok": False, "error": f"{type(e).__name__}: {e}"})
            return 1
        emit({"step": i, "op": op, "ok": True, **result})
    return 0


def _wait(ctl, pred, timeout, what):
    got = ctl.wait_for(pred, timeout)
    if not got:
        raise ScriptError(f"timed out after {timeout:g} s waiting for {what}")
    return got


def _await_reply(ctl, pkt: ControlPacket, timeout: float) -> dict:
    def done(c):
        r = c._requests.get(pkt.request_id)
        return r if r is not None and (r.reply is not None or r.undelivered) else None

    req = _wait(ctl, done, timeout, f"the node's reply to {pkt.op.value}")
    if req.undelivered:
        raise ScriptError(f"{pkt.op.value} was never delivered")
    out = {"request_id": pkt.request_id, "reply": req.reply}
    if req.reply.startswith("refused"):
        raise ScriptError(f"the node refused {pkt.op.value}: {req.reply}")
    return out


def _run_step(ctl: AppController, op, step, timeout, unlock, seen_status):
    if op == "add_goal":
        pkt = ctl.add_goal(step["title"], step.get("goal_id"))
        return {"goal_id": pkt.goal_id, **_await_reply(ctl, pkt, timeout)}, seen_status
    if op in ("stop", "pause", "resume"):
        pkt = getattr(ctl, op)(step["goal_id"])
        return _await_reply(ctl, pkt, timeout), seen_status
    if op == "retract_memory":
        return _await_reply(ctl, ctl.retract_memory(step["memory_id"]), timeout), seen_status
    if op == "resync":
        ctl.request_resync()
        return {}, seen_status
    if op == "answer":
        probe = _wait(ctl, lambda c: next((p for p in c.conversation.pending() if step["match"] in p.content), None),
                      timeout, f"a question containing {step['match']!r}")
        if "option" in step:
            ctl.answer(probe.probe_id, option=step["option"])
        else:
            ctl.answer(probe.probe_id, step["text"])
        return {"probe_id": probe.probe_id, "question": probe.content}, seen_status
    if op in ("approve", "deny"):
        pc = _wait(ctl, lambda c: next((p for p in c._challenges.values() if _action_matches(step["action"], p)),
                                       None), timeout, f"a Gate 2 challenge for {step['action']}")
        if op == "deny":
            ctl.deny(pc.challenge.challenge_id)
        else:
            if unlock is None:
                raise ScriptError("approve needs the operator key: no keystore / passphrase configured")
            ctl.approve(pc.challenge.challenge_id, unlock)
        return {"challenge_id": pc.challenge.challenge_id, "action_hash": pc.review.local_hash}, seen_status
    if op == "confirm_memory":
        def offered(c):
            return next((m for m in c._memories.values() if m.packet.key == step["key"]
                         and ("value" not in step or m.packet.value == step["value"])), None)

        m = _wait(ctl, offered, timeout, f"an offered memory with key {step['key']!r}")
        pkt = ctl.confirm_memory(m.packet.memory_id)
        return {"memory_id": pkt.memory_id, **_await_reply(ctl, pkt, timeout)}, seen_status
    if op == "expect_status":
        def found(c):
            entries = c.conversation.transcript()
            for j in range(seen_status, len(entries)):
                e = entries[j]
                if e.kind is DialogueType.SYSTEM_STATUS and step["match"] in e.text:
                    return (j, e.text)
            return None

        j, text = _wait(ctl, found, timeout, f"a status containing {step['match']!r}")
        return {"status": text}, j + 1
    if op == "expect_view":
        line = _wait(ctl, lambda c: next((ln for ln in render_lines(c.view) if step["match"] in ln), None),
                     timeout, f"the view to show {step['match']!r}")
        return {"line": line}, seen_status
    raise ScriptError(f"unknown op {op!r}")  # unreachable: check_step ran first


__all__ = [
    "AppController",
    "AppError",
    "OfferedMemory",
    "PendingChallenge",
    "Request",
    "ScriptError",
    "check_step",
    "describe",
    "review_memory",
    "run_script",
    "short_did",
]
