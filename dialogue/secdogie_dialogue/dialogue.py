"""Socratic Dialogue Channel: the probe / clarification loop, both ends.

When a gate finds a step ambiguous, under-observed or unproven, the Agent does
not guess and does not carry on quietly: it opens a *probe* -- a
``SocraticQuestion`` with a ``probe_id`` and optional suggested answers -- and
waits. Only an explicit ``UserClarification`` naming that probe resolves it. If
none comes in time the probe expires, and an expired probe is a "no": the step
stays suspended. There is no implicit yes, no default answer, and a late answer
never revives an expired probe.

  * ``ProbeLedger`` (Agent side) opens probes, resolves them from
    clarifications, expires them, and lets the loop block on one.
  * ``Conversation`` (App side) keeps the transcript and the pending probes, and
    turns the operator's answer into a clarification packet.

The Agent closes a probe on the App's screen with a ``SystemStatus`` whose
``in_reply_to`` names it (accepted, or expired). Packets arrive here already
authenticated by ``protocol.open_envelope``; this module only keeps state.
Pure given a clock; unit-tested headless.
"""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from enum import Enum

from .inspector import clean
from .protocol import DialoguePacket, DialogueType

DEFAULT_PROBE_TTL = 300.0


class DialogueError(ValueError):
    """An answer the App refuses to send (unknown probe, bad option, empty)."""


def _new_id() -> str:
    return secrets.token_hex(8)


def system_status(text: str, *, about: str = "") -> DialoguePacket:
    """A status line from the Agent; ``about`` names the probe it closes, if any."""
    return DialoguePacket(_new_id(), DialogueType.SYSTEM_STATUS, text, in_reply_to=about)


# ---- Agent side -------------------------------------------------------------


class ProbeStatus(str, Enum):
    OPEN = "open"
    ANSWERED = "answered"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Probe:
    probe_id: str
    question: str
    options: tuple[str, ...]
    gate_finding: str
    expires_at: float


@dataclass(frozen=True)
class Resolution:
    probe_id: str
    answer: str
    answered_by: str  # the DID that signed the clarification
    answered_at: float


class ProbeLedger:
    """The Agent's open questions. Thread-safe: the loop blocks in ``wait`` while
    the session thread calls ``resolve``."""

    def __init__(self, *, ttl: float = DEFAULT_PROBE_TTL, clock=time.time, id_factory=_new_id):
        self._ttl = float(ttl)
        self._clock = clock
        self._new_id = id_factory
        self._probes: dict[str, Probe] = {}
        self._status: dict[str, ProbeStatus] = {}
        self._answers: dict[str, Resolution] = {}
        self._cond = threading.Condition()

    def open(self, question: str, *, options: tuple[str, ...] = (), gate_finding: str = "",
             ttl: float | None = None) -> DialoguePacket:
        """Record a new probe and return the ``SocraticQuestion`` to send."""
        pid = self._new_id()
        expires = float(self._clock()) + float(ttl if ttl is not None else self._ttl)
        with self._cond:
            if pid in self._probes:
                raise ValueError(f"probe id {pid!r} already used")
            self._probes[pid] = Probe(pid, question, tuple(options), gate_finding, expires)
            self._status[pid] = ProbeStatus.OPEN
        return DialoguePacket(pid, DialogueType.SOCRATIC_QUESTION, question,
                              suggested_options=tuple(options), gate_finding=gate_finding)

    def resolve(self, pkt: DialoguePacket, *, answered_by: str, now: float | None = None) -> Resolution | None:
        """Resolve the probe ``pkt`` answers. Returns None -- and changes nothing
        -- unless ``pkt`` is a non-empty clarification of a probe that is still
        open and not past its expiry."""
        if pkt.dialogue_type is not DialogueType.USER_CLARIFICATION or not pkt.content.strip():
            return None
        t = float(now) if now is not None else float(self._clock())
        with self._cond:
            pid = pkt.in_reply_to
            if self._status.get(pid) is not ProbeStatus.OPEN:
                return None  # unknown, already answered, or expired: a late answer revives nothing
            if t >= self._probes[pid].expires_at:
                self._status[pid] = ProbeStatus.EXPIRED
                self._cond.notify_all()
                return None
            res = Resolution(pid, pkt.content, answered_by, t)
            self._status[pid] = ProbeStatus.ANSWERED
            self._answers[pid] = res
            self._cond.notify_all()
            return res

    def expire(self, now: float | None = None) -> tuple[str, ...]:
        """Expire every open probe past its deadline; return their ids."""
        t = float(now) if now is not None else float(self._clock())
        with self._cond:
            gone = tuple(pid for pid, st in self._status.items()
                         if st is ProbeStatus.OPEN and t >= self._probes[pid].expires_at)
            for pid in gone:
                self._status[pid] = ProbeStatus.EXPIRED
            if gone:
                self._cond.notify_all()
        return gone

    def wait(self, probe_id: str, timeout: float) -> Resolution | None:
        """Block until ``probe_id`` is answered, or ``timeout`` seconds pass.
        On timeout the probe is expired (fail closed) and None is returned."""
        deadline = time.monotonic() + float(timeout)
        with self._cond:
            while self._status.get(probe_id) is ProbeStatus.OPEN:
                left = deadline - time.monotonic()
                if left <= 0:
                    self._status[probe_id] = ProbeStatus.EXPIRED
                    break
                self._cond.wait(left)
            return self._answers.get(probe_id)

    def status(self, probe_id: str) -> ProbeStatus:
        with self._cond:
            return self._status.get(probe_id, ProbeStatus.UNKNOWN)

    def pending(self) -> tuple[Probe, ...]:
        with self._cond:
            return tuple(self._probes[p] for p, st in self._status.items() if st is ProbeStatus.OPEN)


