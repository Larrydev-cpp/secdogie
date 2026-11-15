"""What the mind knows about weighty acts. It is written down here, not
learned.

For each kind of act there is a **domain**.

- **Motives.** Why someone might ask for it. Each motive has:
  - a prior;
  - the evidence that makes it likelier or less likely, as likelihood ratios;
  - how often a calm person with that motive later wishes an irreversible
    step undone (``regret``);
  - whether the motive comes from a passing state (``transient``), so that it
    weighs more while the person is upset;
  - what the person could say to show it (``unlock``).
- **Options.** What the companion could do about it. Each option has:
  - how well it serves each motive;
  - whether it can be undone;
  - the harm it may do to someone else and to the person;
  - the rules it breaks;
  - whether it overrides what they asked.
- **Value.** How much is lost if the companion gets it wrong.

``reason.py`` combines these with the frame and the situation: Bayes for the
motives, expected utility for the options. None of it is fitted to data.
Every number is a judgment someone can read, argue with and change.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from . import lexicon as L
from .perceive import Frame, Situation

PROCEED, DISSUADE, REFUSE = "proceed", "dissuade", "refuse"


@dataclass(frozen=True)
class Things:
    """What the usage reader and the person's own words say the request touches."""

    names: tuple[str, ...] = ()
    found: bool = False  # it is on this computer
    used: bool = False  # it is in use, or the person spoke of it
    recent: bool = False  # changed or opened in the past month
    unused: bool = False  # empty, or never touched since it arrived
    photos: bool = False  # what it holds is mostly photos and videos
    size: int = 0
    stories: tuple[tuple[str, ...], ...] = ()  # the usage story of each thing, sentence by sentence


NOTHING = Things()  # nothing known about what the request touches

Test = Callable[[Frame, Situation, Things], bool]


@dataclass(frozen=True)
class Motive:
    id: str
    prior: float
    name: str  # how the companion names it: "不想再看到它们"
    evidence: tuple[tuple[Test, float, str], ...] = ()  # (is it present, likelihood ratio, what it rests on)
    regret: float = 0.1  # calm, with this motive: how often an irreversible step is later wished undone
    transient: bool = False  # it comes from a passing state
    unlock: str = ""  # what the person can say that shows this motive


@dataclass(frozen=True)
class Option:
    id: str
    name: str  # "封存起来"
    say: str = ""  # what the person can say to choose it; {thing} is filled in
    fulfil: dict = field(default_factory=dict)  # motive -> how well it serves it (0..1)
    reversible: bool = True
    regret_factor: float = 1.0  # for an irreversible option: how much of the possible regret it carries
    effort: float = 0.0  # the delay and work it adds
    overrides: bool = True  # it is not what they asked
    harm_others: dict = field(default_factory=dict)  # motive -> harm to someone else (0..1)
    harm_self: dict = field(default_factory=dict)  # motive -> harm to the person, as a share of what is at stake
    norm: dict = field(default_factory=dict)  # motive -> how far it breaks a rule (a platform's, the law)
    because: str = ""  # one line on what this path keeps or avoids


@dataclass(frozen=True)
class Domain:
    act: str
    motives: tuple[Motive, ...]
    options: tuple[Option, ...]
    value: Callable[[Frame, Situation, Things], float]  # what is lost if it goes wrong (0..1)
    cool_off: bool = False  # an irreversible step: held back while upset, weighed again after a while
    unlock: str = "我想清楚了"  # what the person can say once they have thought it over

    def motive(self, mid: str) -> Motive:
        return next(m for m in self.motives if m.id == mid)

    def option(self, oid: str) -> Option:
        return next(o for o in self.options if o.id == oid)


def _w(pattern: str) -> Test:
    rx = re.compile(pattern, re.I)
    return lambda f, s, t: bool(rx.search(f.text))


def _p(pattern) -> Test:
    return lambda f, s, t: bool(pattern.search(f.text))


def _every(ids, v=1.0) -> dict:
    return {m: v for m in ids}


# ---- deleting ------------------------------------------------------------------------------------------

