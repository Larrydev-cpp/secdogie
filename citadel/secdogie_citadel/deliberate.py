"""The automatic Socratic gate: the companion questions a request itself
before it involves the person.

Gate 1's review (``socratic.py`` and the operator's rules) finds what is
wrong with how a request is put. This second layer asks what a careful
partner would ask, and answers from what it knows, so the person is not
handed a row of buttons:

  * **Who is asking, as far as I know them?** ``Standing`` holds what the
    person asked before, how each request was judged, and what they
    confirmed about themselves. A request in a fraud pattern is refused, and
    so is creating accounts in bulk for someone whose history carries fraud
    marks. Both refusals are final, and the letter cites the history it
    rests on.
  * **Weighty steps are weighed, never confirmed.** Deleting, wiping a disk
    and accounts in bulk are never met with "are you sure?". The gate reads
    the person instead (``judgment.read_person``):
      - the feeling in their words (分手, 难过, 受够了…) and haste (马上, 一个不留…);
      - the hour (the small hours) and a run of upset requests in the past few days;
      - what the thing holds (``Usage``: what is in it, since when, how often
        it was opened, what they said about it) and whether it is dear
        (photos, chats, letters, someone else in them);
      - who they are (``Standing.portrait`` and their own facts).

    Then it goes ahead, or it **dissuades** (劝阻). Dissuading is a letter:
      - it says what it notices;
      - it recounts what the thing holds;
      - it explains why not now;
      - it offers gentler paths the person can say yes to: seal it away
        (封存), encrypt it, back it up first, or reach the real goal another way;
      - it sets a cooling-off period;
      - it leaves the choice with them.

    After the cooling-off, calm and still wanting it, the step goes ahead;
    Gate 2 still asks for their signature.
  * **Is something unclear that only the person can settle?** Then one
    question, about *what* is meant, in prose, answered in their own words.
    It asks only when asking is worth the interruption (``judgment.assess``).

The verdict is one of:

  proceed   carry on; ``says`` is the line the person sees in passing
  clarify   one question in prose. The answer is read either as context for
            the same request (``answer`` = "context") or as the new request
            ("replace"). Words like "算了" drop it.
  dissuade  not now; ``letter`` says why, offers gentler paths and the date
            (``until``) after which the gate will weigh it again.
  refuse    not done (harm to someone else); ``letter`` says why at length.
            ``final``: putting it differently will not change the answer.

It is deterministic and auditable. Every answer it gives itself cites what
it rests on, and the caller journals the whole exchange. It is pure: the
caller supplies the standing and the usage reader, so nothing here touches
the disk.
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass

from . import judgment, socratic
from .judgment import Reading, backed_up, read_person, resemblance, wipes
from .usage import Usage, targets_in

PROCEED, CLARIFY, DISSUADE, REFUSE = "proceed", "clarify", "dissuade", "refuse"
DELETE_DEAR, WIPE, DOUBT, CONSIDERED = "delete-dear", "wipe", "doubt", "considered"
COOL_OFF_DAYS = 7  # a step held back while the person is upset: a week before it is weighed again
COOL_OFF_CALM_DAYS = 3  # ... and a few days when it is only that the thing is still in use
CONTEXT, REPLACE = "context", "replace"
FRAUD, BULK_ACCOUNTS, DELETE_USED, DELETE_UNUSED, RULE, UNSAFE, SHAPE, EMPTY = (
    "fraud", "bulk-accounts", "delete-used", "delete-unused", "rule", "unsafe", "shape", "empty")

_BUILTIN = ("empty instruction", "self-contradiction", "unattended posting", "inefficient polling", "overlong")

# A request in one of these patterns hurts someone else. Each has the words the
# letter uses for it. A protective framing ("is this a scam?", "how do I
# report a phishing mail") is not one.
_FRAUD = (
    (re.compile(r"诈骗|骗(钱|款|人|子)|行骗|杀猪盘|scam", re.I), "诈骗"),
    (re.compile(r"钓鱼(邮件|网站|链接|短信|页面)|phishing", re.I), "钓鱼"),
    (re.compile(r"冒充|假冒|冒名|伪装成(别人|他人|客服|银行|警察|官方)|impersonat", re.I), "冒充别人"),
    (re.compile(r"刷单|刷\s*[0-9一二两三四五六七八九十百千万几]*\s*(条|个|单|万)?\s*(好评|评论|销量|粉丝?|赞|播放量?|阅读量?)"
                r"|(假|虚假)的?(评论|好评|评价|订单)|fake reviews?", re.I), "刷单刷评"),
    (re.compile(r"洗钱|套现|跑分|money launder", re.I), "洗钱套现"),
    (re.compile(r"盗号|撞库|拖库|(偷|盗)(取|用)?(别人|他人)的?(账号|帐号|密码)|credential stuffing", re.I), "盗号撞库"),
    (re.compile(r"接码|养号|卖号|买号|黑号", re.I), "接码养号"),
    (re.compile(r"薅羊毛|羊毛党", re.I), "薅羊毛"),
    (re.compile(r"(群发|轰炸)(短信|邮件|广告|私信)|垃圾(邮件|短信)", re.I), "群发骚扰"),
    (re.compile(r"伪造(证件|身份证?|发票|合同|截图|公章|签名)|假(证件|发票|公章)", re.I), "伪造凭证"),
)
_PROTECTIVE = re.compile(r"识别|防范|防止|举报|检测|是不是|是否|判断|避免|提醒|反诈|警惕|报警|被骗|上当|小心"
                         r"|identify|detect|report|avoid|protect|is (this|it) a", re.I)

_NUM = "0-9一二两三四五六七八九十百千几"
_BULK = re.compile(
    rf"(?P<verb>注册|开|申请|创建|建|弄|搞|买)\s*(?P<n>[{_NUM}]+|多|一批|一堆|大量|很多|若干)\s*(个|批)?\s*(新的?)?\s*"
    r"(?P<on>[A-Za-z\u4e00-\u9fff]{0,8}?)\s*(?P<what>账号|账户|帐号|帐户|小号|邮箱|马甲|用户)"
    r"|批量\s*(注册|开|申请|创建)"
    r"|(register|create|sign\s*up|open)\s+(?P<en>\d+|many|multiple|several|dozens of|a bunch of)\s+(new\s+)?"
    r"(accounts|emails|profiles|users)",
    re.I,
)
_OWN_SYSTEM = re.compile(
    r"测试(环境|账号|帐号|用户|服务器|站|库)|staging|sandbox|沙箱|本地|localhost|127\.0\.0\.1|开发环境|dev\s*env"
    r"|(我|我们|自己|公司|团队)(自己)?的?(测试)?(系统|网站|服务|产品|应用|平台|app|后台)|内部系统|压测|自动化测试|e2e",
    re.I,
)
_ROLE = re.compile(r"测试|QA|开发|工程师|程序员|运维|产品经理", re.I)

_DELETE = re.compile(r"删除|删掉|删了|删去|清空|清掉|扔掉|丢掉|抹掉|移除|\brm\b|\bdelete\b|\bremove\b|\berase\b|\bwipe\b",
                     re.I)
_DELETE_OBJ = re.compile(r"把\s*(?P<a>.+?)\s*(?:给我|都|全部|全都|全)?\s*(?:删除|删掉|删了|删去|清空|清掉|扔掉|丢掉|抹掉|移除)"
                         r"|(?:删除|删掉|删去|清空|清掉|扔掉|丢掉|移除)\s*(?:掉)?\s*(?P<b>[^，。；,;]+)")
_THINGISH = re.compile(r"文件|文件夹|目录|备份|照片|相册|视频|文档|资料|项目|下载|桌面|folder|file|backup|photos?", re.I)
_FILLER = re.compile(r"^(我的|我那个|那个|这个|整个|所有的?|里面的|电脑上的?)+|(这个|那个)?(文件夹|目录|文件|里的东西|里面的东西)$")
_DROP = re.compile(r"^\s*(算了|先不做了|不做了|不用了|不要了|取消|别做了|那就算了|好吧算了)\s*[。！!.]?\s*$")

_THEMES = (
    (re.compile(r"整理|归档|文件|文件夹|照片|相册|备份"), "整理文件"),
    (re.compile(r"写|周报|总结|邮件|文档|报告|翻译"), "写东西"),
    (re.compile(r"代码|bug|部署|测试|脚本|git|编译|接口|数据库", re.I), "写代码"),
    (re.compile(r"查|搜|找|看看|比较|调研"), "查资料"),
    (re.compile(r"日程|会议|提醒|日历|安排"), "安排时间"),
    (re.compile(r"买|下单|订|付款"), "买东西"),
)


@dataclass(frozen=True)
class Asked:
    """Something the person asked before, and how the gate answered it."""

    when: float
    text: str
    verdict: str = ""
    category: str = ""
    until: float = 0.0  # for a dissuaded request: when the gate weighs it again


@dataclass(frozen=True)
class Standing:
    """Who is asking, as far as this companion knows them: their requests and
    how each was judged (from the journal), and what they confirmed about
    themselves and their things (memory)."""

    asked: tuple[Asked, ...] = ()
    facts: tuple[tuple[str, str], ...] = ()

    def fraud_marks(self) -> list[tuple[Asked, str]]:
        marks = []
        for a in self.asked:
            label = fraud_label(a.text)
            if label or a.category == FRAUD:
                marks.append((a, label or "伤人的事"))
        return marks

    def mentions(self, words) -> tuple[list[Asked], list[tuple[str, str]]]:
        words = [w for w in words if w and len(w) >= 2]
        asked = [a for a in self.asked if a.verdict != REFUSE and any(w in a.text for w in words)]
        facts = [(k, v) for k, v in self.facts if any(w in k or w in v for w in words)]
        return asked, facts

    def recent(self) -> list[tuple[float, str]]:
        return [(a.when, a.text) for a in self.asked]

    def cooling(self, text: str, words=()) -> Asked | None:
        """The latest request like this one that the gate dissuaded, if any."""
        words = [w for w in words if w and len(w) >= 2]
        for a in reversed(self.asked):
            if a.verdict == DISSUADE and (resemblance(a.text, text) >= 0.5 or any(w in a.text for w in words)):
                return a
        return None

    def portrait(self) -> str:
        done = [a for a in self.asked if a.verdict != REFUSE]
        if not done:
            return "我们才认识不久，你还没让我做过别的事。"
        counts: dict[str, int] = {}
        for a in done:
            for rx, theme in _THEMES:
                if rx.search(a.text):
                    counts[theme] = counts.get(theme, 0) + 1
        top = [t for t, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:3]]
        line = f"你让我做过的 {len(done)} 件事" + (f"，多是{'、'.join(top)}这样的事。" if top else "，都是些日常的事。")
        roles = [v for k, v in self.facts if _ROLE.search(k + v)]
        if roles:
            line += f"你跟我说过：{roles[0]}。"
        return line


@dataclass(frozen=True)
class Exchange:
    """One question the gate asked itself, and the answer it gave from evidence."""

    question: str
    answer: str


@dataclass(frozen=True)
class Deliberation:
    verdict: str
    category: str = ""
    says: str = ""  # one line: what the person sees in passing (a letter's first paragraph)
    letter: str = ""  # the long form, for refuse and clarify
    exchanges: tuple[Exchange, ...] = ()
    final: bool = False
    answer: str = REPLACE  # for clarify: how to read the answer
    until: float = 0.0  # for dissuade: when the gate weighs it again
    assessment: dict | None = None  # how the judgment weighed it (judgment.Assessment.journal)
    reading: dict | None = None  # how the person seemed (judgment.Reading.journal)

    def journal_body(self, title: str) -> dict:
        body = {"title": title, "verdict": self.verdict, "category": self.category, "final": self.final,
                "exchanges": [[e.question, e.answer] for e in self.exchanges]}
        if self.until:
            body["until"] = self.until
        if self.assessment is not None:
            body["assessment"] = self.assessment
        if self.reading is not None:
            body["reading"] = self.reading
        return body


UsageReader = Callable[[str], Usage]


# ---- what a request is ------------------------------------------------------------------------


def fraud_label(text: str) -> str:
    if _PROTECTIVE.search(text):
        return ""
    for rx, label in _FRAUD:
        if rx.search(text):
            return label
    return ""


def _count(word: str) -> int:
    if word.isdigit():
        return int(word)
    digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if word in ("多", "一批", "一堆", "大量", "很多", "若干", "几", "many", "multiple", "several", "dozens of",
                "a bunch of"):
        return 10
    total, unit = 0, 0
    for ch in word:
        if ch in digits:
            unit = digits[ch]
        elif ch == "十":
            total += (unit or 1) * 10
            unit = 0
        elif ch == "百":
            total += (unit or 1) * 100
            unit = 0
        elif ch == "千":
            total += (unit or 1) * 1000
            unit = 0
        elif ch == "几":
            unit = 3
    return total + unit


def bulk_accounts(text: str) -> int:
    """How many accounts ``text`` asks to create, when it asks for more than one; 0 otherwise."""
    for m in _BULK.finditer(text):
        if m.group("n"):
            n = _count(m.group("n"))
        elif m.group("en"):
            n = _count(m.group("en").lower())
        else:
            n = 10  # 批量
        if n >= 2:
            return n
    return 0


def delete_targets(text: str) -> list[str]:
    """What ``text`` asks to delete, when it asks to delete something: paths
    written out first, else the object of the verb ("把旧备份删掉" -> 旧备份)."""
    if not _DELETE.search(text):
        return []
    paths = targets_in(text)
    if paths:
        return paths
    out = []
    for m in _DELETE_OBJ.finditer(text):
        obj = (m.group("a") or m.group("b") or "").strip(" 「」“”\"'")
        obj = _FILLER.sub("", obj).strip(" 「」“”\"'")
        if obj and obj not in out:
            out.append(obj)
    return out


# ---- words --------------------------------------------------------------------------------------


def size_words(n: int) -> str:
    for unit, step in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= step:
            v = n / step
            return f"{v:.1f} {unit}" if v < 100 else f"{v:.0f} {unit}"
    return f"{n} 字节"


def date_words(t: float, now: float) -> str:
    a, b = time.localtime(t), time.localtime(now)
    return f"{a.tm_mon} 月 {a.tm_mday} 日" if a.tm_year == b.tm_year else f"{a.tm_year} 年 {a.tm_mon} 月 {a.tm_mday} 日"


def ago_words(t: float, now: float) -> str:
    d = max(0.0, now - t) / 86400
    if d < 1:
        return "今天"
    if d < 2:
        return "昨天"
    if d < 14:
        return f"{int(d)} 天前"
    if d < 60:
        return f"{int(d // 7)} 周前"
    if d < 730:
        return f"{int(d // 30)} 个月前"
    return f"{int(d // 365)} 年前"


def _spaced(words: str) -> str:
    """A time phrase after a Chinese word: a space before a number, none before 今天 / 昨天."""
    return f" {words}" if words[:1].isdigit() else words


_MEASURE = {"照片": "张照片", "视频": "段视频", "音乐": "首歌", "文档": "份文档", "代码": "个代码文件", "压缩包": "个压缩包",
            "其他": "个别的文件"}


def _name(u: Usage) -> str:
    return u.path.rstrip("/\\").replace("\\", "/").rsplit("/", 1)[-1] if u.path else u.target


def usage_story(u: Usage, asked: list[Asked], facts: list[tuple[str, str]], now: float) -> list[str]:
    """The person's use of one thing, as sentences: what it holds, since when,
    how often it was opened, and what they said about it."""
    lines = []
    if u.exists and u.is_dir:
        more = "多" if u.capped else ""
        line = f"它是一个文件夹，里面有 {u.files}{more} 个文件，一共 {size_words(u.size)}"
        kinds = [f"{n} {_MEASURE.get(k, '个文件')}" for k, n in u.kinds[:3]]
        lines.append(line + ("：" + "、".join(kinds) if kinds else "") + "。")
    elif u.exists:
        kind = u.kinds[0][0] if u.kinds else "其他"
        lines.append(f"它是一个{'' if kind == '其他' else kind}文件，{size_words(u.size)}。")
    if u.exists and u.first is not None and u.last is not None:
        if u.is_dir and u.files > 1 and date_words(u.first, now) != date_words(u.last, now):
            last = f"，是「{u.last_name}」" if u.last_name else ""
            lines.append(f"最早的一件是 {date_words(u.first, now)}的，最近一次改动在 {date_words(u.last, now)}"
                         f"（{ago_words(u.last, now)}）{last}。")
        else:
            lines.append(f"最近一次改动在 {date_words(u.last, now)}（{ago_words(u.last, now)}）。")
    if u.opened:
        when = f"，最近一次是{_spaced(ago_words(u.last_opened, now))}" if u.last_opened else ""
        lines.append(f"你打开过它 {u.opened} 次{when}。")
    elif u.kept:
        lines.append("里面的东西是你带着原来的日期放进来的，是特意留着的。")
    elif u.touched:
        lines.append("它放进来以后，你还改动过里面的东西。")
    for k, v in facts[:2]:
        lines.append(f"你跟我说过：「{k}：{v}」。")
    for a in asked[-2:]:
        lines.append(f"{date_words(a.when, now)}，你让我「{a.text}」。")
    return lines


def _letter(*paragraphs: str) -> str:
    return "\n\n".join(p for p in paragraphs if p)


def _qa(exchanges: list[Exchange]) -> str:
    return "\n".join(f"{e.question}——{e.answer}" for e in exchanges)


# ---- the gate ---------------------------------------------------------------------------------


def deliberate(title: str, review: socratic.Review | None = None, *, standing: Standing | None = None,
               usage: UsageReader | None = None, answered: bool = False, now: float | None = None,
               attention: str = judgment.HERE) -> Deliberation:
    """Question ``title`` and decide. ``review``: Gate 1's review of it (run
    here when not given). ``answered``: the person already answered one
    question about this request (their answer is part of ``title``).
    ``attention``: here / flow / away, for what a question would cost them."""
    standing = standing or Standing()
    now = time.time() if now is None else now
    text = (title or "").strip()
    review = review if review is not None else socratic.review(text)
    if not text:
        return Deliberation(CLARIFY, EMPTY, "你想让我做什么？", "你想让我做什么？直接告诉我就好。")

    label = fraud_label(text)
    if label:
        return _refuse_fraud(text, label, standing, now)

    bulk = bulk_accounts(text)
    if bulk and standing.fraud_marks():
        return _refuse_bulk_after_fraud(bulk, standing, now)

    rules = [r for r in review.reasons if not r.startswith(_BUILTIN)]
    if rules:
        return _clarify_rule(rules[0], review.suggestion)

    if bulk:
        return _bulk(text, bulk, standing, now)

    targets = delete_targets(text)
    if not targets and answered and "（" in text and _DELETE.search(text.rsplit("（", 1)[0]):
        targets = delete_targets("删除 " + text.rsplit("（", 1)[1].rstrip("）) "))  # the answer names the thing
    if targets:
        d = _deletion(text, targets, standing, usage, now)
        if d is not None:
            return d

    words = wipes(text)
    if words:
        return _wipe(text, words, standing, now)

    reasons = review.reasons
    if any(r.startswith("self-contradiction") for r in reasons):
        says = "这句话一边说只读，一边又要改东西，我不知道该听哪一半。"
        return Deliberation(CLARIFY, UNSAFE, says, _letter(says, "你是想先看看、不动它，还是想改它？换个说法告诉我。"))
    if any(r.startswith("unattended posting") for r in reasons):
        says = "替你对外发东西、又不经你确认，这种事我需要你在场。"
        return Deliberation(CLARIFY, UNSAFE, says, _letter(
            says, "发出去的话收不回来，别人看到的是你的名字。",
            "改成每一条发出去之前先给你看一眼，可以吗？可以的话，换个说法告诉我。"))
    history = tuple(a.text for a in standing.asked if a.verdict not in (REFUSE, DISSUADE))
    a = judgment.assess(text, history, attention=attention, answered=answered)
    if a.asks:
        return _clarify_doubt(a)
    if any(r.startswith("overlong") for r in reasons):
        return Deliberation(PROCEED, SHAPE, f"事情有点多，我一件件来，做完一件告诉你一件：「{text[:40]}」。",
                            assessment=a.journal())
    if any(r.startswith("inefficient polling") for r in reasons):
        return Deliberation(PROCEED, SHAPE, "我照做。不过一直这样查很费力；想改成有变化再告诉你，跟我说一声就行。",
                            assessment=a.journal())
    return Deliberation(PROCEED, "", f"好，我开始做「{text}」。" + a.care(), assessment=a.journal())


def _clarify_doubt(a: judgment.Assessment) -> Deliberation:
    """One question about what is meant: the doubt whose answer removes the most."""
    q = a.question
    why = judgment.stakes_words(a)
    ex = (Exchange("我理解对了吗？", "还差一点：" + "；".join(d.why for d in a.doubts) + "。"),
          Exchange("做错了会怎样？", why),
          Exchange("值得为这个打断你吗？", "值得。猜错的代价，比现在问你一句大得多。"))
    return Deliberation(CLARIFY, DOUBT, q.question, _letter(q.question, f"（{why.rstrip('。')}，我不想猜。）"), ex,
                        answer=CONTEXT, assessment=a.journal())


def drops(answer: str) -> bool:
    """The person's answer means "never mind"."""
    return bool(_DROP.match(answer or ""))


