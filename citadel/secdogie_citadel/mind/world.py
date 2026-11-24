"""The world model: what an act changes, and what follows from that.

The cortex (``cortex/``) reads a request and answers the Socratic questions. Its answers come here as a
*scene*: the kind of change the request would make (the act), the facts of the moment, each with how sure
the cortex is of it, and how much is at stake. Nothing here reads words.

- **Acts** (``ACTS``): the weighty kinds of change, one per weighty answer to "what would doing it change?".
  Each says which effect it makes true, which unknowns its consequences turn on, the gentler steps that can
  stand in for it, and which of the reasons a person may have ("why?") each of its goals answers to.
- **Rules** (``RULES``): what follows from what ("it is gone and there is no other copy: it is lost").
  ``closure`` applies them until nothing new follows and keeps the chain.
- **Scene** (``scene``): one request as a small world.

The planner (``plan.py``) plays each way forward out in every way the unknowns (``belief.py``) could turn
out, by running the rules.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Things:
    """What the device says the request touches (the usage reader), when it knows."""

    names: tuple[str, ...] = ()
    found: bool = False
    used: bool = False  # in use, or recently opened
    recent: bool = False
    unused: bool = False  # empty, or never touched since it arrived
    photos: bool = False
    size: int = 0
    stories: tuple[tuple[str, ...], ...] = ()


NOTHING = Things()


@dataclass(frozen=True)
class Known:
    """What the companion has learned about this person from their own record (counts)."""

    held: int = 0  # weighty steps it held back before
    steady: int = 0  # times they came back calm after a cooling-off and went ahead
    undone: int = 0  # times they asked to restore or undo something
    goals: tuple[tuple[str, str, int], ...] = ()  # (act, goal id, times it was plainly their reason)

    def times(self, act: str, gid: str) -> int:
        return next((n for a, g, n in self.goals if a == act and g == gid), 0)

    @property
    def autonomy_lift(self) -> float:
        return 0.25 * self.steady / (self.steady + 2)

    def cool_days(self, base: int) -> int:
        return max(3, base - 2 * min(2, self.steady))


NOKNOWN = Known()


@dataclass(frozen=True)
class Goal:
    """What someone may be after when they ask for an act."""

    id: str
    atom: str  # what, once true, means they got it
    why: tuple[str, ...]  # the cortex's answers to "why?" that point to it
    zh: str = ""
    en: str = ""


@dataclass(frozen=True)
class Act:
    id: str
    effect: str  # what doing it makes true
    carries: tuple[str, ...]  # facts that come with the act itself
    unknowns: tuple[str, ...]  # the unknowns its consequences turn on
    goals: tuple[Goal, ...]  # the first one is what it is when no reason is given
    steps: tuple[str, ...]  # gentler steps that can stand in for it or go before it (plan.STEPS)
    zh: str
    en: str
    base: float = 0.5  # how much is at stake when nothing more is known


ACTS = {a.id: a for a in (
    Act("removes", "gone", (), ("later", "copy"), (
        Goal("rid", "rid", ("unsaid", "other"), "就是不要了", "get rid of it"),
        Goal("space", "freed", ("space",), "腾地方", "free up room"),
        Goal("tidy", "tidied", ("tidy",), "清爽一点", "tidy up"),
        Goal("unseen", "unseen", ("forget", "quit", "vent"), "不想再看到", "not see it any more"),
        Goal("private", "private", ("privacy",), "不让别人看到", "keep it from others"),
    ), ("hide", "lock", "move_out", "wait"), "删", "delete", 0.35),
    Act("erases", "gone", ("whole",), ("later", "copy"), (
        Goal("rid", "gone", ("unsaid", "other", "space", "tidy"), "清空它", "clear it"),
        Goal("sell", "gone", ("sell",), "转手", "pass it on"),
        Goal("fix", "gone", ("fix",), "让它重新好用", "get it working again"),
        Goal("fresh", "gone", ("forget", "quit", "vent"), "一切重来", "start over"),
    ), ("copy_first", "wait"), "格式化", "wipe", 0.9),
    Act("closes", "gone", ("account",), ("later", "copy"), (
        Goal("rid", "rid", ("unsaid", "other", "tidy"), "注销", "close it"),
        Goal("private", "private", ("privacy",), "不留痕迹", "leave no trace"),
        Goal("leave", "unseen", ("quit", "forget", "vent"), "不再用它", "stop using it"),
    ), ("pause", "copy_first", "wait"), "注销", "close", 0.75),
    Act("pays", "given", (), ("party",), (
        Goal("plain", "reached", ("unsaid", "other"), "转这笔钱", "send this money"),
        Goal("help", "reached", ("help",), "帮人", "help someone"),
        Goal("due", "reached", ("owed",), "付该付的钱", "pay what is owed"),
        Goal("gift", "reached", ("gift",), "送一份心意", "give a gift"),
        Goal("grow", "grown", ("profit",), "得到许诺的回报", "get a promised gain"),
    ), ("verify", "shrink"), "转", "send", 0.4),
    Act("lends", "given", ("loan",), ("party",), (
        Goal("plain", "reached", ("unsaid", "other", "owed", "gift", "profit", "vent", "quit"), "借给对方", "lend it"),
        Goal("help", "reached", ("help",), "帮人渡难关", "help someone through"),
    ), ("verify", "shrink"), "借", "lend", 0.4),
    Act("tells", "told", ("credential",), ("party",), (
        Goal("plain", "delivered", ("unsaid", "other", "owed", "profit"), "给出去", "hand it over"),
        Goal("help", "delivered", ("help",), "帮人办事", "help with something"),
    ), ("verify", "direct"), "给出去", "hand over", 0.85),
    Act("says", "told", (), ("later",), (
        Goal("plain", "said", ("unsaid", "other", "owed", "help", "gift", "profit"), "把话说出去", "say it"),
        Goal("vent", "vented", ("vent",), "撒气", "let the anger out"),
        Goal("mend", "mended", ("mend",), "和好", "make up"),
        Goal("end", "said", ("quit", "forget"), "了断", "end it"),
    ), ("sleep_on", "soften"), "发", "send", 0.5),
    Act("runs", "running", (), ("party",), (
        Goal("plain", "works", ("unsaid", "other", "profit", "owed", "help", "test"), "运行它", "run it"),
        Goal("fix", "works", ("fix",), "修好东西", "fix something"),
    ), ("trace",), "运行", "run", 0.8),
    Act("watches", "watched", (), (), (
        Goal("know", "learned", ("unsaid", "other", "suspect"), "知道对方在做什么", "know what they are doing"),
        Goal("safe", "located", ("safety",), "确认对方安全", "know they are safe"),
        Goal("fix", "fixed", ("fix",), "修好自己的东西", "fix your own device"),
    ), ("ask_them", "share", "ask_around"), "查看", "look into", 0.5),
    Act("registers", "made_many", (), (), (
        Goal("plain", "made_many", ("unsaid", "other", "owed", "gift", "fix"), "要这么多账号", "have these accounts"),
        Goal("test", "tested", ("test",), "测试自己的系统", "test their own system"),
        Goal("share", "shared_out", ("help",), "给认识的人用", "share them with people they know"),
        Goal("reap", "made_many", ("profit",), "多领几份优惠", "collect offers several times"),
    ), ("proper",), "注册", "register", 0.5),
)}

WEIGHTY = tuple(ACTS)  # the cortex's "effect" answers that are thought through; the rest are everyday


@dataclass(frozen=True)
class Rule:
    id: str
    needs: tuple[str, ...]
    gives: str
    strength: float = 1.0
    lacks: tuple[str, ...] = ()
    zh: str = ""  # a short label of the consequence, shown in the trace
    en: str = ""


RULES = (
    Rule("lost", ("gone", "nocopy"), "lost", lacks=("copied",), zh="找不回来", en="it can't be brought back"),
    Rule("regret", ("lost", "sorry"), "regret", zh="想要回来时已经没了", en="wanted back, and gone"),
    Rule("closed", ("gone", "account", "sorry"), "regret", 0.6, zh="账号和联系人回不来", en="the account and its contacts don't come back"),
    Rule("freed", ("gone",), "freed"), Rule("freed2", ("moved_out",), "freed", 0.95, zh="腾出地方，东西还在别处",
                                            en="room freed, things kept elsewhere"),
    Rule("unseen", ("gone",), "unseen"), Rule("unseen2", ("hidden",), "unseen", 0.85, zh="看不见了，但还在", en="out of sight, still there"),
    Rule("unseen3", ("moved_out",), "unseen", 0.5), Rule("unseen4", ("locked",), "unseen", 0.7),
    Rule("private", ("gone",), "private"), Rule("private2", ("locked",), "private", 0.9, zh="别人打不开，自己还留着",
                                                en="others can't open it, you keep it"),
    Rule("private3", ("hidden",), "private", 0.45), Rule("private4", ("moved_out",), "private", 0.3),
    Rule("tidied", ("gone",), "tidied"), Rule("tidied2", ("hidden",), "tidied", 0.7), Rule("tidied3", ("moved_out",), "tidied", 0.8),
    Rule("tidied4", ("locked",), "tidied", 0.5),
    Rule("rid", ("gone",), "rid"), Rule("rid2", ("hidden",), "rid", 0.6), Rule("rid3", ("moved_out",), "rid", 0.6),
    Rule("rid4", ("locked",), "rid", 0.5),
    Rule("taken", ("given", "hostile"), "money_lost", zh="钱追不回来", en="the money does not come back"),
    Rule("reached", ("given", "honest"), "reached", zh="钱到了该到的人手里", en="the money reaches the right person"),
    Rule("grown", ("reached",), "grown", 0.75),
    Rule("hijack", ("told", "credential", "hostile"), "account_taken", lacks=("direct",), zh="对方能以你的名义登录",
         en="they can sign in as you"),
    Rule("drained", ("account_taken",), "money_lost", zh="账号里的东西被转走", en="what is in the account is moved out"),
    Rule("delivered", ("told", "credential", "honest"), "delivered", zh="对方办完了事", en="they do what was needed"),
    Rule("delivered2", ("direct", "honest"), "delivered", 0.9, zh="当面说，冒充的人截不走", en="said in person, an impostor can't catch it"),
    Rule("said", ("told",), "said"), Rule("vented", ("told",), "vented"),
    Rule("vented2", ("drafted",), "vented", 0.6, zh="话写下来了，气也出了一半", en="written down, half the anger out"),
    Rule("hurt", ("told", "hurtful"), "they_hurt", lacks=("softened",), zh="这句话会伤到对方", en="the words hurt them"),
    Rule("mended", ("said",), "mended", lacks=("they_hurt",)),
    Rule("unsaid", ("told", "sorry"), "regret", lacks=("softened",), zh="平静后希望没发出去", en="once calm, you wish it unsent"),
    Rule("unsaid2", ("told", "sorry", "softened"), "regret", 0.5),
    Rule("owned", ("running", "hostile"), "computer_taken", zh="它能动这台电脑上的一切", en="it can touch everything on this computer"),
    Rule("works", ("running", "honest"), "works", zh="装好了，能用", en="it installs and works"),
    Rule("wronged", ("watched",), "they_wronged", 0.8, lacks=("goal:safe", "own_fix"), zh="不经同意看别人的私事", en="looking without their say-so"),
    Rule("wronged3", ("watched", "goal:safe"), "they_wronged", 0.2),
    Rule("unlawful", ("they_wronged",), "rule_broken"),
    Rule("fixed_own", ("watched", "goal:fix", "own_fix"), "fixed", zh="只动自己的设备", en="recovering your own device"),
    Rule("learned", ("watched",), "learned", 0.7), Rule("learned2", ("asked_them",), "learned", 0.65, zh="直接问，也不伤信任",
                                                       en="asking tells more and keeps the trust"),
    Rule("learned3", ("shared",), "learned", 0.35),
    Rule("located", ("watched",), "located", 0.6), Rule("located2", ("shared",), "located", 0.8, zh="对方知道也同意",
                                                        en="they know and agree"),
    Rule("located3", ("asked_around",), "located", 0.9, zh="先找身边的人，找不到就报警", en="ask around first, then the police"),
    Rule("tested", ("made_many", "goal:test"), "tested", zh="在自己的系统里测试", en="a test on your own system"),
    Rule("breaks", ("made_many",), "rule_broken", 0.65, lacks=("goal:test", "goal:share"), zh="平台不许一人多号",
         en="platforms don't allow it"),
    Rule("breaks2", ("made_many", "goal:share"), "rule_broken", 0.2),
    Rule("abuse", ("made_many", "goal:reap"), "others_harmed", 0.4, zh="占了别人的份", en="taken from others' share"),
    Rule("abuse2", ("made_many", "goal:plain"), "others_harmed", 0.3, zh="成批的账号常被拿去骗人", en="bulk accounts are used to deceive"),
    Rule("shared_out", ("made_many",), "shared_out"), Rule("shared_out2", ("each_own",), "shared_out", 0.85,
                                                           zh="各用各的号码", en="each with their own number"),
    Rule("tested2", ("each_own",), "tested", 0.9),
)

COSTS = {"money_lost": ("self", 1.0), "computer_taken": ("self", 1.0), "regret": ("regret", 1.0), "they_hurt": ("others", 0.35),
         "they_wronged": ("others", 1.0), "others_harmed": ("others", 1.0), "rule_broken": ("norm", 1.0)}


def closure(atoms: dict[str, float]) -> tuple[dict[str, float], tuple[tuple[Rule, float], ...]]:
    """Apply the rules to ``atoms`` until nothing new follows; returns what holds and the chain that fired."""
    state = dict(atoms)
    chain: list[tuple[Rule, float]] = []
    fired: set[str] = set()
    changed = True
    while changed:
        changed = False
        for r in RULES:
            if r.id in fired or any(state.get(a, 0.0) > 0 for a in r.lacks):
                continue
            if all(state.get(a, 0.0) > 0 for a in r.needs):
                degree = min(state[a] for a in r.needs) * r.strength
                fired.add(r.id)
                if degree > state.get(r.gives, 0.0):
                    state[r.gives] = degree
                    chain.append((r, degree))
                    changed = True
    return state, tuple(chain)


# The facts of a scene: each from one of the cortex's answers (or the moment), with how sure it is.
FACTS = {
    "upset": ("你现在心里不平静", "you are upset right now"), "late": ("夜很深了", "it is very late"),
    "dear": ("是回忆", "it holds memories"), "work": ("是你的工作", "it is your work"), "junk": ("是用不着的东西", "nobody needs it"),
    "considered": ("你说想清楚了", "you say you have thought it over"), "copied": ("有备份", "there is another copy"),
    "checked": ("你核实过对方", "you checked who they are"), "stranger": ("对方没法核实", "they can't be checked"),
    "pushed": ("是别人让你做的", "someone else is behind it"), "known": ("是你认识的人", "it is someone you know"),
    "own": ("是你自己的", "it is your own"), "public": ("是公开的", "it is public"), "hurtful": ("话说得很重", "the words are harsh"),
    "large": ("关系重大", "a lot is at stake"), "profit": ("说是有回报", "a gain is promised"), "owed": ("是该付的钱", "money owed"),
    "quit": ("是要了断", "it is to end something"), "waited": ("你已经等过一阵", "you have waited a while"),
    "in_use": ("它还在用", "it is still in use"), "unused": ("从没动过", "never touched"), "undone": ("你以前找回过东西", "you have undone such things before"),
    "whole": ("是一整块盘", "a whole disk"), "account": ("是一个账号", "an account"), "loan": ("是借出去的钱", "a loan"),
    "credential": ("是一把钥匙", "it is a key to your accounts"),
}


@dataclass(frozen=True)
class Fact:
    id: str
    level: float = 1.0  # how sure: the cortex's probability, or 1 for what the device or the clock says


@dataclass(frozen=True)
class Scene:
    act: Act
    facts: tuple[Fact, ...]
    stake: float  # 0..1
    why: tuple[tuple[str, float], ...]  # the cortex's answer to "why?" as shares
    upset: float  # 0..1
    considered: float  # 0..1
    things: Things = NOTHING
    known: Known = NOKNOWN
    waited: bool = False
    now: float = 0.0
    extra: dict = field(default_factory=dict, compare=False)

    def has(self, fid: str) -> bool:
        return self.level(fid) > 0.0

    def fact(self, fid: str) -> Fact | None:
        return next((f for f in self.facts if f.id == fid), None)

    def level(self, fid: str) -> float:
        f = self.fact(fid)
        return f.level if f is not None else 0.0

    def atoms(self) -> dict[str, float]:
        return {f.id: 1.0 for f in self.facts if f.id in ("credential", "public", "account", "whole", "own_fix")
                } | ({"hurtful": 1.0} if self.level("hurtful") >= 0.5 else {})


def _p(answers: dict, qid: str, value: str) -> float:
    """The cortex's share for one answer."""
    from ..cortex import questions as Q

    got = answers.get(qid)
    if got is None:
        return 0.0
    dist = got[2] if len(got) > 2 and got[2] else None
    if dist is None:
        return 1.0 if got[0] == value else 0.0
    q = Q.BY_ID[qid]
    return float(dist[q.index(value)]) if value in q.choices else 0.0