_DEL = ("free_space", "tidy", "move_on", "vent", "privacy", "plain")


def _delete_value(f: Frame, s: Situation, t: Things) -> float:
    if t.unused or (f.has("junk") and not t.used):
        return 0.08
    v = 0.35 + 0.3 * t.used + 0.2 * t.recent + 0.45 * (f.has("memories") or t.photos) + 0.25 * f.has("work")
    if f.has("memories") and f.involves("ex", "partner", "him_her"):
        v += 0.2
    return min(1.0, v)


DELETE = Domain("delete", (
    Motive("free_space", 0.22, "腾出地方", (
        (_p(L.SPACE), 6.0, "你说到空间"), (lambda f, s, t: s.upset > 0.4, 0.4, ""),
        (lambda f, s, t: t.size > (5 << 30), 1.6, "它占了不少地方")), regret=0.05),
    Motive("tidy", 0.2, "让眼前清爽一点", (
        (_p(L.TIDY), 5.0, "你说到没用的、重复的"), (lambda f, s, t: f.has("junk"), 4.0, "是些用不着的东西"),
        (lambda f, s, t: s.upset > 0.4, 0.5, "")), regret=0.03),
    Motive("move_on", 0.18, "不想再看到它们，不想再被它们刺到", (
        (lambda f, s, t: s.breakup, 4.0, "你说到分开"), (lambda f, s, t: s.sad > 0, 2.5, "你很难过"),
        (lambda f, s, t: f.has("memories") and f.involves("ex", "partner", "him_her"), 2.5, "里面有另一个人"),
        (_w(r"不想再看|看到就(难受|烦|想哭)|眼不见|忘掉|忘了"), 5.0, "你说不想再看到")), regret=0.4, transient=True),
    Motive("vent", 0.1, "在气头上，想把它抹掉", (
        (lambda f, s, t: s.angry > 0, 5.0, "你在生气"), (lambda f, s, t: s.impulse, 2.0, "你说得很急"),
        (lambda f, s, t: s.late, 1.5, "夜很深了")), regret=0.55, transient=True),
    Motive("privacy", 0.1, "有人要你删，或者怕被别人看到", ((_p(L.ASKED_BY), 8.0, "有人要你删"),), regret=0.1),
    Motive("plain", 0.2, "就是不要了", regret=0.12),
), (
    Option("do", "直接删", fulfil=_every(_DEL), reversible=False, overrides=False, because="省事，就是你说的"),
    Option("seal", "封存起来", "把{thing}封存起来",
           {"free_space": 0.1, "tidy": 0.7, "move_on": 0.85, "vent": 0.6, "privacy": 0.45, "plain": 0.6}, effort=0.05,
           because="挪进一个藏起来的「以后再看」文件夹，眼前看不见，但还在，想反悔随时可以"),
    Option("encrypt", "压缩、加密", "把{thing}加密收起来",
           {"free_space": 0.3, "tidy": 0.5, "move_on": 0.7, "vent": 0.45, "privacy": 0.9, "plain": 0.5}, effort=0.1,
           because="换一个只有你知道的口令，别人打不开，你自己还留着"),
    Option("offload", "挪到移动硬盘或网盘", "把{thing}挪到移动硬盘",
           {"free_space": 0.95, "tidy": 0.8, "move_on": 0.5, "vent": 0.3, "privacy": 0.3, "plain": 0.6}, effort=0.15,
           because="这台电脑腾出地方，东西还在别处"),
), _delete_value, cool_off=True)


# ---- wiping a disk ---------------------------------------------------------------------------------------

_WIPE = ("sell", "fix", "fresh_start", "nothing_to_lose", "plain")

