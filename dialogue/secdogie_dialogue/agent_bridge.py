"""The node's end of the operator dialogue (D7): Gate 2 challenges, Socratic
probes, the operator's answers -- as the hooks a ``Supervisor`` calls.

Three hooks, one session:

  * ``authorize(planned)`` -- a destructive action is about to run. The bridge
    sends a ``Gate2ChallengePacket`` (the action, its risk, the hash the node
    computed, this node's DID, an expiry) and blocks until the App answers or
    the challenge expires. An Approve carries the operator-signed token, which
    is returned for the gate to VERIFY (``action_gate._check_authorization``);
    the bridge never judges a token itself. Deny, timeout, a peer that went
    away, or an answer about a different action -> None -> the gate refuses.
  * ``confirm(prompt, high_risk)`` -- the loop's per-step confirmation. The
    signature IS the confirmation: when the step that just passed the gate is
    a destructive action the operator signed for, that one-shot marker is
    consumed and the step is confirmed without a second question. Anything
    else (a plan approval, a step with no signature) is put to the operator as
    a probe with Approve / Deny, and only a literal "Approve" confirms.
  * ``ask(question)`` -- the model's ask_user, as a Socratic probe. Returns the
    operator's answer text, or None when none came in time.

With a ``SnapshotPublisher`` the bridge also hands the loop's element targets
to it (``on_targets``) and answers the App's RESYNC with a full view.

Fail closed throughout: no answer is a no; a peer reported down fails every
pending challenge and probe at once; a handler exception reaches the loop's
own fail-closed confirm path. Thread-safe: hooks block on the loop thread while
the session thread delivers answers.
"""
from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from secdogie_citadel.authz import action_hash

from .dialogue import ProbeLedger, system_status
from .protocol import (
    DialogueType,
    Envelope,
    Gate2ChallengePacket,
    Gate2ResponsePacket,
    PacketKind,
    RiskLevel,
    SessionEvent,
    TargetAction,
    Verdict,
)
from .session import DialogueSession

DEFAULT_CHALLENGE_TTL = 120.0
DEFAULT_PROBE_TTL = 300.0


@dataclass
class _Challenge:
    key: str
    response: Gate2ResponsePacket | None = None
    dead: bool = False


