"""Thinking it through: the ways forward are generated, each is played out
in every way the unknown things could turn out, and the best is chosen.

1. **Ways forward** (``Step``). Besides doing what was asked and not doing
   it, a few general moves, each offered only where the act allows it:
   find something out first (who the other side is; whether the wish
   holds, by waiting), keep a copy first, do something gentler that still
   gets what the person is after, do less of it. None is written for a
   particular request.
2. **Worlds.** Every combination of answers to the unknowns in play
   (``belief``), each with its probability.
3. **Playing it out.** In each world the step's effects are added to the
   scene and the rules run until nothing new follows (``world.closure``).
   What holds at the end is valued: what the person was after, what they
   lose, what they will regret, what falls on someone else, what rule is
   broken.
4. **Finding out first.** A step that reveals an unknown is valued by what
   would be done once it is known: go ahead where that is best, stop where
   it is not. So the worth of asking, of waiting, of checking, is computed,
   not assumed; a cooling-off is what comes out when waiting is worth more
   than acting now.
5. **Choosing.** The highest expected value. What would fall on someone who
   has not agreed is not traded against anything: past a threshold, the
   act as asked is refused.
6. **What would change the answer** (``turns``): for each unknown, how sure
   the mind would have to be before it went ahead.

Every number is here or in ``world`` / ``belief``, and the same input always
gives the same thought.
"""
from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, replace

from .belief import UNKNOWNS, Belief, GoalBelief, believe, goals
from .world import ACTS, COSTS, NOKNOWN, NOTHING, Known, Rule, Scene, Things, closure, scene

PROCEED, DISSUADE, REFUSE = "proceed", "dissuade", "refuse"
FULFIL, REGRET, OTHERS, SELF, NORM = 1.0, 2.2, 3.0, 2.5, 2.0  # what each consideration weighs
OVERRIDE = 0.25  # the cost of not doing what they asked, before their autonomy is counted
TIE = 0.05  # within this, a step that can be undone beats one that cannot
REFUSE_AT = 0.45  # expected harm to someone else, were it done as asked, at which the companion will not do it
HOT = 0.3  # upset at least this much: a hot state
COOL_OFF_DAYS, COOL_OFF_CALM_DAYS = 7, 3
SHRUNK = 0.3  # "do less of it": this share of what was asked


@dataclass(frozen=True)
class Step:
    """One way forward."""

    id: str
    adds: tuple[str, ...] = ()  # what it makes true
    does: bool = False  # the act asked for is still carried out (after, or along with, this step)
    reveals: str = ""  # the unknown it finds out before deciding
    depart: float = 1.0  # how far it departs from what was asked (0: it is what was asked)
    effort: float = 0.0  # the delay and work it adds
    zh: str = ""  # its name; {verb} and {what} are filled in
    en: str = ""
    head_zh: str = ""  # the first line of the letter when it is chosen
    head_en: str = ""
    say_zh: str = ""  # what the person can say to choose it
    say_en: str = ""
    how_zh: str = ""  # for a step that finds something out: how
    how_en: str = ""