WIPE = Domain("wipe", (
    Motive("sell", 0.2, "要把它卖掉或者送人", ((_p(L.SELL_DEVICE), 6.0, "你说要转手"),), regret=0.5),
    Motive("fix", 0.25, "让它重新好用", ((_p(L.FIX_SYSTEM), 5.0, "你说它不好用了"),), regret=0.55),
    Motive("fresh_start", 0.1, "想把一切清掉重来", (
        (lambda f, s, t: s.upset > 0.3, 4.0, "你心里不好受"), (lambda f, s, t: s.breakup, 3.0, "你说到分开")),
        regret=0.6, transient=True),
    Motive("nothing_to_lose", 0.15, "里面没有要留的", ((_p(L.BACKED_UP), 12.0, "你说备份过了，或者是一块新盘"),),
           regret=0.0, unlock="已经备份过了"),
    Motive("plain", 0.3, "就是想格式化", regret=0.6),
), (
    Option("do", "直接格式化", fulfil=_every(_WIPE), reversible=False, overrides=False, because="省事，就是你说的"),
    Option("backup_first", "先备份，再格式化", "先备份",
           {"sell": 0.95, "fix": 0.95, "fresh_start": 0.75, "nothing_to_lose": 0.9, "plain": 0.9}, effort=0.2,
           because="要留的先放到别处，格式化以后什么也不会丢"),
), lambda f, s, t: 0.05 if L.BACKED_UP.search(f.text) else 0.9, cool_off=True, unlock="已经备份过了")


# ---- moving money ---------------------------------------------------------------------------------------

_VERIFIED = r"确认过|打电话问过|(当面|视频)(问过|确认|说过)|本人(说|确认)|verified|called (him|her|them)"


def _money_value(f: Frame, s: Situation, t: Things) -> float:
    return min(1.0, f.amount / 5000) if f.amount else 0.4


TRANSFER = Domain("transfer", (
    Motive("family", 0.3, "帮家里人", ((lambda f, s, t: f.involves("family"), 4.0, "是给家里人"),), regret=0.02,
           unlock="我打电话确认过了，是本人"),
    Motive("obligation", 0.25, "付该付的钱", (
        (_w(r"房租|学费|账单|水电|工资|货款|还钱|还款|rent|bill|tuition|repay"), 5.0, "是该付的钱"),), regret=0.02,
        unlock="是房租，我确认过收款人了"),
    Motive("gift", 0.15, "送一份心意", ((_w(r"红包|生日|礼物|心意|gift|birthday"), 4.0, "是一份心意"),), regret=0.05),
    Motive("invest", 0.1, "投资赚钱", ((_w(r"投资|理财|收益|股票|基金|币|回报|invest|returns?|crypto"), 5.0, "说到收益"),),
           regret=0.2),
    Motive("scam", 0.04, "对方让你转，可对方很可能是骗子", (
        (_p(L.SCAM), 12.0, "对方用的是骗子常用的说法"),
        (lambda f, s, t: f.involves("stranger"), 5.0, "对方是你不认识、没见过的人，或者是自称的身份"),
        (lambda f, s, t: s.panic > 0, 2.5, "你被催得很急"),
        (_w(r"不要告诉|别告诉|don'?t tell"), 5.0, "对方让你别告诉别人"),
        (lambda f, s, t: f.involves("family"), 0.35, ""), (_w(_VERIFIED), 0.1, "你说确认过了")), regret=0.0),
    Motive("plain", 0.16, "就是要转这笔钱", regret=0.03),
), (
    Option("do", "直接转", fulfil={"family": 1, "obligation": 1, "gift": 1, "invest": 1, "scam": 0, "plain": 1},
           reversible=False, overrides=False, harm_self={"scam": 1.0, "invest": 0.25}, because="省事，就是你说的"),
    Option("verify", "先打个电话确认对方", "我先打电话确认一下",
           {"family": 0.9, "obligation": 0.85, "gift": 0.9, "invest": 0.7, "scam": 0, "plain": 0.85}, effort=0.15,
           harm_self={"scam": 0.05, "invest": 0.15},
           because="用你自己存的号码打过去，或者当面问一句；真是对方要的，晚几分钟也不耽误"),
), _money_value, unlock="我打电话确认过了，是本人")


# ---- saying something that cannot be unsaid --------------------------------------------------------------

_MSG = ("inform", "vent", "reconcile", "end", "plain")