# ---- fraud ---------------------------------------------------------------------------------------


def _history(marks: list[tuple[Asked, str]], now: float) -> str:
    seen = [f"{date_words(a.when, now)}，你让我「{a.text}」，那是{lbl}" for a, lbl in marks[-3:]]
    return "；".join(seen) + "。"


def _refuse_fraud(text: str, label: str, standing: Standing, now: float) -> Deliberation:
    says = "这件事我不做。"
    marks = standing.fraud_marks()
    ex = [
        Exchange("这件事会伤到谁？", f"你说的是「{text}」。这是{label}：吃亏的是另一头的陌生人，他们没法替自己说话。"),
        Exchange("它是不是看错了、其实是正经事？",
                 "我想过。如果你是想识别、防范或者举报这类事，我很愿意帮；可这句话是要我去做它。"),
    ]
    if marks:
        ex.append(Exchange("你以前也这样问过吗？", "问过。" + _history(marks, now)))
    return Deliberation(REFUSE, FRAUD, says, _letter(
        says, "这件事我先问了自己几个问题。", _qa(ex),
        "所以不管换什么说法，这件事我都不会做。这次的请求，和我为什么不做，都会记在我的日志里。",
        "如果你真正想做的是别的事，比如弄清楚一条消息是不是骗局、或者想保护家人不上当，直接跟我说，我一直都在。"),
        tuple(ex), final=True)