STEPS = {s.id: s for s in (
    Step("do", does=True, depart=0.0, zh="直接{verb}", en="{verb} it now"),
    Step("decline", zh="先不{verb}", en="not {verb} it", head_zh="{what}，我先不{verb}。", head_en="I won't {verb} {what} just yet."),
    Step("hide", ("hidden",), depart=0.8, effort=0.05, zh="封存起来", en="archive it out of sight",
         head_zh="{what}，我想先替你收起来，不{verb}。", head_en="I'd like to put {what} away for you rather than {verb} it.",
         say_zh="把{thing}封存起来", say_en="Archive {thing}"),
    Step("pause", ("hidden",), depart=0.8, effort=0.05, zh="先停用一阵", en="step away from it for a while",
         head_zh="{what}，我想先替你停用一阵，不{verb}。", head_en="I'd like to pause {what} for you rather than {verb} it.",
         say_zh="先退出登录、停用一阵", say_en="Sign out and leave it for a while"),
    Step("lock", ("locked",), depart=0.8, effort=0.1, zh="压缩、加密", en="compress and encrypt it",
         head_zh="{what}，我想先替你锁起来，不{verb}。", head_en="I'd like to lock {what} away for you rather than {verb} it.",
         say_zh="把{thing}加密收起来", say_en="Encrypt {thing}"),
    Step("move_out", ("moved_out",), depart=0.8, effort=0.15, zh="挪到移动硬盘或网盘", en="move it to an external drive or the cloud",
         head_zh="{what}，我想先替你挪到别处，不{verb}。", head_en="I'd like to move {what} elsewhere for you rather than {verb} it.",
         say_zh="把{thing}挪到移动硬盘", say_en="Move {thing} to an external drive"),
    Step("copy_first", ("copied",), does=True, depart=0.2, effort=0.2, zh="先留一份副本，再{verb}", en="keep a copy, then {verb} it",
         head_zh="{verb}{what}之前，我想先留一份副本。", head_en="Before I {verb} {what}, I'd like to keep a copy.",
         say_zh="先备份", say_en="Back it up first"),
    Step("wait", reveals="later", depart=0.3, effort=0.1, zh="先等一等，再决定", en="wait a while, then decide",
         head_zh="{what}，我想先等一等。", head_en="I'd like to wait a little before I {verb} {what}.",
         how_zh="等一等，时间会说明", how_en="A little time will tell"),
    Step("sleep_on", ("drafted",), reveals="later", depart=0.3, effort=0.1, zh="先拟好，明早再{verb}", en="draft it and {verb} it in the morning",
         head_zh="{what}，我先替你留到明天早上。", head_en="I'll keep {what} for you until tomorrow morning.",
         say_zh="先拟好，明天早上再发", say_en="Draft it and send it tomorrow morning", how_zh="睡一觉再看", how_en="Sleep on it"),
    Step("soften", ("softened",), does=True, depart=0.4, effort=0.1, zh="把话说得不伤人", en="say it without wounding",
         head_zh="{what}，我想先把话改得平和一点。", head_en="I'd like to make {what} calmer first.",
         say_zh="帮我改得平和一点再发", say_en="Make it calmer, then send it"),
    Step("verify", reveals="party", depart=0.2, effort=0.15, zh="先核实对方", en="check who they are first",
         head_zh="{what}，我先不{verb}：先核实对方。", head_en="I won't {verb} {what} yet: let's check who they are first.",
         say_zh="我先打电话确认一下", say_en="I'll call them first to check",
         how_zh="用你自己存的号码打过去，或者当面问一句", how_en="Call a number you saved yourself, or ask in person"),
    Step("trace", reveals="party", depart=0.2, effort=0.15, zh="先查清它从哪儿来", en="find out where it comes from first",
         head_zh="{what}，我先不{verb}：先查清它从哪儿来。", head_en="I won't {verb} {what} yet: let me find out where it comes from.",
         say_zh="先帮我查查它是从哪儿来的", say_en="Find out where it comes from first",
         how_zh="我先只看它要做什么，不运行它；要用的话，从官网或者应用商店下正版",
         how_en="I look at what it would do without running it; if you need it, the real one comes from the official site or the app store"),
    Step("shrink", ("smaller",), does=True, depart=0.5, effort=0.05, zh="只{verb}一小笔，要不回来也不心疼", en="{verb} only a little, an amount you could lose",
         head_zh="{what}，我想先只{verb}一小笔。", head_en="I'd like to {verb} only a little of {what} first.",
         say_zh="先少一点", say_en="A smaller amount first"),
    Step("direct", ("direct",), depart=0.6, effort=0.1, zh="当面或者打电话说", en="say it in person or on a call",
         head_zh="{what}，我先不替你{verb}：当面说更稳妥。", head_en="I won't {verb} {what} for you here: in person is safer.",
         say_zh="我当面告诉他", say_en="I'll tell them in person"),
    Step("ask_them", ("asked_them",), depart=0.8, effort=0.1, zh="当面问清楚", en="ask them directly",
         head_zh="我不{verb}；我们想想怎么当面问。", head_en="I won't {verb} {what}. Let's work out how to ask them.",
         say_zh="帮我想想怎么开口问", say_en="Help me work out how to ask"),
    Step("share", ("shared",), depart=0.7, effort=0.1, zh="说好以后共享位置", en="agree to share locations",
         head_zh="我不{verb}；说好了共享位置，比偷偷看好。", head_en="I won't {verb} {what}. Agreeing to share locations is better.",
         say_zh="帮我设置共享位置", say_en="Help me set up location sharing"),
    Step("ask_around", ("asked_around",), depart=0.6, zh="先找人，真联系不上就报警", en="reach the people around them, and call the police if you truly can't",
         head_zh="我先不{verb}，我们先把人找到。", head_en="I won't {verb} {what}. Let's find them first."),
    Step("proper", ("each_own",), depart=0.8, zh="换个正当的办法", en="find a proper way",
         head_zh="{verb}{what}，我先不做。", head_en="I won't {verb} {what} just yet."),
)}