class OperatorBridge:
    """``identity`` is the node; ``session`` its dialogue session with the App;
    ``operators`` the trust set (allowlist / TrustPolicy) tokens are verified
    against -- required, so a node with no operators can authorize nothing."""

    def __init__(self, identity, session: DialogueSession, *, operators, challenge_ttl: float = DEFAULT_CHALLENGE_TTL,
                 probe_ttl: float = DEFAULT_PROBE_TTL, clock=time.time,
                 on_control: Callable[[object, str], str] | None = None,
                 on_resync: Callable[[], None] | None = None, publisher=None):
        if operators is None:
            raise ValueError("the bridge needs the operator trust set tokens are verified against")
        self.identity = identity
        self.session = session
        self.operators = operators
        self.on_control = on_control
        self.publisher = publisher
        self.on_resync = on_resync if on_resync is not None or publisher is None else publisher.request_full
        self._challenge_ttl = float(challenge_ttl)
        self._probe_ttl = float(probe_ttl)
        self._clock = clock
        self.ledger = ProbeLedger(ttl=probe_ttl, clock=clock)
        self._challenges: dict[str, _Challenge] = {}
        self._cond = threading.Condition()
        self._last_authorized: str | None = None  # action hash the operator just signed for
        self._confirmed_key: str | None = None  # one-shot: the step the signature confirms
        session.on_envelope = self.on_envelope
        session.on_peer_down = self.on_peer_down

    def hooks(self):
        from secdogie_citadel.supervisor import OperatorHooks

        return OperatorHooks(confirm=self.confirm, ask=self.ask, authorize=self.authorize,
                             operators=self.operators, observe=self.observe,
                             on_targets=self.publisher.publish if self.publisher is not None else None)

    # -- Gate 2 -----------------------------------------------------------------

    def authorize(self, planned) -> dict | None:
        key = action_hash(planned)
        intent = getattr(planned, "intent", None)
        irreversible = bool(getattr(intent, "irreversible", False))
        rollback = str(getattr(intent, "rollback", "") or "").strip()
        target = planned.target_name or planned.target_id or "(no target)"
        challenge = Gate2ChallengePacket(
            challenge_id=secrets.token_hex(8),
            target_action=TargetAction.from_action(planned),
            risk_level=RiskLevel.IRREVERSIBLE if irreversible else RiskLevel.HIGH,
            risk_explanation=f"{planned.kind} on {target}; rollback: "
                             + (rollback or ("none: declared irreversible" if irreversible else "none stated")),
            action_hash=key,
            subject_did=self.identity.did,
            expires_at=float(self._clock()) + self._challenge_ttl,
        )
        ch = _Challenge(key)
        with self._cond:
            self._challenges[challenge.challenge_id] = ch
            self._last_authorized = None
        self.session.send(challenge)
        deadline = time.monotonic() + self._challenge_ttl
        with self._cond:
            while ch.response is None and not ch.dead:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                self._cond.wait(left)
            self._challenges.pop(challenge.challenge_id, None)
            resp = ch.response
        if resp is None or resp.user_verdict is not Verdict.APPROVE or resp.action_hash != key:
            return None
        with self._cond:
            self._last_authorized = key
        return resp.authorization

    def observe(self, planned, decision) -> None:
        """After the gate judged ``planned``: the operator's fresh signature for
        exactly this action, plus the gate allowing it, is what confirms the
        step. Any other outcome leaves nothing behind."""
        key = action_hash(planned)
        with self._cond:
            signed = self._last_authorized == key
            self._last_authorized = None
            self._confirmed_key = key if (signed and decision.allowed and planned.destructive) else None

    def confirm(self, prompt: str, high_risk: bool) -> bool:
        with self._cond:
            key, self._confirmed_key = self._confirmed_key, None
        if key is not None:
            return True  # the signature was the confirmation
        return self.ask(prompt, options=("Approve", "Deny")) == "Approve"

    # -- Socratic probes ------------------------------------------------------------

    def ask(self, question: str, *, options: tuple[str, ...] = ()) -> str | None:
        probe = self.ledger.open(question, options=tuple(options))
        self.session.send(probe)
        res = self.ledger.wait(probe.probe_id, timeout=self._probe_ttl)
        if res is None:
            self.session.send(system_status("no answer in time; the step stays suspended", about=probe.probe_id))
            return None
        self.session.send(system_status("answer adopted", about=probe.probe_id))
        return res.answer

    # -- inbound ----------------------------------------------------------------------

    def on_envelope(self, env: Envelope) -> None:
        pkt = env.packet
        if env.kind is PacketKind.DIALOGUE:
            if pkt.dialogue_type is DialogueType.USER_CLARIFICATION:
                self.ledger.resolve(pkt, answered_by=env.signer)
        elif env.kind is PacketKind.GATE2_RESPONSE:
            with self._cond:
                ch = self._challenges.get(pkt.challenge_id)
                if ch is not None and ch.response is None:
                    ch.response = pkt
                    self._cond.notify_all()
        elif env.kind is PacketKind.CONTROL:
            self._control(pkt, env.signer)
        elif env.kind is PacketKind.SESSION:
            if pkt.event is SessionEvent.RESYNC and self.on_resync is not None:
                self.on_resync()
            elif pkt.event is SessionEvent.BYE:
                self.on_peer_down()

    def _control(self, pkt, signer: str) -> None:
        if self.on_control is None:
            reply = "refused: this node takes no control requests"
        else:
            try:
                reply = str(self.on_control(pkt, signer))
            except Exception as e:  # noqa: BLE001 - the operator gets the refusal, the node keeps running
                reply = f"refused: {e}"
        self.session.send(system_status(reply, about=pkt.request_id))

    def on_peer_down(self) -> None:
        """The App is gone: every pending challenge and probe fails now."""
        with self._cond:
            for ch in self._challenges.values():
                ch.dead = True
            self._last_authorized = None
            self._confirmed_key = None
            self._cond.notify_all()
        self.ledger.expire(now=float("inf"))


__all__ = ["OperatorBridge", "DEFAULT_CHALLENGE_TTL", "DEFAULT_PROBE_TTL"]