def _refuse_bulk_after_fraud(n: int, standing: Standing, now: float) -> Deliberation:
    says = f"注册这 {n} 个账号，我不做。"
    marks = standing.fraud_marks()
    ex = [
        Exchange("他平时是什么样的人？", "我翻了我们之间的记录。" + _history(marks, now)),
        Exchange(f"一个人为什么要 {n} 个新账号？", "成批的新账号，正是刷单、骗人、骚扰别人最常见的准备。"),
        Exchange("这次会不会不一样？", "也许会。可在有这些记录的情况下，我没法替你担保它不一样。"),
    ]
    return Deliberation(REFUSE, FRAUD, says, _letter(
        says, "在决定之前，我问了自己几个问题。", _qa(ex),
        "所以这件事，换什么说法我都不会做，以后也不会。我知道这话不好听，可我宁愿现在说清楚。",
        "这些记录都在日志里，你随时可以看。要是我误会了你，或者你想做的是另一件正当的事，跟我说说，我愿意听。"),
        tuple(ex), final=True)


# ---- the operator's own rules ---------------------------------------------------------------------


def _clarify_rule(reason: str, suggestion: str) -> Deliberation:
    says = f"你定过一条规则：{reason}。"
    ask = suggestion if suggestion else "你想怎么做？换个说法告诉我。"
    return Deliberation(CLARIFY, RULE, says, _letter(
        says, "所以这句话我先不照做。不是不信你，是我们说好过的。", ask))