@dataclass(frozen=True)
class Outcome:
    """One step, played out in one world."""

    world: tuple[tuple[str, str], ...]  # (unknown, answer) pairs, the goal last
    p: float
    u: float  # its value, before the step's own cost
    parts: dict
    chain: tuple[tuple[Rule, float], ...]  # the rules that fired, in order
    then: str = ""  # for a step that finds something out: what is done once it is known


@dataclass(frozen=True)
class Weighed:
    step: Step
    eu: float
    parts: dict  # consideration -> its expected share of eu (signed)
    outcomes: tuple[Outcome, ...]

    @property
    def undoable(self) -> bool:
        return not self.step.does


@dataclass(frozen=True)
class Turn:
    """How sure the mind would have to be about one unknown before it went ahead."""

    unknown: str
    now: float  # how likely the harmless answer is now
    needed: float  # ... and how likely it would have to be (1.01: nothing about this unknown changes the answer)


@dataclass(frozen=True)
class Thought:
    scene: Scene
    beliefs: tuple[Belief, ...]  # the unknowns in play
    goals: tuple[GoalBelief, ...]  # likeliest first
    weighed: tuple[Weighed, ...]  # best first
    autonomy: float
    verdict: str
    cool_off: float  # seconds until it is weighed again; 0 for none
    harm_to_others: float  # expected harm to someone else, were it done as asked
    turns: tuple[Turn, ...] = ()
    keys: tuple[tuple[str, str], ...] = ()  # (what it shows, what to say) that was checked to turn it into going ahead
    acts: tuple[str, ...] = ()  # every act in the request; this thought is about the weightiest

    @property
    def act(self) -> str:
        return self.scene.act.id

    @property
    def value(self) -> float:
        return self.scene.stake

    @property
    def situation(self):
        return self.scene.situation

    @property
    def choice(self) -> Step:
        return self.weighed[0].step

    @property
    def goal(self) -> GoalBelief:
        return self.goals[0]

    @property
    def hot(self) -> bool:
        return self.scene.situation.upset >= HOT

    def of(self, sid: str) -> Weighed:
        return next(w for w in self.weighed if w.step.id == sid)

    def belief(self, uid: str) -> Belief | None:
        return next((b for b in self.beliefs if b.unknown.id == uid), None)

    def journal(self) -> dict:
        r = lambda x: round(x, 3)  # noqa: E731
        s = self.scene
        return {
            "act": self.act, "value": r(self.value), "upset": r(s.situation.upset), "autonomy": r(self.autonomy),
            "waited": s.waited, "facts": [[f.id, f.words] for f in s.facts],
            "unknowns": [[b.unknown.id, r(b.p), [[c.fact, r(ratio), f.words] for c, f, ratio in b.evidence]]
                         for b in self.beliefs],
            "motives": [[g.goal.id, r(g.p), list(g.because)] for g in self.goals],
            "options": [[w.step.id, r(w.eu), {k: r(v) for k, v in w.parts.items()}] for w in self.weighed],
            "worlds": {w.step.id: [[dict(o.world), r(o.p), r(o.u), [x.id for x, _ in o.chain], o.then]
                                   for o in w.outcomes if o.p >= 0.005] for w in self.weighed},
            "choice": self.choice.id, "verdict": self.verdict, "harm_to_others": r(self.harm_to_others),
            "turns": [[t.unknown, r(t.now), r(t.needed)] for t in self.turns],
            "cool_off_days": r(self.cool_off / 86400), "keys": [list(k) for k in self.keys],
            "known": {"held": s.known.held, "steady": s.known.steady, "undone": s.known.undone,
                      "goals": [list(g) for g in s.known.goals if g[0] == self.act]},
            "acts": list(self.acts or (s.frame.act,)),
        }