def _message_value(f: Frame, s: Situation, t: Things) -> float:
    if f.act in ("resign", "post") or f.involves("public"):
        v = 0.9
    elif f.involves("boss"):
        v = 0.85
    elif f.involves("ex", "partner", "family", "him_her"):
        v = 0.8
    elif f.involves("colleague"):
        v = 0.6
    else:
        v = 0.5
    return min(1.0, v * (1.2 if L.INSULT.search(f.content or f.text) else 1.0))


MESSAGE = Domain("message", (
    Motive("inform", 0.5, "把事情说清楚", regret=0.03, unlock="我想清楚了，现在就发"),
    Motive("vent", 0.08, "把气撒出来", (
        (lambda f, s, t: s.angry > 0, 5.0, "你在生气"), (_p(L.INSULT), 6.0, "话说得很重"),
        (lambda f, s, t: s.late, 1.8, "夜很深了"), (lambda f, s, t: s.impulse, 2.0, "你说得很急")),
        regret=0.6, transient=True),
    Motive("reconcile", 0.1, "想和好", ((_w(r"对不起|想你|和好|复合|原谅|sorry|miss you|forgive"), 5.0, "你想和好"),),
           regret=0.25, transient=True),
    Motive("end", 0.12, "想做个了断", (
        (_w(r"分手|辞职|不干了|结束|断了|break up|quit|resign"), 4.0, "你说到要结束"),
        (lambda f, s, t: f.act == "resign", 4.0, "是一封辞职信")), regret=0.35, transient=True, unlock="我想清楚了，现在就发"),
    Motive("plain", 0.2, "就是要发", regret=0.08),
), (
    Option("do", "现在就发", fulfil=_every(_MSG), reversible=False, overrides=False, harm_others={"vent": 0.35},
           because="省事，就是你说的"),
    Option("delay", "先拟好，明早再发", "先拟好，明天早上再发",
           {"inform": 0.6, "vent": 0.55, "reconcile": 0.75, "end": 0.85, "plain": 0.6}, effort=0.2,
           because="我现在就把话拟好放着；睡一觉，明早你还想发，一个字不改就发出去"),
    Option("soften", "把话说得不伤人", "帮我改得平和一点再发",
           {"inform": 0.9, "vent": 0.5, "reconcile": 0.95, "end": 0.9, "plain": 0.85}, reversible=False,
           regret_factor=0.4, effort=0.1, harm_others={"vent": 0.05}, because="意思不变，只是不说以后会后悔的狠话"),
), _message_value, cool_off=True, unlock="我想清楚了，现在就发")


# ---- accounts in bulk --------------------------------------------------------------------------------

BULK = Domain("bulk", (
    Motive("testing", 0.25, "在自己的系统里测试", (
        (_p(L.OWN_SYSTEM), 12.0, "是你自己的测试环境"), (lambda f, s, t: bool(s.roles), 3.0, "你做测试或者开发")),
        unlock="在公司的 staging 测试环境注册十个测试账号"),
    Motive("family", 0.2, "给家里人或者同事用", (
        (_p(L.FAMILY_USE), 6.0, "是给别人用"), (lambda f, s, t: f.involves("family"), 2.0, "说到家里人"))),
    Motive("promo", 0.2, "多领几份优惠或者奖励", ((_p(L.PROMO), 6.0, "说到优惠、抽奖"),)),
    Motive("plain", 0.35, "就是要这么多账号"),
), (
    Option("do", "直接注册", fulfil=_every(("testing", "family", "promo", "plain")), overrides=False,
           harm_others={"promo": 0.5, "plain": 0.3}, norm={"family": 0.2, "promo": 0.7, "plain": 0.6},
           because="省事，就是你说的"),
    Option("own_way", "换个正当的办法", "", {"testing": 0.9, "family": 0.85, "promo": 0.2, "plain": 0.5},
           because="给家里人用，就让每个人用自己的手机号注册；要测试，就在你自己的测试环境里注册测试账号"),
), lambda f, s, t: 0.5, unlock="在公司的 staging 测试环境注册十个测试账号")