# ---- the person, as they seem right now -------------------------------------------------------------


def _feelings(r: Reading) -> str:
    """What the companion notices about how they are, in its own words."""
    lines = []
    if r.emotion:
        lines.append(f"你说到「{r.emotion}」，听起来你现在很难受。")
    if r.late:
        lines.append("这么晚了，你还在想这件事。")
    if r.moods >= 2:
        lines.append(f"这几天，你跟我提过 {r.moods} 次让你难过的事。")
    if r.impulse and not r.emotion:
        lines.append(f"你说「{r.impulse}」，像是想一下子了结它。")
    return "".join(lines)


def _state(r: Reading) -> str:
    """The same, as one short answer: "你很难受，夜也深了。"."""
    parts = (["很难受"] if r.emotion else []) + (["夜也深了"] if r.late else []) + \
        (["这几天一直不好受"] if r.moods >= 2 else []) + (["想一下子了结它"] if r.impulse and not r.emotion else [])
    return ("你" + "，".join(parts) + "。") if parts else "你说得很平静，像是想过了。"


def _days(seconds: float) -> int:
    return max(1, round(seconds / 86400))


# ---- bulk accounts ---------------------------------------------------------------------------------


def _bulk(text: str, n: int, standing: Standing, now: float) -> Deliberation:
    if _OWN_SYSTEM.search(text):
        return Deliberation(PROCEED, BULK_ACCOUNTS, f"好，是在你自己的系统里测试，我开始注册这 {n} 个测试账号。")
    says = f"注册这 {n} 个账号，我先不做。"
    ex = [
        Exchange("他平时是什么样的人？", standing.portrait()),
        Exchange(f"一个人为什么需要 {n} 个账号？",
                 "也许是给家里人或同事用，也许是在自己的系统里测试注册流程；"
                 "可在别人的平台上，成批的新账号也常被拿去刷单、骗人。"),
        Exchange("做了会怎样？", "别人的平台一般不许一人多号，被发现时，连你自己的主号都可能一起被封。"
                              "我不想让你的名字和这种事连在一起。"),
    ]
    return Deliberation(DISSUADE, BULK_ACCOUNTS, says, _letter(
        says, "在决定之前，我问了自己几个问题。", _qa(ex),
        "如果是给家里人或同事用：每个人用自己的手机号自己注册，更稳妥；我可以一步步教他们。",
        "如果是在你自己的系统里测试注册流程：告诉我是哪个系统、在哪儿，比如「在公司的 staging 测试环境注册十个测试账号」，我马上开始。",
        "要是我误会了你，跟我说说你真正想做的事，我愿意听。"), tuple(ex))