# ---- playing one step out in one world -------------------------------------------------------------------


def autonomy(s: Scene) -> float:
    """How much their own say should count right now (0..1): it always counts, and more when it is settled, or
    when their settled choices have held before."""
    sit = s.situation
    a = 0.45 + (0.35 * sit.considered + 0.25 * s.waited + s.known.autonomy_lift) * (1 - sit.upset) - 0.3 * sit.upset
    return max(0.1, min(1.0, a))


def play(s: Scene, step: Step, world: dict) -> tuple[float, dict, tuple]:
    """(value, its parts, the chain of rules) of ``step`` in ``world``, before the step's own cost."""
    atoms = dict(s.atoms())
    atoms.update({a: 1.0 for a in step.adds})
    if step.does:
        atoms[s.act.effect] = 1.0
    atoms.update({v: 1.0 for k, v in world.items() if k != "goal"})
    atoms[f"goal:{world['goal'].id}"] = 1.0
    state, chain = closure(atoms)
    stake = s.stake * (SHRUNK if "smaller" in atoms else 1.0)
    parts = {"fulfil": FULFIL * state.get(world["goal"].atom, 0.0) * (SHRUNK ** 0.5 if "smaller" in atoms else 1.0),
             "regret": 0.0, "self": 0.0, "others": 0.0, "norm": 0.0}
    worst: dict[str, float] = {}
    for atom, (kind, weight) in COSTS.items():
        worst[kind] = max(worst.get(kind, 0.0), state.get(atom, 0.0) * weight)
    parts["regret"] = -REGRET * stake * worst.get("regret", 0.0)
    parts["self"] = -SELF * stake * worst.get("self", 0.0)
    parts["others"] = -OTHERS * worst.get("others", 0.0)
    parts["norm"] = -NORM * worst.get("norm", 0.0)
    return sum(parts.values()), parts, chain


def _worlds(beliefs, gs) -> list[tuple[dict, float]]:
    out = []
    answers = [((b.unknown.id, b.unknown.fine, 1 - b.p), (b.unknown.id, b.unknown.feared, b.p)) for b in beliefs]
    for combo in itertools.product(*answers):
        p = 1.0
        world = {}
        for uid, value, pv in combo:
            world[uid] = value
            p *= pv
        for g in gs:
            if p * g.p > 0:
                out.append(({**world, "goal": g.goal}, p * g.p))
    return out


def _label(world: dict) -> tuple[tuple[str, str], ...]:
    return tuple((k, v) for k, v in world.items() if k != "goal") + (("goal", world["goal"].id),)


def weigh_step(s: Scene, step: Step, worlds, a: float) -> Weighed:
    fixed = {"override": -OVERRIDE * (1 + 2 * a) * step.depart, "effort": -step.effort}
    parts = {k: 0.0 for k in ("fulfil", "regret", "self", "others", "norm")}
    outcomes = []
    if not step.reveals:
        for world, p in worlds:
            u, ps, chain = play(s, step, world)
            for k, v in ps.items():
                parts[k] += p * v
            outcomes.append(Outcome(_label(world), p, u, ps, chain))
    else:  # find it out, then do what is best once it is known
        u_spec = UNKNOWNS[step.reveals]
        do, stop = replace(STEPS["do"], adds=step.adds), replace(STEPS["decline"], adds=step.adds)
        for answer in (u_spec.fine, u_spec.feared):
            given = [(w, p) for w, p in worlds if w[step.reveals] == answer]
            total = sum(p for _, p in given)
            if total <= 0:
                continue
            played = {st.id: [(w, p, *play(s, st, w)) for w, p in given] for st in (do, stop)}
            best = max(("do", "decline"), key=lambda sid: (sum(p * u for _, p, u, _, _ in played[sid]), sid == "decline"))
            for w, p, u, ps, chain in played[best]:
                for k, v in ps.items():
                    parts[k] += p * v
                outcomes.append(Outcome(_label(w), p, u, ps, chain, then=best))
    parts.update(fixed)
    return Weighed(step, sum(parts.values()), parts, tuple(outcomes))


def _next_morning(now: float) -> float:
    lt = time.localtime(now)
    day = 0 if lt.tm_hour < 5 else 1
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + day, 9, 0, 0, 0, 0, -1))


