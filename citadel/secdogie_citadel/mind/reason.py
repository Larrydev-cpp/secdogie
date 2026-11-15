"""The reasoning: from what the request says and how the person seems, to what
the companion should do, and why.

1. **Why are they asking?** Bayes over the domain's motives. Each motive's
   prior is multiplied by the likelihood ratio of every piece of evidence
   that is present, then the scores are normalised. The evidence comes from
   the words, the situation and what the request touches. The trace keeps
   what each belief rests on.
2. **What would each response lead to?** For every option, under every
   motive:
   - how well it serves what they want;
   - for an irreversible step, the chance they later wish it undone, times
     what is at stake;
   - the harm to someone else and to the person;
   - the rules it breaks;
   - for anything but what they asked, the cost of overriding them. That
     cost grows with how much their own judgment should count right now:
     it is high when they are calm, have thought it over, or waited out a
     cooling-off; it is lower when they are in a passing state.
3. **Which is best?** Expected utility over the motives. When the best is
   an irreversible step and a reversible option is within a hair of it, the
   reversible one is taken.
4. **What would change my mind?** The motives under which another option
   would be best (what a mistaken reading would cost, as the value of
   knowing the motive for sure), and the motives under which going ahead as
   asked would be best: what the person could say to unlock it.
5. **For and against.** An argument ledger: each consideration, which side
   it is on, and how much it weighs.

The regret rule: people regret what they do in a hot state more than what
they do in a cold one, and they regret losing what is still part of their
life (``knowledge.Motive.regret``, raised by the situation). Saying they have
thought it over lowers it, and so does having waited.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace

from .knowledge import DISSUADE, DOMAINS, NOTHING, PROCEED, REFUSE, Domain, Motive, Option, Things
from .perceive import Frame, Situation

FULFIL, REGRET, OTHERS, SELF, NORM = 1.0, 2.2, 3.0, 2.5, 2.0  # how much each consideration weighs
OVERRIDE = 0.25  # the cost of not doing what they asked, before their autonomy is counted
TIE = 0.05  # within this, a reversible option beats an irreversible one
REFUSE_AT = 0.45  # expected harm to someone else, were it done as asked, at which the companion will not do it
NOTABLE = 0.08  # a motive with less belief than this does not change what the letter says
HOT = 0.3  # upset at least this much: a hot state
COOL_OFF_DAYS, COOL_OFF_CALM_DAYS = 7, 3


@dataclass(frozen=True)
class Belief:
    motive: Motive
    p: float
    because: tuple[str, ...]  # the evidence it rests on, in the companion's words


@dataclass(frozen=True)
class Weighed:
    option: Option
    eu: float  # expected utility
    parts: dict  # consideration -> its expected share of eu (signed)
    by_motive: dict  # motive id -> utility under that motive alone


@dataclass(frozen=True)
class Argument:
    side: str  # "for" or "against" doing it as asked
    key: str  # asked, autonomy, considered, low_stakes, regret, others, self, norm
    weight: float
    drivers: tuple[str, ...] = ()  # what raises it: hot, late, in_use, memories, regret_prone, unknown, ...


@dataclass(frozen=True)
class Thought:
    domain: Domain
    frame: Frame
    situation: Situation
    things: Things
    beliefs: tuple[Belief, ...]  # most likely first
    weighed: tuple[Weighed, ...]  # best first
    value: float
    autonomy: float
    waited: bool
    verdict: str
    voi: float  # what knowing the motive for sure would be worth
    flips: tuple[str, ...]  # notable motives under which another option would be best
    unlocks: tuple[Motive, ...]  # motives under which going ahead as asked would be best
    ledger: tuple[Argument, ...]
    cool_off: float  # seconds until it is weighed again; 0 for none
    harm_to_others: float  # expected harm to someone else, were it done as asked
    keys: tuple[tuple[str, str], ...] = ()  # (motive id, what to say) that was checked to turn it into going ahead

    @property
    def choice(self) -> Option:
        return self.weighed[0].option

    @property
    def top(self) -> Belief:
        return self.beliefs[0]

    @property
    def hot(self) -> bool:
        return self.situation.upset >= HOT

    def weighed_of(self, oid: str) -> Weighed:
        return next(w for w in self.weighed if w.option.id == oid)

    def alternatives(self) -> tuple[Weighed, ...]:
        """The paths other than doing it as asked, best first."""
        return tuple(w for w in self.weighed if w.option.id != "do")

    def against(self) -> tuple[Argument, ...]:
        return tuple(a for a in self.ledger if a.side == "against")

    def journal(self) -> dict:
        r = lambda x: round(x, 3)  # noqa: E731
        return {
            "act": self.domain.act, "value": r(self.value), "upset": r(self.situation.upset),
            "autonomy": r(self.autonomy), "waited": self.waited,
            "motives": [[b.motive.id, r(b.p), list(b.because)] for b in self.beliefs],
            "options": [[w.option.id, r(w.eu), {k: r(v) for k, v in w.parts.items()}] for w in self.weighed],
            "choice": self.choice.id, "verdict": self.verdict, "voi": r(self.voi), "flips": list(self.flips),
            "unlocks": [m.id for m in self.unlocks], "harm_to_others": r(self.harm_to_others),
            "ledger": [[a.side, a.key, r(a.weight), list(a.drivers)] for a in self.ledger],
            "cool_off_days": r(self.cool_off / 86400), "keys": [list(k) for k in self.keys],
        }


# ---- 1. why are they asking ---------------------------------------------------------------------------


def beliefs(d: Domain, f: Frame, s: Situation, t: Things) -> tuple[Belief, ...]:
    scores = []
    for m in d.motives:
        score, why = m.prior, []
        for test, ratio, words in m.evidence:
            if test(f, s, t):
                score *= ratio
                if words and ratio > 1:
                    why.append(words)
        scores.append((m, score, tuple(why)))
    total = sum(sc for _, sc, _ in scores) or 1.0
    out = [Belief(m, sc / total, why) for m, sc, why in scores]
    return tuple(sorted(out, key=lambda b: -b.p))


# ---- 2. what each response leads to -----------------------------------------------------------------


def regret_chance(d: Domain, m: Motive, f: Frame, s: Situation, t: Things, waited: bool) -> float:
    """The chance that, with motive ``m`` and in this situation, they later wish the step undone."""
    rm = m.regret * (1 + (1.5 if m.transient else 0.5) * s.upset)
    rm = min(0.95, rm)
    use = 0.0
    if d.act == "delete" and not t.unused:
        use = 0.55 if t.recent else 0.45 if t.used else 0.0
    kind = 0.3 if d.act == "delete" and not t.unused and (f.has("memories") or t.photos) else 0.0
    r = 1 - (1 - rm) * (1 - use) * (1 - kind)
    # saying they thought it over, or having waited, shows what they settle on; but it counts for little when
    # said in a hot state, or when they are back in one
    if s.considered:
        r *= 0.4 + 0.55 * s.upset
    elif waited:
        r *= 0.6 + 0.4 * s.upset
    if s.regret_prone:
        r = min(0.95, r * 1.4)
    return r


def autonomy(s: Situation, waited: bool) -> float:
    """How much their own say should count right now (0..1): it always counts, and more when it is settled."""
    a = 0.45 + (0.35 * s.considered + 0.25 * waited) * (1 - s.upset) - 0.3 * s.upset
    return max(0.1, min(1.0, a))


def _utility(o: Option, m: Motive, d: Domain, f: Frame, s: Situation, t: Things, value: float,
             waited: bool) -> dict:
    regret = 0.0 if o.reversible else value * regret_chance(d, m, f, s, t, waited) * o.regret_factor
    return {"fulfil": FULFIL * o.fulfil.get(m.id, 0.5), "regret": -REGRET * regret,
            "others": -OTHERS * o.harm_others.get(m.id, 0.0), "self": -SELF * o.harm_self.get(m.id, 0.0) * value,
            "norm": -NORM * o.norm.get(m.id, 0.0)}


def _fixed(o: Option, a: float) -> dict:
    return {"override": -OVERRIDE * (1 + 2 * a) if o.overrides else 0.0, "effort": -o.effort}


# ---- the whole thought ----------------------------------------------------------------------------------


def _next_morning(now: float) -> float:
    lt = time.localtime(now)
    day = 0 if lt.tm_hour < 5 else 1
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + day, 9, 0, 0, 0, 0, -1))


def weigh(d: Domain, f: Frame, s: Situation, t: Things, *, waited: bool = False, now: float | None = None) -> Thought:
    now = time.time() if now is None else now
    bs = beliefs(d, f, s, t)
    value = d.value(f, s, t)
    a = autonomy(s, waited)
    weighed = []
    for o in d.options:
        fixed = _fixed(o, a)
        parts = {k: 0.0 for k in ("fulfil", "regret", "others", "self", "norm")}
        by_motive = {}
        for b in bs:
            u = _utility(o, b.motive, d, f, s, t, value, waited)
            for k, v in u.items():
                parts[k] += b.p * v
            by_motive[b.motive.id] = sum(u.values()) + sum(fixed.values())
        parts.update(fixed)
        weighed.append(Weighed(o, sum(parts.values()), parts, by_motive))
    weighed.sort(key=lambda w: -w.eu)
    if len(weighed) > 1 and not weighed[0].option.reversible and weighed[1].option.reversible \
            and weighed[0].eu - weighed[1].eu < TIE:
        weighed[0], weighed[1] = weighed[1], weighed[0]
    choice = weighed[0]
    do = next(w for w in weighed if w.option.id == "do")

    best = {b.motive.id: max(weighed, key=lambda w: w.by_motive[b.motive.id]) for b in bs}
    voi = sum(b.p * (best[b.motive.id].by_motive[b.motive.id] - choice.by_motive[b.motive.id]) for b in bs)
    flips = tuple(b.motive.id for b in bs if b.p >= NOTABLE and best[b.motive.id].option.id != choice.option.id)
    unlocks = tuple(b.motive for b in bs if choice.option.id != "do" and best[b.motive.id].option.id == "do")

    harm = sum(b.p * do.option.harm_others.get(b.motive.id, 0.0) for b in bs)
    if choice.option.id == "do":
        verdict = PROCEED
    elif harm >= REFUSE_AT:
        verdict = REFUSE
    else:
        verdict = DISSUADE

    cool = 0.0
    if verdict == DISSUADE and d.cool_off:
        if d.act == "message" and s.upset >= HOT:
            cool = max(3600.0, _next_morning(now) - now)
        elif s.upset >= HOT:
            cool = COOL_OFF_DAYS * 86400.0
        elif d.act == "delete" and t.used:
            cool = COOL_OFF_CALM_DAYS * 86400.0

    th = Thought(d, f, s, t, bs, tuple(weighed), value, a, waited, verdict, voi, flips, unlocks, (), cool, harm)
    return _with_ledger(th, do, choice)


def _with_ledger(th: Thought, do: Weighed, choice: Weighed) -> Thought:
    s, t, f = th.situation, th.things, th.frame
    led = [Argument("for", "asked", do.parts["fulfil"])]
    if choice.option.overrides:
        led.append(Argument("for", "autonomy", -choice.parts["override"]))
    if s.considered or th.waited:
        led.append(Argument("for", "considered", 0.4, ("said" if s.considered else "waited",)))
    if th.value < 0.2:
        led.append(Argument("for", "low_stakes", 1.0 - th.value))
    drivers = []
    if not do.option.reversible:
        drivers.append("irreversible")
    if th.hot:
        drivers.append("hot")
    if s.late:
        drivers.append("late")
    if th.domain.act == "delete" and t.used and not t.unused:
        drivers.append("in_use")
    if th.domain.act == "delete" and (f.has("memories") or t.photos) and not t.unused:
        drivers.append("memories")
    if th.domain.act == "wipe" and th.value > 0.5:
        drivers.append("unknown")
    if s.regret_prone:
        drivers.append("regret_prone")
    for key in ("regret", "others", "self", "norm"):
        w = -do.parts[key]
        if w > 0.01:
            led.append(Argument("against", key, w, tuple(drivers) if key == "regret" else ()))
    led.sort(key=lambda a: -a.weight)
    return replace(th, ledger=tuple(led))


def think(f: Frame, s: Situation, t: Things = NOTHING, *, act: str = "", waited: bool = False,
          now: float | None = None) -> Thought | None:
    """The thought about a weighty request, or None when nothing in it is weighty."""
    d = DOMAINS.get(act or f.act)
    if d is None or f.negated or f.question:
        return None
    return weigh(d, f, s, t, waited=waited, now=now)


__all__ = ["Argument", "Belief", "COOL_OFF_CALM_DAYS", "COOL_OFF_DAYS", "HOT", "Thought", "Weighed", "autonomy",
           "beliefs", "regret_chance", "think", "weigh"]