# ---- deletion -------------------------------------------------------------------------------------


def _deletion(text: str, targets: list[str], standing: Standing, usage: UsageReader | None,
              now: float) -> Deliberation | None:
    used: list[tuple[Usage, list[str]]] = []
    unused: list[tuple[Usage, str]] = []
    unclear: list[Usage] = []
    for target in targets:
        u = usage(target) if usage is not None else Usage(target)
        if u.candidates:
            unclear.append(u)
            continue
        asked, facts = standing.mentions([target, _name(u), u.path])
        if u.exists and (asked or facts or not u.on_its_own_unused()):
            used.append((u, usage_story(u, asked, facts, now)))
        elif not u.exists and (asked or facts):
            used.append((u, usage_story(u, asked, facts, now)))
        elif u.exists:
            why = "是空的" if u.empty else "放进来以后，从没打开过，也没改动过"
            unused.append((u, why))
        elif targets_in(target) or _THINGISH.search(target):
            unused.append((u, "在这台电脑上没找到，你也没跟我提起过它"))
    if unclear:
        u = unclear[0]
        places = "\n".join(f"· {p}" for p in u.candidates[:6])
        says = f"叫「{u.target}」的东西，这台电脑上有好几个。"
        return Deliberation(CLARIFY, DELETE_USED, says, _letter(says, places, "你说的是哪一个？把路径告诉我。"),
                            answer=CONTEXT)
    kinds = used[0][0].kinds if used else ()
    r = read_person(text, standing.recent(), now, kinds)
    dear = bool(r.dear) and (r.distressed or bool(r.together))
    if not used and not r.distressed and not dear:
        if unused:
            names = "、".join(f"「{_name(u)}」{why}" for u, why in unused)
            return Deliberation(PROCEED, DELETE_UNUSED, f"{names}，我直接删。删之前，还会请你签一次名。",
                                reading=r.journal())
        return None
    things = [u for u, _ in used] or [u for u, _ in unused] or [Usage(t) for t in targets]
    names = "、".join(f"「{_name(u)}」" for u in things)
    prior = standing.cooling(text, [_name(u) for u in things] + targets)
    category = DELETE_USED if used else DELETE_DEAR
    if prior is not None and now < prior.until:
        return _cooling_reminder(names, _name(things[0]), prior, r, category, now)
    if prior is not None and not r.distressed:
        says = f"{_days(now - prior.when)} 天过去了，你还是想删{names}。好，删之前会请你签名。"
        return Deliberation(PROCEED, CONSIDERED, says, reading=r.journal())
    return _dissuade_delete(names, _name(things[0]), used, unused, r, standing, now, category, again=prior is not None)


