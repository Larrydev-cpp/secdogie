"""When the companion shows itself: attention and the stage.

The companion is a presence, not a dashboard. Most of the time all you see is
a faint, breathing light. It speaks when the work needs you: one question,
one signature, one thing it would like to remember. Even then it waits for
a pause in what you are doing. This module decides *when*; the window only
draws what it is told. Pure Python, no Qt: tested headless.

Attention comes from one coarse signal: how many seconds since the last input
anywhere (``idle.py``, the OS's own idle counter). Nothing else is read: not
which keys, not which window, not the screen.

  flow     input with no real pause for a while: hold everything
  engaged  working, with short pauses: hold, wait for a real one
  gap      a pause of a few seconds, or you are looking at the companion: surface
  away     nobody there: hold; what expires, expires (a no)
  unknown  the OS gives no idle signal: surface, gently

A moment marked ``soon`` (the work is waiting on you) still waits for a pause,
but not forever: after ``max_hold`` seconds it surfaces anyway, as a card that
fades in and never takes the keyboard focus. There is no other override. A
signature request that expires unanswered is refused by the node, which is
the fail-closed answer.

One card at a time. A card stays while it still waits for you, so it is never
swapped out mid-reading. Whispers (the companion's one-line notes) show for a
few seconds when no card is up and you are not in flow, then fade. A note that
waited too long is dropped, because it is courtesy, not a record (the journal
is the record).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

FLOW, ENGAGED, GAP, AWAY, UNKNOWN = "flow", "engaged", "gap", "away", "unknown"
REST, THINKING, WAITING, SPEAKING, SIGNING = "rest", "thinking", "waiting", "speaking", "signing"
GAP_URGENCY, SOON = "gap", "soon"


@dataclass(frozen=True)
class AttentionConfig:
    flow_window: float = 20.0  # seconds of input with no real pause = flow
    flow_idle_max: float = 2.0  # a "real pause" is longer than this
    gap_after: float = 4.0  # idle this long = a pause worth using
    away_after: float = 300.0  # idle this long = nobody is there
    looking_lasts: float = 12.0  # after touching the companion, you are attending to it


class Attention:
    def __init__(self, cfg: AttentionConfig | None = None):
        self.cfg = cfg or AttentionConfig()
        self._samples: deque[tuple[float, float | None]] = deque()
        self._looking_until = float("-inf")

    def sample(self, idle_s: float | None, now: float) -> None:
        self._samples.append((now, idle_s))
        horizon = now - max(self.cfg.flow_window * 2, 60.0)
        while self._samples and self._samples[0][0] < horizon:
            self._samples.popleft()

    def touch(self, now: float) -> None:
        """The person is looking at the companion (hover, click)."""
        self._looking_until = now + self.cfg.looking_lasts

    def mode(self, now: float) -> str:
        if now < self._looking_until:
            return GAP
        if not self._samples or self._samples[-1][1] is None:
            return UNKNOWN
        t, idle = self._samples[-1]
        idle += max(0.0, now - t)
        if idle >= self.cfg.away_after:
            return AWAY
        if idle >= self.cfg.gap_after:
            return GAP
        recent = [(s, i) for s, i in self._samples if s >= now - self.cfg.flow_window and i is not None]
        if recent and now - recent[0][0] >= self.cfg.flow_window * 0.75 and \
                all(i < self.cfg.flow_idle_max for _, i in recent):
            return FLOW
        return ENGAGED


@dataclass(frozen=True)
class Moment:
    """Something that needs the person: a card."""

    id: str
    kind: str  # ask | sign | memory | welcome
    says: str
    options: tuple[str, ...] = ()
    details: tuple[tuple[str, str], ...] = ()
    free_text: bool = False
    urgency: str = GAP_URGENCY
    arrived: float = 0.0
    expires: float | None = None
    can_approve: bool = True  # a sign card whose challenge failed the local review offers only "no"
    label: str = ""


@dataclass(frozen=True)
class Whisper:
    """One line in passing; ``tone`` is calm or warm (a step it stopped)."""

    id: str
    text: str
    tone: str = "calm"
    arrived: float = 0.0


@dataclass(frozen=True)
class View:
    card: Moment | None
    whisper: Whisper | None
    mood: str
    held: int  # moments waiting for a pause


class Stage:
    def __init__(self, *, max_hold: float = 30.0, whisper_for: float = 5.0, whisper_stale: float = 120.0):
        self.max_hold = max_hold
        self.whisper_for = whisper_for
        self.whisper_stale = whisper_stale
        self._card: str | None = None
        self._whisper: Whisper | None = None
        self._whisper_since = 0.0
        self._said: set[str] = set()
        self._queue: deque[Whisper] = deque()

    def note(self, whisper: Whisper) -> None:
        if whisper.id not in self._said:
            self._said.add(whisper.id)
            self._queue.append(whisper)

    def _surfaces(self, m: Moment, mode: str, now: float) -> bool:
        if m.kind == "welcome":
            return True
        if mode in (GAP, UNKNOWN):
            return True
        return m.urgency == SOON and mode != AWAY and now - m.arrived >= self.max_hold

    def update(self, moments, mode: str, now: float, *, busy: bool = False) -> View:
        moments = [m for m in moments if m.expires is None or m.expires > now]
        by_id = {m.id: m for m in moments}
        card = by_id.get(self._card)
        if card is None:
            order = sorted(moments, key=lambda m: (m.kind != "welcome", m.urgency != SOON, m.arrived, m.id))
            card = next((m for m in order if self._surfaces(m, mode, now)), None)
        self._card = card.id if card is not None else None

        whisper = None
        if self._whisper is not None and now - self._whisper_since < self.whisper_for and card is None:
            whisper = self._whisper
        elif card is None and mode != FLOW:
            while self._queue:
                w = self._queue.popleft()
                if now - w.arrived <= self.whisper_stale:
                    self._whisper, self._whisper_since, whisper = w, now, w
                    break
        if whisper is None and (card is not None or now - self._whisper_since >= self.whisper_for):
            self._whisper = None

        held = len(moments) - (1 if card is not None else 0)
        if card is not None:
            mood = SIGNING if card.kind == "sign" else SPEAKING
        elif held:
            mood = WAITING
        elif busy:
            mood = THINKING
        else:
            mood = REST
        return View(card, whisper, mood, held)


__all__ = ["AWAY", "Attention", "AttentionConfig", "ENGAGED", "FLOW", "GAP", "GAP_URGENCY", "Moment", "REST",
           "SIGNING", "SOON", "SPEAKING", "Stage", "THINKING", "UNKNOWN", "View", "WAITING", "Whisper"]
