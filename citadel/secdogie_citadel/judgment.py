"""How the automatic Socratic gate weighs a request that no hard rule settles.

The gate's hard constraints come first and are absolute (``deliberate.py``):
- harm to someone else is refused;
- bulk accounts after a history of fraud are refused;
- the operator's own rules are honoured;
- something the person still uses is not deleted.

Everything else is a judgment, and this module makes it the way a careful
partner would, as a small decision under uncertainty:

1. **Read** what the request would *do*. Each act (read, write, organize,
   modify, delete, send, publish, pay, accounts, install, system) carries how
   hard it is to undo, how far it reaches (you alone, someone else, the
   public) and whether money moves. Words like 所有 / all widen it.
2. **Doubt**: what could make the companion misunderstand. These are a bare
   "那个" where a thing should be named, a verb with no object, a message
   with no recipient, a purchase with no budget, hedging. Each doubt has a
   weight (how often it means a misunderstanding) and the one question that
   would settle it. A request like ones the person made before, which went
   fine, is less doubtful.
3. **Weigh**:
   - P(wrong) = 1 - Π(1 - weight), on top of a small prior for a slip of the
     tongue (larger for steps nothing can undo, like formatting a disk).
   - The stakes are what acting on a misunderstanding would cost.
   - Asking costs the person's attention: cheap while they are here, dear
     while they are deep in their work.
   - Asking is worth it only when its value is greater than its cost:
     value = (P(wrong) - P(wrong after an answer)) × stakes.
4. **Decide**:
   - Ask the single question that removes the most doubt, or go ahead. A
     question is only ever about *what* is meant, never "are you sure":
     deleting, wiping a disk and accounts in bulk are weighed by reading the
     person instead (``read_person`` below, used by ``deliberate.py``).
   - When going ahead with something that reaches other people or money, say
     how it will still be checked (a look first, a signature at Gate 2).

It is deterministic and pure: the same request and the same history give the
same decision. Every number is journaled with the words it rests on, so the
person can see why it asked, or why it didn't.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---- what a request does -------------------------------------------------------------------------

READ, CREATE, SCHEDULE, ORGANIZE, MODIFY, INSTALL, ACCOUNT, DELETE, SEND, PUBLISH, PAY, SYSTEM = (
    "read", "create", "schedule", "organize", "modify", "install", "account", "delete", "send", "publish", "pay",
    "system")
SELF, OTHERS, PUBLIC = "self", "others", "public"

HERE, FLOW, AWAY = "here", "flow", "away"
# What one question costs the person, in the same units as the stakes. Here:
# they just asked and are looking. Flow: deep in their work. Away: they will
# read it when they are back, and the work waits.
ASK_COST = {HERE: 0.5, FLOW: 1.1, AWAY: 0.3}
SLIP = 0.03  # a clear sentence still goes wrong this often (a typo, a slip of the tongue)
AFTER_ANSWER = 0.15  # what is left of the doubt once the person has answered
FAMILIAR = 0.5  # a request like one that went fine before: half the doubt

_DELETE_WORDS = (r"删除|删掉|删了|删去|删一下|清空|清掉|扔掉|丢掉|抹掉|移除|\brm\b|\bdelete\b|\bremove\b|\berase\b"
                 r"|\bwipe\b")


@dataclass(frozen=True)
class ActKind:
    kind: str
    rx: re.Pattern
    undo: float  # 0: undone in a moment .. 1: nothing brings it back
    reach: str
    money: bool
    harm: str  # what going wrong means, in the companion's words
    care: str  # how it is still checked when the gate lets it through


_ACTS = (
    ActKind(SYSTEM, re.compile(r"格式化|重装系统|恢复出厂|清空(回收站|废纸篓)|抹掉(整个)?(磁盘|硬盘)|重新分区"
                               r"|format\s+(the\s+)?(disk|drive)|factory reset|empty (the )?(trash|recycle bin)", re.I),
            1.0, SELF, False, "这一步做了，里面的东西就再也找不回来", "这一步改不回来，动手前会请你签名。"),
    ActKind(PAY, re.compile(r"付款|支付|转账|汇款|打钱|充值|下单|购买|续费|(?<!新)买(?!的)(一|个|些|点|下|了|张|台|件|份|本)?"
                            r"|订(一?张|个)?(票|机票|火车票|高铁票|酒店|房间|外卖|餐)"
                            r"|\bpay\b|\bbuy\b|purchase|place an order|(wire|transfer) money|subscribe to", re.I),
            0.9, OTHERS, True, "钱付出去，就很难再要回来", "付款之前，会请你签名。"),
    ActKind(PUBLISH, re.compile(r"发布|发帖|发(到|个|条|一条)?(朋友圈|微博|动态|推特|小红书|抖音|公众号)|公开发表"
                                r"|\bpost (it |this )?(on|to)\b|\bpublish\b|\btweet\b", re.I),
            0.9, PUBLIC, False, "发出去的话收不回来，看到的人会把它当成你说的", "发布之前，我会先给你看一眼。"),
    ActKind(SEND, re.compile(r"发(给|送|出去)|发(个|封|一封|条|一条)?(邮件|消息|短信|微信)|回复|转发|寄给|\bsend\b|\breply\b|\bforward\b"
                             r"|\be-?mail (him|her|them|to)\b|\bmessage (him|her|them)\b", re.I),
            0.95, OTHERS, False, "发出去的话收不回来，对方看到的是你的名字", "发出去之前，我会先给你看一眼。"),
    ActKind(ACCOUNT, re.compile(r"注册|注销|改密码|修改密码|重置密码|解绑|绑定(手机|邮箱|银行卡)|退订|取消订阅"
                                r"|sign ?up|close (my )?account|change (my )?password|unsubscribe", re.I),
            0.6, OTHERS, False, "账号上的事会用到你的名字和信息，有的撤不回来", "用到你的账号和信息之前，我会先问你。"),
    ActKind(INSTALL, re.compile(r"安装|卸载|运行(这个|一下)?(脚本|程序|命令)|执行(这个|这条|一下)?(脚本|命令)|\bsudo\b"
                                r"|\binstall\b|uninstall|run (this|the) (script|command)", re.I),
            0.5, SELF, False, "装上或运行的东西能改动整台电脑", "要运行的是什么，我会先给你看。"),
    ActKind(DELETE, re.compile(_DELETE_WORDS, re.I),
            0.95, SELF, False, "删除是改不回来的一步", "删之前，会请你签名。"),
    ActKind(MODIFY, re.compile(r"修改|改(成|为|一下|掉|好)|替换|重命名|覆盖|编辑|\bedit\b|\brename\b|\breplace\b"
                               r"|overwrite", re.I),
            0.4, SELF, False, "改错了还能改回来，但要费些功夫", ""),
    ActKind(ORGANIZE, re.compile(r"整理|归档|移动|挪(到|一下)|分类|排序|压缩|备份|同步|上传到(网盘|云盘|iCloud|OneDrive"
                                 r"|Google ?Drive|Dropbox)|\btidy\b|organi[sz]e|archive|\bmove\b|back ?up|\bsync\b", re.I),
            0.2, SELF, False, "放错了地方还能挪回来", ""),
    ActKind(SCHEDULE, re.compile(r"提醒|日程|安排|约(个|一下)?会|订会议室|calendar|remind|schedule", re.I),
            0.15, SELF, False, "记错了时间，可能误了事", ""),
    ActKind(CREATE, re.compile(r"写|起草|新建|创建|生成|做(一|个)|画|翻译|总结|draft|write|create|generate|make a"
                               r"|translate|summari[sz]e", re.I),
            0.05, SELF, False, "写得不对，改就是了", ""),
    ActKind(READ, re.compile(r"看看|看一下|查|搜|找|读|列出|统计|比较|分析|告诉我|多少|是什么|是不是|怎么|为什么|look"
                             r"|check|find|search|list|show|count|compare|analy[sz]e|how many|what is", re.I),
            0.0, SELF, False, "只是看，不会动任何东西", ""),
)
_BY_KIND = {a.kind: a for a in _ACTS}
_BASE = {DELETE: "删", PAY: "买", MODIFY: "改", ORGANIZE: "整理", INSTALL: "装", ACCOUNT: "处理", SCHEDULE: "安排",
         CREATE: "写", SYSTEM: "清掉"}  # the plain verb, for a question about the object
_REACH_COST = {SELF: 0.0, OTHERS: 2.0, PUBLIC: 3.0}

_ALL = re.compile(r"所有|全部|整个|一切|统统|全都|每一个|\bevery(thing| one)?\b|\ball\b|\bentire\b|\bwhole\b", re.I)
_MANY = re.compile(r"(?P<n>\d{2,})\s*(个|份|张|条|封|件|items?|files?)", re.I)
_PRONOUN = r"它们|他们|她们|它|他|她|那个|这个|那些|这些|那边|这边|那里|这里|上次那个|上回那个"
_VERB_AFTER = (r"删|发|回|转|改|移|挪|上传|处理|整理|弄|扔|丢|清|卸|装|买|格式化|压缩|备份|归档|重命名|覆盖|打开|关")
_BARE = re.compile(rf"把\s*(?P<a>{_PRONOUN})\s*(?:给我|都|全部?|也)?\s*(?:{_VERB_AFTER})"
                   rf"|(?:删|删掉|删除|发给|回复|转发给|改|移到|挪到|上传|买|处理|整理|弄)\s*(?:一下)?\s*(?P<b>{_PRONOUN})"
                   r"\s*(?:吧|了)?\s*[。！!.]?\s*$"
                   r"|\b(?P<c>it|that one|those|them)\s*[.!]?\s*$", re.I)
_POLITE = re.compile(r"帮我|帮忙|请你|请|麻烦你?|你|给我|一下|吧|呗|了|啊|呀|嘛|吗|下|please|can you|could you|for me"
                     r"|[，。！？!?,.\s]", re.I)
_RECIPIENT = re.compile(r"(发给|回复|转发给|寄给|给|to)\s*(?P<who>[^，。,;；\s]{1,12})", re.I)
_RECIPIENT_NOUN = re.compile(r"老师|老板|同事|领导|经理|客户|妈|爸|朋友|同学|群|团队|大家|家人|[\w.+-]+@[\w-]+\.[\w.]+|@\S+",
                             re.I)
_PRONOUN_ONLY = re.compile(rf"^({_PRONOUN}|him|her|them|it|that)\b", re.I)
_AMOUNT = re.compile(r"\d+(\.\d+)?\s*(元|块|万|千|美元|刀|镑|欧|rmb|usd|eur|dollars?)|[¥$€£]\s*\d|预算|以内|不超过|under\s*\$?\d",
                     re.I)
_PLACE = re.compile(r"朋友圈|微博|推特|twitter|小红书|抖音|公众号|博客|blog|论坛|群里|网站|主页|linkedin|facebook|instagram", re.I)
_HEDGE = re.compile(r"好像|大概|也许|可能|不确定|差不多|随便|看着办|maybe|probably|not sure|whatever|i guess", re.I)


@dataclass(frozen=True)
class Act:
    kind: str
    words: str  # the words that showed it

    @property
    def spec(self) -> ActKind:
        return _BY_KIND[self.kind]


@dataclass(frozen=True)
class Doubt:
    """Something that could make the companion misunderstand, and the question that settles it."""

    cue: str
    weight: float  # how often this means a misunderstanding
    words: str  # what in the request shows it
    question: str  # the one question that would settle it
    why: str  # the doubt in the companion's words


@dataclass(frozen=True)
class Assessment:
    acts: tuple[Act, ...]
    stakes: float  # what acting on a misunderstanding would cost (0..10)
    doubts: tuple[Doubt, ...]
    familiar: str = ""  # a past request this resembles, which went fine
    p_wrong: float = SLIP
    ask_cost: float = ASK_COST[HERE]
    attention: str = HERE
    wide: bool = False
    notes: tuple[str, ...] = field(default=())

    @property
    def value_of_asking(self) -> float:
        return (self.p_wrong - self.p_wrong * AFTER_ANSWER) * self.stakes

    @property
    def asks(self) -> bool:
        return self.value_of_asking > self.ask_cost

    @property
    def main(self) -> Act | None:
        """The act with the most at stake."""
        return max(self.acts, key=lambda a: _act_cost(a.spec), default=None)

    @property
    def question(self) -> Doubt | None:
        """The doubt whose answer removes the most: the heaviest."""
        return max(self.doubts, key=lambda d: d.weight, default=None)

    def care(self) -> str:
        """How a step that reaches others, money or the whole disk is still checked after the gate lets it through."""
        main = self.main
        return main.spec.care if main is not None and self.stakes >= 6 else ""

    def journal(self) -> dict:
        return {"acts": [a.kind for a in self.acts], "stakes": round(self.stakes, 2), "p_wrong": round(self.p_wrong, 3),
                "value_of_asking": round(self.value_of_asking, 3), "ask_cost": self.ask_cost,
                "attention": self.attention, "wide": self.wide, "familiar": self.familiar,
                "doubts": [[d.cue, d.weight, d.words] for d in self.doubts], "asks": self.asks}


def _act_cost(spec: ActKind) -> float:
    return 1.0 + 6.0 * spec.undo + _REACH_COST[spec.reach] + (2.0 if spec.money else 0.0)


def acts_in(text: str) -> tuple[Act, ...]:
    """What the request would do, most consequential first."""
    found = []
    for spec in _ACTS:
        m = spec.rx.search(text)
        if m and not (spec.kind == PUBLISH and re.search(r"上传到(网盘|云盘)", text)):
            found.append(Act(spec.kind, m.group(0)))
    return tuple(sorted(found, key=lambda a: -_act_cost(a.spec)))


def _core(text: str) -> str:
    return _POLITE.sub("", text)


def _bigrams(text: str) -> set[str]:
    t = re.sub(r"\s+", "", text.lower())
    return {t[i:i + 2] for i in range(len(t) - 1)}


def resemblance(a: str, b: str) -> float:
    """How alike two requests read (Jaccard over character pairs; works for Chinese and English)."""
    x, y = _bigrams(a), _bigrams(b)
    return len(x & y) / len(x | y) if x and y else 0.0


def doubts_in(text: str, acts: tuple[Act, ...]) -> tuple[Doubt, ...]:
    kinds = {a.kind for a in acts}
    out: list[Doubt] = []
    m = _BARE.search(text)
    if m and acts and kinds != {READ}:
        word = m.group("a") or m.group("b") or m.group("c")
        if word in ("他", "她", "他们", "她们", "him", "her", "them") and kinds & {SEND, PUBLISH}:
            out.append(Doubt("who", 0.45, word, "要发给谁？告诉我名字或者地址。", f"你说「{word}」，我不知道是谁"))
        else:
            out.append(Doubt("which", 0.35, word, f"你说的「{word}」，是指哪一个？告诉我它的名字或者位置。",
                             f"你说「{word}」，我不确定是哪一个"))
    core = _core(text)
    main = max(acts, key=lambda a: _act_cost(a.spec), default=None)
    if main is not None and main.kind not in (READ, SYSTEM) and len(core) <= len(main.words) + 1 and not m:
        verb = main.words
        question = ("你想发些什么？说给我听，我先拟好给你看。" if main.kind in (SEND, PUBLISH)
                    else f"你想{_BASE.get(main.kind, verb)}的是什么？说得具体一点就好。")
        # a missing object is a real doubt for a step that is hard to undo; for an easy one, the obvious guess is cheaper
        out.append(Doubt("what", 0.5 if main.spec.undo >= 0.35 else 0.2, verb, question, f"你只说了「{verb}」，没说对什么"))
    if SEND in kinds and not any(d.cue in ("who", "what") for d in out):
        r = _RECIPIENT.search(text)
        who = r.group("who") if r else ""
        if not (who and not _PRONOUN_ONLY.match(who)) and not _RECIPIENT_NOUN.search(text):
            out.append(Doubt("who", 0.35, "", "要发给谁？告诉我名字或者地址。", "你没说要发给谁"))
    if PAY in kinds and not _AMOUNT.search(text) and not any(d.cue == "what" for d in out):
        out.append(Doubt("budget", 0.08, "", "这次花多少钱以内？", "你没说花多少钱以内"))
    if PUBLISH in kinds and not _PLACE.search(text):
        out.append(Doubt("where", 0.25, "", "要发在哪儿？", "你没说发在哪儿"))
    h = _HEDGE.search(text)
    if h and kinds - {READ, CREATE}:
        out.append(Doubt("hedge", 0.12, h.group(0), "你说得有点拿不准，要不要先说清楚想要什么样的结果？",
                         f"你说「{h.group(0)}」，听起来还没完全拿定主意"))
    return tuple(out)


def assess(text: str, history: tuple[str, ...] = (), *, attention: str = HERE, answered: bool = False) -> Assessment:
    """Weigh ``text``. ``history``: what the person asked before that went
    ahead (most recent last). ``attention``: here / flow / away.
    ``answered``: the person already answered a question about it (their
    answer is part of ``text``), so most of the doubt is settled."""
    acts = acts_in(text)
    doubts = doubts_in(text, acts)
    stakes = max((_act_cost(a.spec) for a in acts), default=1.0)
    wide = bool(_ALL.search(text)) or any(int(m.group("n")) >= 20 for m in _MANY.finditer(text))
    if wide and acts and max(a.spec.undo for a in acts) > 0.1:
        stakes *= 1.35
    stakes = min(10.0, stakes)
    p_keep = 1.0 - SLIP
    for d in doubts:
        p_keep *= 1.0 - d.weight
    p_wrong = 1.0 - p_keep
    familiar = ""
    for past in reversed(history):
        if past != text and resemblance(past, text) >= 0.55:
            familiar = past
            break
    if familiar:
        p_wrong = max(SLIP, p_wrong * FAMILIAR)
    if answered:
        p_wrong *= AFTER_ANSWER
    attention = attention if attention in ASK_COST else HERE
    return Assessment(acts, stakes, doubts, familiar, p_wrong, ASK_COST[attention], attention, wide)


# ---- the person, as the request and the moment show them ------------------------------------------

_EMOTION = re.compile(r"分手|前任|前男友|前女友|离婚|吵架|吵了一架|难过|伤心|心痛|心碎|生气|气死|恨死|恨(他|她)|烦死|受够了|崩溃"
                      r"|绝望|再也不想(看到|见到|想起)|想忘掉|忘掉(他|她)|忘了(他|她)|眼不见为净|不想再看|失恋|背叛|出轨"
                      r"|broke up|break ?up|my ex\b|divorce|heartbroken|can't stand|sick of|want to forget", re.I)
_IMPULSE = re.compile(r"马上|立刻|现在就|赶紧|一个不留|彻底|永远|干干净净|统统|全部删|全删|right now|immediately"
                      r"|every single|forever", re.I)
_DEAR = re.compile(r"照片|相片|相册|合照|合影|视频|录像|聊天记录|对话记录|日记|信件|情书|语音|录音|纪念|回忆"
                   r"|photos?|pictures?|videos?|chat (logs?|history)|diary|letters|voice (notes|memos)|memories", re.I)
_TOGETHER = re.compile(r"我们的|我们俩|和(他|她)的|跟(他|她)的|(他|她)的|前任|我和.{1,8}的|\bour\b|with (him|her)", re.I)
_BACKED_UP = re.compile(r"备份过|已经备份|备份好了|已备份|新(买的)?(硬盘|盘|U盘|移动硬盘)|空(的)?(硬盘|盘)|里面没(有)?(东西|用的)"
                        r"|backed up|new (drive|disk)|empty (drive|disk)", re.I)
_TRASH = re.compile(r"回收站|废纸篓|\btrash\b|recycle bin", re.I)
_WIPE = re.compile(r"格式化|重装系统|恢复出厂|抹掉(整个)?(磁盘|硬盘)|重新分区|format\s+(the\s+)?(disk|drive)|factory reset"
                   r"|wipe (the )?(disk|drive)", re.I)
_DEAR_KINDS = ("照片", "视频")
MOOD_DAYS = 3  # how far back a run of upset requests counts


@dataclass(frozen=True)
class Reading:
    """How the person seems, from their words, the hour and the past few days,
    and what in the request is dear to them."""

    emotion: str = ""  # words that show distress
    impulse: str = ""  # words that show haste
    late: bool = False  # the small hours
    moods: int = 0  # upset requests in the past few days, this one not counted
    dear: str = ""  # what in it holds memories
    together: str = ""  # someone else is in it

    @property
    def distressed(self) -> bool:
        return bool(self.emotion) or (bool(self.impulse) and (self.late or self.moods >= 2))

    def journal(self) -> dict:
        return {"emotion": self.emotion, "impulse": self.impulse, "late": self.late, "moods": self.moods,
                "dear": self.dear, "together": self.together, "distressed": self.distressed}


def read_person(text: str, recent=(), now: float | None = None, kinds=()) -> Reading:
    """``recent``: (when, what they asked) of earlier requests. ``kinds``: what
    the thing to be touched holds, as the usage reader counts it (most first)."""
    import time

    now = time.time() if now is None else now
    e, i, d, t = _EMOTION.search(text), _IMPULSE.search(text), _DEAR.search(text), _TOGETHER.search(text)
    moods = sum(1 for when, said in recent if said != text and 0 <= now - when <= MOOD_DAYS * 86400
                and _EMOTION.search(said))
    dear = d.group(0) if d else next((k for k, n in kinds[:1] if k in _DEAR_KINDS and n), "")
    return Reading(e.group(0) if e else "", i.group(0) if i else "", time.localtime(now).tm_hour < 5, moods, dear,
                   t.group(0) if t else "")


def wipes(text: str) -> str:
    """The words, when ``text`` asks to wipe a disk or a whole system (emptying the trash is not that)."""
    m = _WIPE.search(text)
    return m.group(0) if m and not _TRASH.search(text) else ""


def backed_up(text: str) -> str:
    """How the request says there is nothing to lose: "backup" or "new" (a new or empty disk); "" otherwise."""
    m = _BACKED_UP.search(text)
    if not m:
        return ""
    return "backup" if "备份" in m.group(0) or "backed" in m.group(0).lower() else "new"


def stakes_words(a: Assessment) -> str:
    main = a.main
    if main is None:
        return "做错了，改就是了。"
    line = main.spec.harm + "。"
    if a.wide:
        line += "而且这次涉及的是一大片，不是一两个。"
    return line


__all__ = ["ACCOUNT", "AWAY", "ASK_COST", "Act", "Assessment", "CREATE", "DELETE", "Doubt", "FLOW", "HERE",
           "INSTALL", "MODIFY", "ORGANIZE", "OTHERS", "PAY", "PUBLIC", "PUBLISH", "READ", "Reading", "SCHEDULE", "SELF",
           "SEND", "SYSTEM", "acts_in", "assess", "backed_up", "doubts_in", "read_person", "resemblance",
           "stakes_words", "wipes"]