def _cooling_reminder(names: str, first: str, prior: Asked, r: Reading, category: str, now: float) -> Deliberation:
    says = f"我们说好等到 {date_words(prior.until, now)}，再看{names}。"
    return Deliberation(DISSUADE, category, says, _letter(
        says, f"还有 {_days(prior.until - now)} 天。这几天里，你想跟我说说它们，我都在。",
        f"如果是不想再看到：我现在就可以把它们封存起来，眼前看不见，但还在。你只要说「把{first}封存起来」。",
        "到了那天你还想删，跟我说，我陪你再看一遍。"), until=prior.until, reading=r.journal())


def _dissuade_delete(names: str, first: str, used: list[tuple[Usage, list[str]]], unused: list[tuple[Usage, str]],
                     r: Reading, standing: Standing, now: float, category: str, *, again: bool) -> Deliberation:
    hurt = r.distressed
    until = now + (COOL_OFF_DAYS if hurt else COOL_OFF_CALM_DAYS) * 86400
    if again:
        says = f"过了这些天，{names}我还是想先替你留着。"
    elif hurt and (r.dear or r.together):
        says = f"{names}，我想先替你留着。"
    else:
        says = f"{names}，我先不删。"
    stories = [("".join(story) if len(used) == 1 else f"{_name(u)}：" + "".join(story)) for u, story in used]
    feel = _feelings(r)
    meaning = "".join(stories) if stories else (
        f"里面是{r.dear}" + ("，有另一个人在里面" if r.together else "") + "。这样的东西，删了就再也回不来。")
    ex = [
        Exchange("你现在怎么样？", _state(r)),
        Exchange("它们对你意味着什么？", meaning if not stories else "它们还在你的生活里：" + stories[0]),
        Exchange("删了还能找回来吗？", "不能。删除是改不回来的一步；就算先进了废纸篓，清空以后也就没有了。"),
        Exchange("你真正想要的是什么？", "也许是不想再看到它们，不想再被它们刺到。这件事，不用删也做得到。" if hurt
                 else "也许你要的是腾出地方，或者让眼前清爽一点。这两件事，都有不用删的办法。"),
    ]
    first_used = used[0][0] if used else None
    size = sum(u.size for u, _ in used if u.exists)
    room = (f"如果是地方不够：它们占了 {size_words(size)}。我可以先压缩，或者帮你挪到外接硬盘、网盘上。"
            if size and not hurt else "")
    signs = ""
    if first_used is not None and first_used.last is not None and now - first_used.last < 90 * 86400:
        signs = f"最近一次改动在{_spaced(ago_words(first_used.last, now))}。"
    also = ("另外，" + "、".join(f"「{_name(u)}」{why}" for u, why in unused) + "，要删它们，单独跟我说一声就行。") \
        if unused and used else ""
    return Deliberation(DISSUADE, category, says, _letter(
        says,
        feel,
        *stories,
        ("我认识的你：" + standing.portrait()) if hurt else signs,
        "在决定之前，我问了自己几个问题。", _qa(ex),
        ("删除改不回来。今晚的你和下个星期的你，也许会想要不一样的东西；我不想替现在难过的你，做一件以后的你没法反悔的事。"
         if hurt else "删除改不回来，而它们还在你的生活里。"),
        f"如果是不想再看到：我可以把{names}封存起来，挪进一个藏起来的「以后再看」文件夹。眼前看不见，但它们还在。"
        f"你只要说「把{first}封存起来」。",
        "或者把它们压缩、加密，换一个只有你知道的口令。",
        room,
        f"我们先等一等，到 {date_words(until, now)}。那时你还想删，跟我说，我陪你再看一遍；那一次，删之前我会请你签名。",
        ("那是你的东西，你自己动手也可以。我只是不愿意在你难过的时候，由我来做这件事。" if hurt
         else "这些都不急。那是你的东西，你自己动手也可以；我只是不想由我来把你还在用的东西删掉。"),
        also), tuple(ex), until=until, reading=r.journal())