def _steps(s: Scene) -> list[Step]:
    """The ways forward this act allows."""
    ids = list(s.act.steps)
    if s.act.id == "close_account":
        ids = ["pause" if i == "hide" else i for i in ids]
    if s.act.effect == "told" and "wait" in ids:
        ids = ["sleep_on" if i == "wait" else i for i in ids]
    if s.act.id == "run_unknown":
        ids = ["trace" if i == "verify" else i for i in ids]
    return [STEPS["do"]] + [STEPS[i] for i in ids] + [STEPS["decline"]]


def _choose(s: Scene, beliefs, gs, a: float) -> tuple[Weighed, ...]:
    worlds = _worlds(beliefs, gs)
    weighed = [weigh_step(s, st, worlds, a) for st in _steps(s)]
    weighed.sort(key=lambda w: -w.eu)
    if len(weighed) > 1 and weighed[0].step.id == "do" and weighed[0].eu - weighed[1].eu < TIE:
        weighed[0], weighed[1] = weighed[1], weighed[0]  # a near tie goes to what can be undone
    return tuple(weighed)


def _turns(s: Scene, beliefs, gs, a: float) -> tuple[Turn, ...]:
    """For each unknown: how likely its harmless answer would have to be for going ahead to be chosen."""
    out = []
    for i, b in enumerate(beliefs):
        needed = 1.01
        for k in range(0, 21):
            sure = k / 20
            trial = beliefs[:i] + (replace(b, p=1 - sure),) + beliefs[i + 1:]
            if _choose(s, trial, gs, a)[0].step.id == "do":
                needed = sure
                break
        out.append(Turn(b.unknown.id, 1 - b.p, needed))
    return tuple(out)


def weigh(s: Scene) -> Thought:
    beliefs = tuple(believe(s, uid) for uid in s.act.unknowns)
    gs = goals(s)
    a = autonomy(s)
    weighed = _choose(s, beliefs, gs, a)
    choice, do = weighed[0], next(w for w in weighed if w.step.id == "do")
    harm = -do.parts["others"] / OTHERS
    if choice.step.id == "do":
        verdict = PROCEED
    elif harm >= REFUSE_AT:
        verdict = REFUSE
    else:
        verdict = DISSUADE
    cool = 0.0
    waiting = next((w for w in weighed if w.step.reveals == "later"), None)
    upset = s.situation.upset
    if verdict == DISSUADE and waiting is not None and waiting.eu > do.eu:  # waiting is worth more than acting now
        if s.act.effect == "told":
            cool = max(3600.0, _next_morning(s.now) - s.now) if upset >= HOT else 0.0
        elif upset >= HOT:
            cool = s.known.cool_days(COOL_OFF_DAYS) * 86400.0
        elif s.act.id == "delete" and s.things.used:
            cool = COOL_OFF_CALM_DAYS * 86400.0
    turns = _turns(s, beliefs, gs, a) if verdict != PROCEED else ()
    return Thought(s, beliefs, gs, weighed, a, verdict, cool, harm, turns)


def think(frame, situation, things: Things = NOTHING, *, act: str = "", waited: bool = False, now: float | None = None,
          known: Known = NOKNOWN) -> Thought | None:
    """The thought about a weighty request, or None when nothing in it is weighty."""
    s = scene(frame, situation, things, act=act, waited=waited, now=now, known=known)
    return weigh(s) if s is not None else None


CAUTION = {PROCEED: 0, DISSUADE: 1, REFUSE: 2}


def weightiest(thoughts) -> Thought | None:
    """Of the thoughts about each act in a request, the one the gate answers for: the most cautious verdict, then
    the act whose going-ahead looks worst."""
    thoughts = [t for t in thoughts if t is not None]
    if not thoughts:
        return None
    return max(thoughts, key=lambda t: (CAUTION[t.verdict], -t.of("do").eu))


__all__ = ["ACTS", "CAUTION", "COOL_OFF_CALM_DAYS", "COOL_OFF_DAYS", "DISSUADE", "HOT", "Outcome", "PROCEED", "REFUSE",
           "REFUSE_AT", "STEPS", "Step", "Thought", "Turn", "Weighed", "autonomy", "play", "think", "weigh", "weightiest"]
