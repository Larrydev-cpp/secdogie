"""What the mind does not know, and how sure it is.

A consequence usually turns on something no one has said:

- ``later``: will they still think the same once this has passed, or be
  sorry?
- ``copy``: is there another copy of the thing, or is this the only one?
- ``party``: is the other side who they say they are, or not?
- the **goal**: what the person is after (``world.Goal``).

Each unknown has a prior, and **cues**: facts of the scene that make one
answer likelier, as likelihood ratios. Belief is Bayes: prior odds times the
ratios of every cue present. Nothing is fitted to data; every ratio is a
judgment that can be read and argued with.

A cue that reassures may carry ``say``: what the person could tell the
companion to supply that fact. The mind only ever offers such a sentence
after it has thought the request through again with it and seen the answer
change (``mind.consider``).
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
    zh: str  # the question, in the companion's words
    en: str
    fine_zh: str
    fine_en: str
    feared_zh: str
    feared_en: str
    advice_zh: str = ""  # what to keep in mind when the feared answer is likely
    advice_en: str = ""


UNKNOWNS = {u.id: u for u in (
    Unknown("later", "same", "sorry", {"delete": 0.12, "wipe": 0.6, "close_account": 0.25, "send": 0.06, "post": 0.1,
                                       "resign": 0.2},
            "过后你还会不会这样想", "whether you will still feel this way later",
            "过后还是这样想", "you still feel this way later", "过后会后悔", "you are sorry later",
            advice_zh="此刻的你和过些时候的你，也许想要不一样的东西。我不想替现在的你，做一件以后的你没法反悔的事。",
            advice_en="You now and you a little later may want different things. I don't want to do something for you now that the "
                      "later you can't take back."),
    Unknown("copy", "copy", "nocopy", {"delete": 0.75, "wipe": 0.8, "close_account": 0.9},
            "别处还有没有一份", "whether there is another copy somewhere",
            "别处还有一份", "there is another copy", "这是唯一的一份", "this is the only copy"),
    Unknown("party", "honest", "hostile", {"transfer": 0.03, "lend": 0.05, "share_secret": 0.1, "run_unknown": 0.05},
            "对方可不可信", "whether the other side can be trusted",
            "对方可信", "they can be trusted", "对方不可信", "they can't be trusted",
            advice_zh="真正的银行、客服和公检法，都不会让你把钱转到「安全账户」，不会要你的验证码和密码，也不会让你瞒着家里人。越催你、越让你保密，越要停一下。"
                      "拿不准，打 96110 反诈专线问一问；要是已经给出去了，马上打 110。",
            advice_en="A real bank, a real support line and the police never ask you to move money to a \u201csafe account\u201d, never ask "
                      "for a code or a password, and never tell you to keep it from your family. The more they rush you and the more "
                      "secret it is, the more reason to stop. If you are unsure, call your bank's official number (or 96110 in "
                      "mainland China); if it has already gone, call the police now."),
)}

Ratio = Callable[[Scene, Fact], float]


@dataclass(frozen=True)
class Cue:
    unknown: str
    fact: str  # the fact of the scene it rests on
    ratio: float | Ratio  # likelihood ratio toward the feared answer (> 1 raises it, < 1 lowers it)
    zh: str
    en: str
    acts: tuple[str, ...] = ()  # only for these acts ("" = wherever the unknown is in play)
    say_zh: str = ""  # what the person could say to supply this fact
    say_en: str = ""
    if_zh: str = ""  # ... and the condition it states: "要是{if_zh}，跟我说「{say_zh}」"
    if_en: str = ""
    proxy: bool = False  # indirect: a sign of what the person will want, which their own settled word screens off


# The person's own settled word ("I've thought it over", or having waited and still wanting it) is direct evidence
# about what they will want; indirect signs (it is in use, it holds memories) are evidence about the same thing, which
# they already know. So while they are calm, their word screens the indirect signs off: each such ratio is raised to
# this power. While they are upset their word is not settled, and the signs count in full.
SCREENED = 0.3
_DIRECT = ("considered", "waited")

_WORDS = ("send", "post", "resign")
_GONE = ("delete", "wipe", "close_account")
CUES = (
    # will they be sorry later
    Cue("later", "upset", lambda s, f: 1 + 9 * f.level, "你现在心里不平静", "you are upset right now"),
    Cue("later", "breakup", 2.0, "你说到分开", "you speak of a breakup"),
    Cue("later", "late", lambda s, f: 1.6 if s.has("upset") or s.has("impulse") else 1.0, "夜很深了", "it is very late"),
    Cue("later", "impulse", 1.8, "你说得很急，像是想一下子了结它", "you put it as if to be done with it in one go"),
    Cue("later", "hard_days", 1.4, "这几天你一直不好受", "these have been hard days for you"),
    Cue("later", "in_use", 8.0, "它还在用", "it is still in use", ("delete",), proxy=True),
    Cue("later", "recent", 2.0, "最近还动过它", "it was changed or opened lately", ("delete",), proxy=True),
    Cue("later", "dear", 2.5, "里面是回不来的回忆", "what is in it can't be made again", ("delete", "close_account"), proxy=True),
    Cue("later", "work", 1.5, "里面是你的工作", "it holds your work", ("delete",), proxy=True),
    Cue("later", "hurtful", 3.0, "话说得很重", "the words are harsh", _WORDS, proxy=True),
    Cue("later", "undone", 1.4, "你以前找回过删掉的东西", "you have recovered deleted things before", _GONE),
    Cue("later", "considered", lambda s, f: 0.25 + 0.6 * s.level("upset"), "你说想清楚了", "you say you have thought it over",
        say_zh="我想清楚了", say_en="I've thought it over", if_zh="你已经想清楚了", if_en="you have thought it over"),
    Cue("later", "waited", lambda s, f: 0.3 + 0.5 * s.level("upset"), "你已经等过一阵，还是这样想", "you waited, and still want it"),
    Cue("later", "leave_words", 0.4, "你说已经不用它了", "you say you no longer use it", ("close_account",)),
    Cue("later", "junk", 0.1, "是些用不着的东西", "they are things no one needs", ("delete",)),
    Cue("later", "unused", 0.08, "它放进来以后从没动过", "it has not been touched since it arrived", ("delete",)),
    Cue("later", "steady", lambda s, f: 0.7 ** f.level, "上次你等过以后，还是这样决定的", "last time you waited and still chose this"),
    # is there another copy
    Cue("copy", "backed", 0.03, "你说备份过了，或者是一块新盘", "you say it is backed up, or new",
        say_zh="已经备份过了", say_en="I've already backed it up", if_zh="已经有备份", if_en="it is backed up"),
    Cue("copy", "junk", 0.1, "这类东西随时能再弄到", "things like these can be had again any time", ("delete",)),
    Cue("copy", "unused", 0.3, "它是空的，或者从没用过", "it is empty or was never used", ("delete",)),
    # is the other side who they say
    Cue("party", "scam_words", 12.0, "对方用的是骗子常用的说法", "they use the words scammers use"),
    Cue("party", "safe_account", 8.0, "「安全账户」只在骗局里出现", "a “safe account” only ever appears in scams",
        ("transfer", "lend")),
    Cue("party", "to_stranger", 5.0, "对方的身份你没法确认", "you can't verify who they are"),
    Cue("party", "official", 4.0, "对方自称客服、银行或者警察", "they claim to be support, a bank or the police", ("share_secret",)),
    Cue("party", "code", 3.0, "验证码从来只该你自己用", "a code is only ever for you", ("share_secret",)),
    Cue("party", "rushed", 2.5, "你被催得很急", "you are being rushed"),
    Cue("party", "secrecy", 5.0, "对方让你别告诉别人", "they tell you to keep it secret"),
    Cue("party", "returns_words", 5.0, "说到带你赚钱", "it promises to make you money", ("transfer", "lend")),
    Cue("party", "prize", 5.0, "来路像是骗局", "where it comes from looks like a scam", ("run_unknown",)),
    Cue("party", "cracked", 2.5, "破解版里常常夹着别的东西", "cracked copies often carry something else", ("run_unknown",)),
    Cue("party", "shield", 3.0, "你要关掉的是电脑的防护", "it is the computer's protection you would turn off", ("run_unknown",)),
    Cue("party", "to_family", 0.3, "是家里人", "they are family"),
    Cue("party", "to_friend", 0.5, "是认识的人", "you know them"),
    Cue("party", "verified", 0.1, "你说确认过了", "you say you verified it",
        say_zh="我打电话确认过了，是本人", say_en="I called them and verified it", if_zh="你已经核实过对方",
        if_en="you have checked who they are"),
    Cue("party", "due_words", 0.3, "是该付的钱", "it is money owed", ("transfer",)),
    Cue("party", "long_known", 0.3, "你们认识很久了，或者是当面", "you have known them a long time, or it is in person", ("lend",),
        say_zh="是我认识多年的朋友，当面借的", say_en="It's an old friend, lent in person", if_zh="对方是认识多年的朋友、当面借",
        if_en="they are an old friend and it is in person"),
    Cue("party", "work_source", 0.15, "是工作上、或者从官方来的", "it is from work or an official source", ("run_unknown",),
        say_zh="是公司 IT 发的，官网下载的", say_en="It's from IT at work, from the official site", if_zh="它是公司或者官方发的",
        if_en="it comes from work or an official source"),
    Cue("party", "own_device", 0.1, "是给你自己用", "it is for your own use", ("share_secret",)),
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
    because: tuple[str, ...]  # the facts it rests on


def believe(s: Scene, uid: str) -> Belief:
    u = UNKNOWNS[uid]
    prior = u.prior.get(s.act.id, 0.1)
    odds = prior / (1 - prior)
    seen = []
    facts = list(s.facts)
    if s.known.steady:
        facts.append(Fact("steady", level=float(s.known.steady)))
    upset = s.level("upset")
    screen = SCREENED + (1 - SCREENED) * min(1.0, upset / 0.3) if any(s.has(d) for d in _DIRECT) else 1.0
    for c in CUES:
        if c.unknown != uid or (c.acts and s.act.id not in c.acts):
            continue
        f = next((x for x in facts if x.id == c.fact), None)
        if f is None:
            continue
        r = c.ratio(s, f) if callable(c.ratio) else c.ratio
        if c.proxy:
            r **= screen
        if r != 1.0:
            odds *= r
            seen.append((c, f, r))
    return Belief(u, odds / (1 + odds), tuple(seen))


def goals(s: Scene) -> tuple[GoalBelief, ...]:
    """What they are most likely after, likeliest first."""
    scores = []
    for g in s.act.goals:
        score, why = s.known.prior(s.act.id, g), []
        if s.known.times(s.act.id, g.id):
            why.append("before")
        for fid, ratio in g.cues:
            f = s.fact(fid)
            if f is None:
                continue
            score *= ratio if fid != "upset" or ratio >= 1 else 1 - (1 - ratio) * min(1.0, f.level / 0.4)
            if ratio > 1:
                why.append(fid)
        scores.append((g, score, tuple(why)))
    total = sum(sc for _, sc, _ in scores) or 1.0
    return tuple(sorted((GoalBelief(g, sc / total, why) for g, sc, why in scores), key=lambda b: -b.p))


__all__ = ["Belief", "CUES", "Cue", "GoalBelief", "SCREENED", "UNKNOWNS", "Unknown", "believe", "goals"]