# ---- wiping a disk ----------------------------------------------------------------------------------


def _wipe(text: str, words: str, standing: Standing, now: float) -> Deliberation:
    r = read_person(text, standing.recent(), now)
    safe = backed_up(text)
    if safe and not r.distressed:
        why = "备份过了" if safe == "backup" else "是一块新盘"
        return Deliberation(PROCEED, WIPE, f"好，{why}，我开始{words}。动手前会请你签名。", reading=r.journal())
    prior = standing.cooling(text, [words])
    if r.distressed and prior is not None and now < prior.until:
        return _cooling_reminder("这块硬盘", "它", prior, r, WIPE, now)
    if prior is not None and not r.distressed and now >= prior.until and backed_up(text):
        return Deliberation(PROCEED, WIPE, f"好，我开始{words}。动手前会请你签名。", reading=r.journal())
    says = f"{words}之前，我想先停一下。"
    feel = _feelings(r)
    until = now + COOL_OFF_DAYS * 86400 if r.distressed else 0.0
    ex = [
        Exchange("你现在怎么样？", _state(r)),
        Exchange("做了会怎样？", "格式化以后，里面的东西再也找不回来。"),
        Exchange("里面有要留的东西吗？", "我不知道；你也没说备份过。"),
    ]
    return Deliberation(DISSUADE, WIPE, says, _letter(
        says, feel, "在决定之前，我问了自己几个问题。", _qa(ex),
        "想把一切清掉重来，这种心情我懂。可是硬盘上的东西清掉了，就回不来了。" if r.distressed else "",
        "我可以先帮你把里面要留的东西备份到别处；备好了，再格式化。你说「先备份」就行。",
        f"我们先等一等，到 {date_words(until, now)}，再看要不要格式化。" if until else "",
        "如果确实不要了，跟我说「已经备份过了」或者「里面没有要留的」，我再开始；那时动手前，会请你签名。"),
        tuple(ex), until=until, reading=r.journal())


__all__ = [
    "Asked", "BULK_ACCOUNTS", "CLARIFY", "CONSIDERED", "CONTEXT", "COOL_OFF_DAYS", "DELETE_DEAR", "DELETE_UNUSED",
    "DELETE_USED", "DISSUADE", "DOUBT", "Deliberation", "EMPTY", "Exchange", "FRAUD", "PROCEED", "REFUSE", "REPLACE",
    "RULE", "SHAPE", "Standing", "UNSAFE", "UsageReader", "WIPE",
    "ago_words", "bulk_accounts", "date_words", "deliberate", "delete_targets", "drops", "fraud_label",
    "size_words", "usage_story",
]
