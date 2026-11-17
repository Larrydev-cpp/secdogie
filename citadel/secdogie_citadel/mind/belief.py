"""What the companion does not know, and how sure it is.

A consequence usually turns on something no one has said:

- ``later``: will they still want it once this moment has passed, or be sorry?
- ``copy``: is there another copy, or is this the only one?
- ``party``: is the other side who they say they are?
- the **goal**: what the person is after.

Each unknown has a prior for each act, and **cues**: facts of the scene that make one answer likelier, as
likelihood ratios. The facts come from the cortex's answers with how sure it is of each, so a cue counts in
proportion: a ratio r on a fact the cortex is 60% sure of counts as r^0.6. Belief is Bayes: prior odds times
the ratios. The goal is the cortex's own answer to "why?", gathered onto the act's goals.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .world import Fact, Goal, Scene


@dataclass(frozen=True)
class Unknown:
    id: str
    fine: str  # the answer that makes going ahead harmless
    feared: str  # the answer that makes it costly
    prior: dict  # act id -> P(feared)
    zh: str
    en: str
    fine_zh: str = ""  # the two answers, as the Gate page labels them
    fine_en: str = ""
    feared_zh: str = ""
    feared_en: str = ""


UNKNOWNS = {u.id: u for u in (
    Unknown("later", "same", "sorry", {"removes": 0.12, "erases": 0.6, "closes": 0.25, "says": 0.06},
            "过后还会不会这样想", "whether you will still want it later", "过后还是这样想", "you still want it later",
            "过后会后悔", "you are sorry later"),
    Unknown("copy", "copy", "nocopy", {"removes": 0.75, "erases": 0.8, "closes": 0.9},
            "别处还有没有一份", "whether there is another copy", "别处还有一份", "there is another copy",
            "这是唯一的一份", "this is the only copy"),
    Unknown("party", "honest", "hostile", {"pays": 0.03, "lends": 0.05, "tells": 0.25, "runs": 0.05},
            "对方可不可信", "whether the other side can be trusted", "对方可信", "they can be trusted",
            "对方不可信", "they can't be trusted"),
)}

Ratio = Callable[[Scene, Fact], float]


@dataclass(frozen=True)
class Cue:
    unknown: str
    fact: str
    ratio: float | Ratio  # toward the feared answer (> 1 raises it)
    acts: tuple[str, ...] = ()  # only for these acts (empty: wherever the unknown is in play)
    proxy: bool = False  # an indirect sign, which the person's own settled word screens off while they are calm


SCREENED = 0.3
_GONE = ("removes", "erases", "closes")
_MONEY = ("pays", "lends", "tells", "runs")
CUES = (
    Cue("later", "upset", lambda s, f: 1 + 9 * f.level),
    Cue("later", "late", lambda s, f: 1.6 if s.upset > 0.2 else 1.0),
    Cue("later", "in_use", 8.0, ("removes",), proxy=True),
    Cue("later", "dear", 2.5, ("removes", "closes"), proxy=True),
    Cue("later", "work", 1.5, ("removes",), proxy=True),
    Cue("later", "hurtful", 3.0, ("says",), proxy=True),
    Cue("later", "undone", 1.4, _GONE),
    Cue("later", "considered", lambda s, f: 0.25 + 0.6 * s.upset),
    Cue("later", "waited", lambda s, f: 0.3 + 0.5 * s.upset),
    Cue("later", "junk", 0.1, ("removes",)),
    Cue("later", "unused", 0.08, ("removes",)),
    Cue("later", "steady", lambda s, f: 0.7 ** f.level),
    Cue("copy", "copied", 0.03),
    Cue("copy", "junk", 0.1, ("removes",)),
    Cue("copy", "unused", 0.3, ("removes",)),
    Cue("party", "stranger", 20.0, _MONEY),
    Cue("party", "profit", 5.0, ("pays", "lends", "runs")),
    Cue("party", "pushed", 2.0, ("pays", "lends", "tells")),
    Cue("party", "upset", lambda s, f: 1 + 1.5 * f.level if s.has("pushed") else 1.0, ("pays", "lends", "tells")),
    Cue("party", "known", 0.3, _MONEY),
    Cue("party", "checked", 0.1, _MONEY),
    Cue("party", "owed", 0.3, ("pays",)),
    Cue("party", "own", 0.1, _MONEY),
)


@dataclass(frozen=True)
class Belief:
    unknown: Unknown
    p: float  # how likely the feared answer is
    evidence: tuple[tuple[Cue, Fact, float], ...]  # the cues present, the facts they rest on, the ratio each applied

    def of(self, value: str) -> float:
        return self.p if value == self.unknown.feared else 1.0 - self.p


@dataclass(frozen=True)
class GoalBelief:
    goal: Goal
    p: float
    because: tuple[str, ...]  # the "why?" answers it rests on


def believe(s: Scene, uid: str) -> Belief:
    u = UNKNOWNS[uid]
    prior = u.prior.get(s.act.id, 0.1)
    odds = prior / (1 - prior)
    seen = []
    facts = list(s.facts)
    if s.known.steady:
        facts.append(Fact("steady", float(s.known.steady)))
    screen = SCREENED + (1 - SCREENED) * min(1.0, s.upset / 0.3) if (s.considered > 0.5 or s.waited) else 1.0
    for c in CUES:
        if c.unknown != uid or (c.acts and s.act.id not in c.acts):
            continue
        f = next((x for x in facts if x.id == c.fact), None)
        if f is None:
            continue
        r = c.ratio(s, f) if callable(c.ratio) else c.ratio ** min(1.0, f.level)
        if c.proxy:
            r **= screen
        if r != 1.0:
            odds *= r
            seen.append((c, f, r))
    return Belief(u, odds / (1 + odds), tuple(seen))


def goals(s: Scene) -> tuple[GoalBelief, ...]:
    """What they are most likely after: the cortex's answer to "why?", gathered onto this act's goals."""
    out = []
    covered = {w for g in s.act.goals for w in g.why}
    stray = sum(p for w, p in s.why if w not in covered)  # a reason this act has no goal for: its plain one
    for i, g in enumerate(s.act.goals):
        share = sum(p for w, p in s.why if w in g.why) + (stray if i == 0 else 0.0)
        times = s.known.times(s.act.id, g.id)
        out.append((g, share + 0.02 + 0.1 * times, tuple(w for w, p in s.why if w in g.why and p >= 0.2)))
    total = sum(x for _, x, _ in out) or 1.0
    return tuple(sorted((GoalBelief(g, x / total, b) for g, x, b in out), key=lambda b: -b.p))


__all__ = ["Belief", "CUES", "Cue", "GoalBelief", "SCREENED", "UNKNOWNS", "Unknown", "believe", "goals"]