# ---- App side ---------------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    who: str  # "agent", "you", or "app" (a note from this App itself)
    kind: DialogueType
    text: str
    probe_id: str = ""  # the probe this entry asks, answers or closes
    options: tuple[str, ...] = ()


class Conversation:
    """The operator's side of the dialogue: what was said, and which probes are
    still waiting for an answer."""

    def __init__(self):
        self._entries: list[Entry] = []
        self._pending: dict[str, DialoguePacket] = {}  # arrival order
        self._seen: set[str] = set()

    def receive(self, pkt: DialoguePacket) -> bool:
        """Take a packet from the Agent. Returns False for anything the Agent
        has no business sending (a clarification) or a repeated probe."""
        if pkt.dialogue_type is DialogueType.SOCRATIC_QUESTION:
            if pkt.probe_id in self._seen:
                return False
            self._seen.add(pkt.probe_id)
            self._pending[pkt.probe_id] = pkt
            self._entries.append(Entry("agent", pkt.dialogue_type, pkt.content, pkt.probe_id,
                                       pkt.suggested_options))
            return True
        if pkt.dialogue_type is DialogueType.SYSTEM_STATUS:
            if pkt.in_reply_to:
                self._pending.pop(pkt.in_reply_to, None)  # the Agent closed that probe
            self._entries.append(Entry("agent", pkt.dialogue_type, pkt.content, pkt.in_reply_to))
            return True
        return False

    def pending(self) -> tuple[DialoguePacket, ...]:
        return tuple(self._pending.values())

    def drop_pending(self, note: str) -> tuple[str, ...]:
        """Close every open probe locally (the node went away and has failed
        them on its side); ``note`` is recorded once. Returns the dropped ids."""
        gone = tuple(self._pending)
        self._pending.clear()
        if gone:
            self._entries.append(Entry("app", DialogueType.SYSTEM_STATUS, note))
        return gone

    def answer(self, probe_id: str, text: str | None = None, *, option: int | None = None) -> DialoguePacket:
        """The operator's clarification of ``probe_id``: free text, or the
        1-based number of one of the suggested options. Exactly one of the two."""
        probe = self._pending.get(probe_id)
        if probe is None:
            raise DialogueError(f"no pending probe {probe_id!r}")
        if (text is None) == (option is None):
            raise DialogueError("answer with text or with an option number, not both or neither")
        if option is not None:
            if not 1 <= option <= len(probe.suggested_options):
                raise DialogueError(f"option {option} is not one of the {len(probe.suggested_options)} offered")
            text = probe.suggested_options[option - 1]
        if not text.strip():
            raise DialogueError("an empty answer clarifies nothing")
        del self._pending[probe_id]
        self._entries.append(Entry("you", DialogueType.USER_CLARIFICATION, text, probe_id))
        return DialoguePacket(_new_id(), DialogueType.USER_CLARIFICATION, text, in_reply_to=probe_id)

    def transcript(self) -> tuple[Entry, ...]:
        return tuple(self._entries)

    def lines(self) -> list[str]:
        """Plain-text transcript. Agent text is untrusted (it may
        quote other applications' UI), so every line is cleaned."""
        out: list[str] = []
        for e in self._entries:
            who = {"agent": "Agent", "you": "You"}.get(e.who, "App")
            out.append(f"{who}: {clean(e.text, 500)}")
            if e.kind is DialogueType.SOCRATIC_QUESTION:
                out.extend(f"  [{i}] {clean(o)}" for i, o in enumerate(e.options, 1))
        return out


__all__ = [
    "DEFAULT_PROBE_TTL",
    "DialogueError",
    "system_status",
    "ProbeStatus",
    "Probe",
    "Resolution",
    "ProbeLedger",
    "Entry",
    "Conversation",
]