def scene(answers: dict, *, act: str = "", context: tuple[str, ...] = (), things: Things = NOTHING, known: Known = NOKNOWN,
          waited: bool = False, now: float | None = None) -> Scene | None:
    """The request as a small world, built from the cortex's answers. None when no weighty act is in it."""
    a = ACTS.get(act or answers.get("effect", ("",))[0])
    if a is None:
        return None
    p = lambda q, v: _p(answers, q, v)  # noqa: E731
    facts = [Fact(c) for c in a.carries]
    soft = {
        "upset": p("mood", "upset"), "dear": p("dear", "dear"), "work": p("dear", "work"), "junk": p("dear", "junk"),
        "considered": p("assured", "thought"), "copied": p("assured", "copied"), "checked": p("assured", "checked"),
        "stranger": max(p("to", "stranger"), p("pushed", "stranger")), "pushed": p("pushed", "known") + p("pushed", "stranger"),
        "known": p("to", "known"), "own": p("to", "own"), "public": p("to", "public"), "hurtful": p("others", "words"),
        "large": p("stake", "large"), "profit": p("why", "profit"), "owed": p("why", "owed"), "quit": p("why", "quit"),
    }
    for fid, level in soft.items():
        if level >= 0.05:
            facts.append(Fact(fid, round(level, 3)))
    own_fix = (
        p("others", "self") >= 0.90
        and p("seat", "nobody") >= 0.90
        and p("why", "fix") >= 0.60
        and p("others", "harm") < 0.05
    )
    for fid, on in (("late", "night" in context), ("waited", waited), ("in_use", things.used), ("unused", things.unused),
                    ("undone", known.undone > 0), ("own_fix", own_fix)):
        if on:
            facts.append(Fact(fid))
    upset = min(1.0, soft["upset"] + (0.15 if "night" in context and soft["upset"] > 0.2 else 0.0))
    stake = a.base + (1 - a.base) * 0.8 * soft["large"]
    if a.id == "removes":
        stake = 0.08 if (things.unused or soft["junk"] > 0.6) else min(1.0, 0.25 + 0.5 * soft["large"] + 0.35 * soft["dear"]
                                                                      + 0.2 * soft["work"] + 0.3 * things.used)
    why_q = answers.get("why")
    shares = tuple(zip(_why_choices(), why_q[2], strict=True)) if why_q and len(why_q) > 2 and why_q[2] else (
        ((why_q[0], 1.0),) if why_q else (("unsaid", 1.0),))
    return Scene(a, tuple(facts), round(stake, 3), shares, round(upset, 3), soft["considered"], things, known, waited,
                 time.time() if now is None else now)


def _why_choices() -> tuple[str, ...]:
    from ..cortex import questions as Q

    return Q.BY_ID["why"].choices


__all__ = ["ACTS", "Act", "COSTS", "FACTS", "Fact", "Goal", "Known", "NOKNOWN", "NOTHING", "RULES", "Rule", "Scene", "Things",
           "WEIGHTY", "closure", "scene"]