# ---- watching someone without their knowing ------------------------------------------------------------

TRACK = Domain("track", (
    Motive("suspicion", 0.35, "心里不踏实，想知道对方在做什么", (
        (_p(L.SUSPECT), 4.0, "你在怀疑"), (lambda f, s, t: f.involves("partner"), 3.0, "对方是你的另一半"))),
    Motive("stalk_ex", 0.15, "放不下前任", ((lambda f, s, t: f.involves("ex"), 8.0, "对方是前任"),)),
    Motive("safety", 0.2, "担心对方的安全", (
        (_p(L.SAFETY), 5.0, "你在担心对方的安全"),
        (lambda f, s, t: f.involves("family") and not f.involves("partner"), 2.0, "是家里人"))),
    Motive("plain", 0.3, "就是想看看"),
), (
    Option("do", "偷偷去看", fulfil={"suspicion": 0.7, "stalk_ex": 0.8, "safety": 0.6, "plain": 0.7}, overrides=False,
           harm_others={"suspicion": 0.8, "stalk_ex": 1.0, "safety": 0.3, "plain": 0.7},
           norm={"suspicion": 0.8, "stalk_ex": 1.0, "safety": 0.3, "plain": 0.8}, because="能马上知道"),
    Option("talk", "当面问清楚", "帮我想想怎么开口问", {"suspicion": 0.65, "stalk_ex": 0.3, "safety": 0.3, "plain": 0.5},
           because="直接问，比偷偷看更能知道你想知道的，也不会伤了信任"),
    Option("consent", "说好以后共享位置", "帮我设置共享位置", {"safety": 0.8, "suspicion": 0.3, "plain": 0.4},
           because="对方知道，也同意，担心的时候随时能看到"),
    Option("call_help", "先找人，真联系不上就报警", "", {"safety": 0.9, "suspicion": 0.1, "plain": 0.2},
           because="先打给身边的人，问问最后见到的人；真的失联，就打 110"),
), lambda f, s, t: 0.5, unlock="")


# ---- closing an account ------------------------------------------------------------------------------

CLOSE = Domain("close_account", (
    Motive("privacy", 0.25, "不想再留下痕迹", ((_w(r"隐私|信息|泄露|privacy|data"), 5.0, "你在意隐私"),), regret=0.1),
    Motive("leave", 0.3, "不想再用它", ((_w(r"不用了|不玩了|卸载|退出|quit|stop using"), 4.0, "你不想再用"),), regret=0.15),
    Motive("vent", 0.1, "在气头上，想一走了之", ((lambda f, s, t: s.upset > 0.3, 5.0, "你心里不好受"),), regret=0.55,
           transient=True),
    Motive("plain", 0.35, "就是要注销", regret=0.2),
), (
    Option("do", "直接注销", fulfil=_every(("privacy", "leave", "vent", "plain")), reversible=False, overrides=False,
           because="省事，就是你说的"),
    Option("export_first", "先导出，再注销", "先把{thing}的记录导出来",
           {"privacy": 0.9, "leave": 0.95, "vent": 0.7, "plain": 0.9}, effort=0.15,
           because="聊天记录、照片先导出来留着，再注销，什么也不会丢"),
    Option("pause", "先停用一阵", "先退出登录、停用一阵", {"privacy": 0.5, "leave": 0.8, "vent": 0.85, "plain": 0.6},
           effort=0.05, because="眼前清净了，想回来也还在"),
), lambda f, s, t: 0.75, cool_off=True)

DOMAINS = {"delete": DELETE, "wipe": WIPE, "transfer": TRANSFER, "send": MESSAGE, "post": MESSAGE, "resign": MESSAGE,
           "bulk": BULK, "track": TRACK, "close_account": CLOSE}

__all__ = ["BULK", "CLOSE", "DELETE", "DISSUADE", "DOMAINS", "Domain", "MESSAGE", "Motive", "NOTHING", "Option", "PROCEED",
           "REFUSE", "TRACK", "TRANSFER", "Things", "WIPE"]
