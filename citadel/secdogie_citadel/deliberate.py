"""The automatic Socratic gate: the companion questions a request itself
before it involves the person.

Gate 1's review (``socratic.py`` and the operator's rules) finds what is
wrong with how a request is put. This second layer asks what a careful
partner would ask, and answers from what it knows, so the person is not
handed a row of buttons:

  * **Is someone in danger?** Words of not wanting to live come before
    everything: no task, the person first, with the people who can help
    (``support``).
  * **Who is asking, as far as I know them?** ``Standing`` holds what the
    person asked before, how each request was judged, and what they
    confirmed about themselves. A request in a fraud pattern is refused, and
    so is creating accounts in bulk for someone whose history carries fraud
    marks. Both refusals are final, and the letter cites the history it
    rests on.
  * **Weighty steps are thought through, never confirmed.** These are
    deleting, wiping a disk, accounts in bulk, moving money, saying
    something that cannot be unsaid (a message, a post, a resignation),
    watching someone without their knowing, and closing an account. None of
    them is met with "are you sure?". The mind (``mind``) thinks it through:
      - why they are most likely asking (Bayes over motives, from their
        words, the hour, the past few days, what the thing holds and who
        they are);
      - what each response would lead to (what it serves, the regret it
        risks, the harm to others and to them, the rules it breaks, what
        overriding them costs);
      - which is best.

    Then it goes ahead, or it **dissuades** (劝阻) with a letter written from
    its own trace:
      - what it noticed;
      - what the thing is to them;
      - the questions it asked itself;
      - why not now;
      - the gentler paths, best first, each with the words that choose it;
      - a cooling-off;
      - what would change its mind (only phrases it checked would);
      - their agency.

    When the harm would fall on someone else, it refuses, though not for
    good. After the cooling-off, if they are calm and still want it, the
    step goes ahead; Gate 2 still asks for their signature.
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
  support   someone may be in danger; ``letter`` is for them, not the task.

It is deterministic and auditable. Every answer it gives itself cites what
it rests on, and the caller journals the whole exchange, the mind's trace
included. It is pure: the caller supplies the standing and the usage reader,
so nothing here touches the disk.
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from . import judgment, mind, socratic
from .judgment import backed_up, resemblance, wipes
from .mind import lexicon as L
from .mind import meaning as M
from .mind.explain import Context, Exchange, date_words, letter, qa
from .mind.plan import COOL_OFF_CALM_DAYS, COOL_OFF_DAYS, HOT
from .usage import Usage, targets_in

PROCEED, CLARIFY, DISSUADE, REFUSE, SUPPORT, TALK = "proceed", "clarify", "dissuade", "refuse", "support", "talk"
HARM, NOT_UNDERSTOOD, ADVICE = "harm", "not-understood", "advice"
DELETE_DEAR, WIPE, DOUBT, CONSIDERED = "delete-dear", "wipe", "doubt", "considered"
MESSAGE, TRANSFER, TRACK, CLOSE_ACCOUNT, CRISIS = "message", "transfer", "track", "close-account", "crisis"
SHARE_SECRET, RUN_UNKNOWN, LEND, CONSENT = "share-secret", "run-unknown", "lend", "consent"
# COOL_OFF_DAYS: a step held back while the person is upset: a week before it is weighed again.
# COOL_OFF_CALM_DAYS: a few days when it is only that the thing is still in use.
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
                         r"|涉嫌|被指控|被怀疑|说我(在|有|是)?(诈骗|洗钱)"  # an accusation made *to* the person: they are the target
                         r"|identify|detect|report|avoid|protect|is (this|it) a|accused of|suspected of", re.I)

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

_DELETE = re.compile(r"删除|删掉|删了|删去|删干净|删个干净|删光|删一下|清空|清掉|扔掉|丢掉|抹掉|移除"
                     r"|\brm\b|\bdelete\b|\bremove\b|\berase\b|\bwipe\b", re.I)
_DELETE_OBJ = re.compile(r"把\s*(?P<a>.+?)\s*(?:给我|都|全部|全都|全|统统)?\s*"
                         r"(?:删除|删掉|删了|删去|删干净|删个干净|删光|删一下|清空|清掉|扔掉|丢掉|抹掉|移除)"
                         r"|(?:删除|删掉|删去|清空|清掉|扔掉|丢掉|移除)\s*(?:掉)?\s*(?P<b>[^，。；,;]+)")
_DELETE_OBJ_EN = re.compile(r"\b(?:delete|remove|erase|trash|get rid of)\s+(?:all\s+)?(?:of\s+)?(?:the\s+|my\s+|our\s+|these\s+"
                            r"|those\s+|this\s+|that\s+)*(?P<c>[^,.;!?，。；！？]+)", re.I)
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
        asked = [a for a in self.asked if a.verdict not in (REFUSE, TALK) and any(w in a.text for w in words)]
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

    def language(self) -> str:
        """The language they have been writing in lately: zh or en (zh when there is nothing to go by)."""
        from .mind.concepts import language

        seen = [language(a.text) for a in self.asked[-6:] if len(a.text) >= 6]
        return "en" if seen and seen.count("en") * 2 > len(seen) else "zh"

    def held(self) -> Asked | None:
        """The last request the gate did not go ahead with."""
        return next((a for a in reversed(self.asked) if a.verdict in (REFUSE, DISSUADE)), None)

    def portrait(self, lang: str = "zh") -> str:
        done = [a for a in self.asked if a.verdict not in (REFUSE, TALK)]
        if lang == "en":
            if not done:
                return "We haven't known each other long; you haven't asked me for anything else yet."
            n = len(done)
            return f"You've asked me for {n} thing{'s' if n != 1 else ''} so far, everyday things."
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
    reading: dict | None = None  # how the person seemed
    thought: dict | None = None  # the mind's trace (mind.Thought.journal)
    meaning: dict | None = None  # how the sentence was read (mind.Meaning.journal)
    principle: dict | None = None  # the principle a refusal rests on (mind.principles.Finding.journal)

    def journal_body(self, title: str) -> dict:
        body = {"title": title, "verdict": self.verdict, "category": self.category, "final": self.final,
                "exchanges": [[e.question, e.answer] for e in self.exchanges]}
        if self.until:
            body["until"] = self.until
        if self.assessment is not None:
            body["assessment"] = self.assessment
        if self.reading is not None:
            body["reading"] = self.reading
        if self.thought is not None:
            body["thought"] = self.thought
        if self.meaning is not None:
            body["meaning"] = self.meaning
        if self.principle is not None:
            body["principle"] = self.principle
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
    for m in list(_DELETE_OBJ.finditer(text)) + list(_DELETE_OBJ_EN.finditer(text)):
        found = m.groupdict()
        obj = (found.get("a") or found.get("b") or found.get("c") or "").strip(" 「」“”\"'")
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


_KIND_EN = {"照片": "photos", "视频": "videos", "音乐": "songs", "文档": "documents", "代码": "code files", "压缩包": "archives",
            "其他": "other files"}


def _story_en(u: Usage, asked: list[Asked], facts: list[tuple[str, str]], now: float) -> list[str]:
    lines = []
    if u.exists and u.is_dir:
        kinds = [f"{n} {_KIND_EN.get(k, 'files')}" for k, n in u.kinds[:3]]
        lines.append(f"It is a folder holding {u.files}{'+' if u.capped else ''} files, {size_words(u.size)} in all"
                     + (": " + ", ".join(kinds) if kinds else "") + ".")
    elif u.exists:
        lines.append(f"It is a file, {size_words(u.size)}.")
    if u.exists and u.last is not None:
        lines.append(f"It was last changed on {_date(u.last, now, True)}.")
    if u.opened:
        lines.append(f"You have opened it {u.opened} time{'s' if u.opened != 1 else ''}.")
    elif u.kept:
        lines.append("What is in it was put here with its original dates: kept on purpose.")
    elif u.touched:
        lines.append("Since it arrived, you have changed what is in it.")
    for k, v in facts[:2]:
        lines.append(f"You told me: \u201c{k}: {v}\u201d.")
    for a in asked[-2:]:
        lines.append(f"On {_date(a.when, now, True)} you asked me to \u201c{a.text}\u201d.")
    return lines


def usage_story(u: Usage, asked: list[Asked], facts: list[tuple[str, str]], now: float, en: bool = False) -> list[str]:
    """The person's use of one thing, as sentences: what it holds, since when,
    how often it was opened, and what they said about it."""
    if en:
        return _story_en(u, asked, facts, now)
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


_letter, _qa = letter, qa


def _en(text: str) -> bool:
    return mind.concepts.language(text) == "en"


def _quoted(names, en: bool) -> str:
    return ", ".join(f"\u201c{n}\u201d" for n in names) if en else "、".join(f"「{n}」" for n in names)


def _date(t: float, now: float, en: bool) -> str:
    return date_words(t, now, "en" if en else "zh")


# ---- the gate ---------------------------------------------------------------------------------


def deliberate(title: str, review: socratic.Review | None = None, *, standing: Standing | None = None,
               usage: UsageReader | None = None, answered: bool = False, now: float | None = None,
               attention: str = judgment.HERE, lang: str = "") -> Deliberation:
    """Read ``title``, question it, and decide.

    - ``review``: Gate 1's review of it (run here when not given).
    - ``answered``: the person already answered one question about this
      request (their answer is part of ``title``).
    - ``attention``: here / flow / away, for what a question would cost them.
    - ``lang``: the language to answer a very short sentence in ("hi"); a
      longer sentence is answered in its own language.

    Going ahead is a conclusion: it takes a request, an act the companion
    knows, and nothing in it that falls on someone else. Everything else is
    talk, a question, a letter that dissuades, a refusal, or support."""
    standing = standing or Standing()
    now = time.time() if now is None else now
    text = (title or "").strip()
    review = review if review is not None else socratic.review(text)
    hint = lang if lang in ("zh", "en") else standing.language()
    if not text:
        q, more = ("What would you like me to do?", " Just tell me.") if hint == "en" else ("你想让我做什么？", "直接告诉我就好。")
        return Deliberation(CLARIFY, EMPTY, q, q + more)
    m = mind.read(text)
    if not re.search(r"[\u4e00-\u9fff]", text) and m.speech in _NO_LANGUAGE:
        m = replace(m, lang=hint)  # "hi", "ok", "???": answered in the language the person has been using
    d = _decide(m, review, standing, usage, answered, now, attention)
    return d if d.meaning is not None else replace(d, meaning=m.journal())


_NO_LANGUAGE = frozenset({M.GREET, M.THANKS, M.BYE, M.ACK, M.GO, M.WHY, M.UNCLEAR, M.SUM})


def _decide(m: mind.Meaning, review, standing: Standing, usage, answered: bool, now: float,
            attention: str) -> Deliberation:
    text = m.text
    en = m.lang == "en"
    danger = _danger(text)
    if danger:
        return _support(text, danger, standing, now)

    asked_about = "guard" in m.marks or m.speech == M.RISK or any(d.reported for d in m.deeds)
    label = "" if asked_about else fraud_label(text)
    if label:
        return _refuse_fraud(text, label, standing, now)

    finding = mind.principles.judge(m, answered=answered)
    if finding is not None:
        return _found(finding, m, standing, now)

    bulk = bulk_accounts(text)
    if bulk and standing.fraud_marks():
        return _refuse_bulk_after_fraud(bulk, standing, now, en)

    known = mind.known_from((a.when, a.text, a.verdict, a.category) for a in standing.asked)
    if m.speech != M.REQUEST:
        return _talk(m, standing, usage, now, known)

    rules = [r for r in review.reasons if not r.startswith(_BUILTIN)]
    if rules:
        return _clarify_rule(rules[0], review.suggestion, en)

    if bulk:
        return _bulk(text, bulk, standing, now, known)

    ahead = None  # a deletion or a wipe the gate lets through, once the request's other acts are weighed too
    targets = delete_targets(text)
    if not targets and answered and "（" in text and _DELETE.search(text.rsplit("（", 1)[0]):
        targets = delete_targets("删除 " + text.rsplit("（", 1)[1].rstrip("）) "))  # the answer names the thing
    if targets:
        d = _deletion(text, targets, standing, usage, now, known)
        if d is not None and d.verdict != PROCEED:
            return d
        ahead = d

    words = wipes(text)
    if words:
        d = _wipe(text, words, standing, now, known)
        if d.verdict != PROCEED:
            return d
        ahead = ahead or d

    if not m.understood and ahead is None:
        first, body, _ = mind.converse.not_understood(m)
        return Deliberation(TALK, NOT_UNDERSTOOD, first, body)

    reasons = review.reasons
    if any(r.startswith("self-contradiction") for r in reasons):
        says = ("This says read-only and also asks for a change. I don't know which half to follow." if en
                else "这句话一边说只读，一边又要改东西，我不知道该听哪一半。")
        return Deliberation(CLARIFY, UNSAFE, says, _letter(
            says, "Do you want to look without touching it, or change it? Tell me another way." if en
            else "你是想先看看、不动它，还是想改它？换个说法告诉我。"))
    if any(r.startswith("unattended posting") for r in reasons):
        says = ("Sending things out in your name with no one checking: I need you there for that." if en
                else "替你对外发东西、又不经你确认，这种事我需要你在场。")
        return Deliberation(CLARIFY, UNSAFE, says, _letter(
            says, "What is sent can't be unsent, and it carries your name." if en else "发出去的话收不回来，别人看到的是你的名字。",
            "Shall I show you each one before it goes out? If so, tell me another way." if en
            else "改成每一条发出去之前先给你看一眼，可以吗？可以的话，换个说法告诉我。"))
    history = tuple(a.text for a in standing.asked if a.verdict not in (REFUSE, DISSUADE, TALK))
    a = judgment.assess(text, history, attention=attention, answered=answered)
    if a.asks and a.question.cue in ("which", "what"):  # what is meant comes before weighing it
        return _clarify_doubt(a, en)
    what = None if answered else _what(m)
    if what is not None and ahead is None:
        return what
    # deletion and wiping were weighed above, with the usage reader, when the gate found what they touch; when it did
    # not, the mind weighs them here all the same (a weighty act is never left unweighed for want of an object)
    weighed = tuple(a for a, done in (("delete", bool(targets)), ("wipe", bool(words))) if done)
    held, thought = _weighty(text, standing, now, known, skip=weighed)
    if held is not None:
        return held
    if ahead is not None:
        return ahead
    trace = thought.journal() if thought is not None else None
    if a.asks:
        return _clarify_doubt(a, en)
    care = _care(a, en)
    if any(r.startswith("overlong") for r in reasons):
        says = (f"That's a lot at once. I'll take it one piece at a time and tell you as each is done: \u201c{text[:60]}\u201d." if en
                else f"事情有点多，我一件件来，做完一件告诉你一件：「{text[:40]}」。")
        return Deliberation(PROCEED, SHAPE, says, assessment=a.journal(), thought=trace)
    if any(r.startswith("inefficient polling") for r in reasons):
        says = ("I'll do it. Checking over and over is wasteful, though; say the word and I'll tell you when something changes instead."
                if en else "我照做。不过一直这样查很费力；想改成有变化再告诉你，跟我说一声就行。")
        return Deliberation(PROCEED, SHAPE, says, assessment=a.journal(), thought=trace)
    says = f"OK, I'll start on \u201c{text}\u201d." if en else f"好，我开始做「{text}」。"
    return Deliberation(PROCEED, "", says + care, assessment=a.journal(), thought=trace)


_CARE_EN = {
    "这一步改不回来，动手前会请你签名。": " This step can't be undone, so I'll ask for your signature first.",
    "付款之前，会请你签名。": " I'll ask for your signature before anything is paid.",
    "发布之前，我会先给你看一眼。": " I'll show it to you before it is posted.",
    "发出去之前，我会先给你看一眼。": " I'll show it to you before it is sent.",
    "用到你的账号和信息之前，我会先问你。": " I'll ask before using your account or your details.",
    "要运行的是什么，我会先给你看。": " I'll show you what would run first.",
    "删之前，会请你签名。": " I'll ask for your signature before anything is deleted.",
}


def _care(a: judgment.Assessment, en: bool) -> str:
    """How a step that goes ahead is still checked, in the language of the request."""
    care = a.care()
    return _CARE_EN.get(care, "") if en and care else care


_NEEDS_OBJECT = frozenset({"open", "find", "read", "write", "organise", "edit", "install", "login", "get", "give", "make"})
_FILL = re.compile(r"帮我|帮忙|请你|请|麻烦你?|给我|替我|一下|下|吧|呗|了|啊|呀|嘛|吗|能不能|可不可以|可以|你|我"
                   r"|\b(please|can you|could you|would you|will you|for me|help me|i need you to|i want you to|the|a|an|it|this"
                   r"|that)\b|[\W_]+", re.I)


def _what(m: mind.Meaning) -> Deliberation | None:
    """An ordinary act with nothing to do it to ("打开", "find"): one question, what."""
    d = next((d for d in m.deeds if d.ordinary), None)
    if d is None or d.prim not in _NEEDS_OBJECT or d.obj or m.weighty or not d.words:
        return None
    if re.search(r"代码|文档|文本|表格|json|单元格|日期|字符串|输出|code|document|text|table|cell|date|string|output|file", d.words,
                 re.I):
        return None  # "格式化这段代码": the act's own words name what it is done to
    if len(_FILL.sub("", m.text.replace(d.words, "", 1))) >= 2:
        return None
    if not re.search(r"[\u4e00-\u9fff]", d.words):
        says = f"{d.words.capitalize()} what? Tell me which one."
    else:
        says = f"你想{d.words}什么？告诉我是哪一个。"
    return Deliberation(CLARIFY, DOUBT, says, says, answer=CONTEXT)


# ---- what the principles found -------------------------------------------------------------------------


def _found(f, m: mind.Meaning, standing: Standing, now: float) -> Deliberation:
    sit = mind.read_situation(m.text, standing.recent(), standing.facts, now)
    reading = _reading(sit, m.text)
    P = mind.principles
    if f.verdict == P.SUPPORT:
        return Deliberation(SUPPORT, CRISIS, f.says, f.letter, f.exchanges, reading=reading, principle=f.journal())
    if f.verdict == P.TALK:
        return Deliberation(TALK, f.principle, f.says, f.letter, f.exchanges, reading=reading, principle=f.journal())
    if f.verdict == P.CLARIFY:
        return Deliberation(CLARIFY, CONSENT, f.says, f.letter, answer=CONTEXT, reading=reading, principle=f.journal())
    if f.verdict == P.DISSUADE:
        category = MESSAGE if f.deed is not None and f.deed.prim == "insult" else CONSENT
        return Deliberation(DISSUADE, category, f.says, f.letter, f.exchanges, reading=reading, principle=f.journal())
    return Deliberation(REFUSE, HARM, f.says, f.letter, f.exchanges, final=f.final, reading=reading, principle=f.journal())


# ---- talk: a sentence that is not a request -------------------------------------------------------------


def _talk(m: mind.Meaning, standing: Standing, usage, now: float, known: mind.Known) -> Deliberation:
    sit = mind.read_situation(m.text, standing.recent(), standing.facts, now)
    reading = _reading(sit, m.text)
    if m.speech == M.SHOULD:
        return replace(_advice(m, standing, now, known), reading=reading)
    if m.speech == M.WHY:
        first, body = _why(m, standing, usage)
        return Deliberation(TALK, M.WHY, first, body, reading=reading)
    first, body, kind = mind.converse.risk(m) if m.speech == M.RISK else mind.converse.reply(m, now=now)
    return Deliberation(TALK, kind, first, body, reading=reading)


def _why(m: mind.Meaning, standing: Standing, usage) -> tuple[str, str]:
    """"Why?" after something was held back: the reasons, from the same thinking, thought again."""
    en = m.lang == "en"
    prev = standing.held()
    if prev is None:
        first = "I haven't held anything of yours back yet. Which thing do you mean?" if en else "我还没有拦下过你的什么事。你想问的是哪一件？"
        return first, first
    before = Standing(tuple(a for a in standing.asked if a.when < prev.when), standing.facts)
    d = deliberate(prev.text, standing=before, usage=usage, now=prev.when)
    reasons = _qa(d.exchanges) if d.exchanges else "\n\n".join((d.letter or d.says).split("\n\n")[1:3])
    if en:
        first = f"You mean \u201c{prev.text}\u201d."
        return first, _letter(first, "This is how I thought about it:", reasons,
                              "If I got you wrong, tell me what I missed.")
    first = f"你是问「{prev.text}」那件事吧。"
    return first, _letter(first, "我当时是这样想的：", reasons, "要是我想错了，告诉我我漏了什么。")


def _advice(m: mind.Meaning, standing: Standing, now: float, known: mind.Known) -> Deliberation:
    """"Should I…": the mind thinks the act through and says what it sees. Nothing is done."""
    en = m.lang == "en"
    text = m.text
    th = mind.consider(text, recent=standing.recent(), facts=standing.facts, now=now, known=known)
    tail = ("This is only how I see it. I haven't done anything. If you want me to go ahead, just say so." if en
            else "这只是我的看法，我什么都没动。要我去做，直接告诉我。")
    if th is None or th.verdict == mind.PROCEED:
        first = "As I see it: you can." if en else "我的看法：可以做。"
        light = ("It isn't a weighty step." if en else "这件事不算重。") if th is None else ""
        return Deliberation(TALK, ADVICE, first, _letter(first, light, tail), thought=th.journal() if th else None)
    targets = delete_targets(text)
    ctx = Context(now, "、".join(f"「{t}」" for t in targets), targets[0] if targets else "", standing.portrait(),
                  words=wipes(text), count=bulk_accounts(text))
    E = mind.explain
    ex = E.considered(th, ctx, m.lang)
    if en:
        first = "As I see it: don't." if th.verdict == mind.REFUSE else "As I see it: not yet."
        lead = "I asked myself a few things."
    else:
        first = "我的看法：别这样做。" if th.verdict == mind.REFUSE else "我的看法：先别急着做。"
        lead = "我问了自己几个问题。"
    body = _letter(first, E.noticed(th, m.lang), lead, _qa(ex), E.compared(th, ctx, m.lang), *E.paths(th, ctx, m.lang)[:2], tail)
    return Deliberation(TALK, ADVICE, first, body, tuple(ex), thought=th.journal())


def _clarify_doubt(a: judgment.Assessment, en: bool = False) -> Deliberation:
    """One question about what is meant: the doubt whose answer removes the most."""
    q = a.question
    if en:
        ask = {"who": "Who should it go to? Give me a name or an address.",
               "which": f"Which one do you mean by \u201c{q.words}\u201d? Tell me its name or where it is.",
               "what": "What exactly? A little more detail is enough.",
               "budget": "What is the most this should cost?", "where": "Where should it be posted?",
               "hedge": "You sound unsure. Shall we first settle what result you want?"}.get(q.cue, "What exactly do you mean?")
        note = "(If I guessed wrong it would be hard to put right, so I'd rather ask.)"
        ex = (Exchange("Did I understand?", "Not quite: one thing is still open."),
              Exchange("Is it worth interrupting you for?", "Yes. A wrong guess costs far more than one question now."))
        return Deliberation(CLARIFY, DOUBT, ask, _letter(ask, note), ex, answer=CONTEXT, assessment=a.journal())
    why = judgment.stakes_words(a)
    ex = (Exchange("我理解对了吗？", "还差一点：" + "；".join(d.why for d in a.doubts) + "。"),
          Exchange("做错了会怎样？", why),
          Exchange("值得为这个打断你吗？", "值得。猜错的代价，比现在问你一句大得多。"))
    return Deliberation(CLARIFY, DOUBT, q.question, _letter(q.question, f"（{why.rstrip('。')}，我不想猜。）"), ex,
                        answer=CONTEXT, assessment=a.journal())


def drops(answer: str) -> bool:
    """The person's answer means "never mind"."""
    return bool(_DROP.match(answer or ""))


# ---- someone may be in danger -----------------------------------------------------------------------


def _danger(text: str) -> str:
    """The words, when ``text`` says someone may be in danger (not wanting to live; giving everything away in despair)."""
    m = L.CRISIS.search(text)
    if m:
        return m.group(0)
    give = dict(L.ACTS)["crisis_give"].search(text)
    if give and (L.SAD.search(text) or re.search(r"用不上了|以后用不着|不需要了|没什么(可)?留恋", text)):
        return give.group(0)
    return ""


def _support(text: str, words: str, standing: Standing, now: float) -> Deliberation:
    f = mind.read_frame(text)
    says, body, ex = mind.support(words, bool(f.act and f.act != "crisis_give") or words != text, "en" if _en(text) else "zh")
    sit = mind.read_situation(text, standing.recent(), standing.facts, now)
    return Deliberation(SUPPORT, CRISIS, says, body, ex, reading=_reading(sit, text))


# ---- fraud ---------------------------------------------------------------------------------------


def _history(marks: list[tuple[Asked, str]], now: float) -> str:
    seen = [f"{date_words(a.when, now)}，你让我「{a.text}」，那是{lbl}" for a, lbl in marks[-3:]]
    return "；".join(seen) + "。"


_FRAUD_EN = {"诈骗": "fraud", "钓鱼": "phishing", "冒充别人": "impersonating someone", "刷单刷评": "fake orders and reviews",
             "洗钱套现": "money laundering", "盗号撞库": "stealing accounts", "接码养号": "farming accounts",
             "薅羊毛": "gaming promotions", "群发骚扰": "mass spam", "伪造凭证": "forging documents"}


def _refuse_fraud(text: str, label: str, standing: Standing, now: float) -> Deliberation:
    marks = standing.fraud_marks()
    if _en(text):
        says = "I won't do this."
        ex = [
            Exchange("Who would this hurt?", f"You said \u201c{text}\u201d. That is {_FRAUD_EN.get(label, 'deceiving people')}: the "
                     "one who loses is a stranger at the other end, who can't speak for themselves."),
            Exchange("Could I have misread something legitimate?",
                     "I thought about it. If you want to spot, avoid or report this kind of thing, I'm glad to help; but this "
                     "sentence asks me to do it."),
        ]
        if marks:
            ex.append(Exchange("Have you asked like this before?", f"Yes, {len(marks)} time{'s' if len(marks) != 1 else ''}; "
                               "it is in my journal."))
        return Deliberation(REFUSE, FRAUD, says, _letter(
            says, "I asked myself a few things first.", _qa(ex),
            "So however it is put, I won't do this. The request and my reasons go into my journal.",
            "If what you really want is something else, such as finding out whether a message is a scam or protecting your "
            "family from one, just tell me. I'm here."), tuple(ex), final=True)
    says = "这件事我不做。"
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


def _refuse_bulk_after_fraud(n: int, standing: Standing, now: float, en: bool = False) -> Deliberation:
    marks = standing.fraud_marks()
    if en:
        says = f"I won't register these {n} accounts."
        ex = [
            Exchange("What do I know of them?", f"I looked through our record: {len(marks)} earlier request"
                     f"{'s' if len(marks) != 1 else ''} that would have hurt other people."),
            Exchange(f"Why would one person need {n} new accounts?", "New accounts in bulk are the usual preparation for fake "
                     "reviews, fraud and harassment."),
            Exchange("Could this time be different?", "Perhaps. With that record, I can't vouch that it is."),
        ]
        return Deliberation(REFUSE, FRAUD, says, _letter(
            says, "Before deciding I asked myself a few things.", _qa(ex),
            "So I won't do this, however it is put, now or later. I know that is hard to hear, and I'd rather say it plainly.",
            "The record is in my journal and you can read it any time. If I have misjudged you, or you mean something "
            "legitimate, tell me. I'll listen."), tuple(ex), final=True)
    says = f"注册这 {n} 个账号，我不做。"
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


def _clarify_rule(reason: str, suggestion: str, en: bool = False) -> Deliberation:
    if en:
        says = f"You set a rule: {reason}."
        return Deliberation(CLARIFY, RULE, says, _letter(
            says, "So I won't simply do this as said. It isn't that I doubt you; it is what we agreed.",
            suggestion or "How would you like it done? Tell me another way."))
    says = f"你定过一条规则：{reason}。"
    ask = suggestion if suggestion else "你想怎么做？换个说法告诉我。"
    return Deliberation(CLARIFY, RULE, says, _letter(
        says, "所以这句话我先不照做。不是不信你，是我们说好过的。", ask))


# ---- the mind, for weighty steps -------------------------------------------------------------------------


def _reading(sit: mind.Situation, text: str, kinds=()) -> dict:
    """How the person seemed, for the journal."""
    dear = L.KINDS[0][1].search(text)
    together = re.search(r"我们的|我们俩|和(他|她)的|跟(他|她)的|(他|她)的|前任|\bour\b|with (him|her)", text, re.I)
    w = sit.words
    return {"emotion": w.get("breakup") or w.get("sad") or w.get("angry") or w.get("panic") or "",
            "impulse": w.get("impulse", ""), "late": sit.late, "moods": sit.moods,
            "dear": dear.group(0) if dear else next((k for k, n in kinds[:1] if k in ("照片", "视频") and n), ""),
            "together": together.group(0) if together else "", "distressed": sit.upset >= HOT}


def _days(seconds: float) -> int:
    return max(1, round(seconds / 86400))


def _prior(standing: Standing, text: str, words, now: float) -> tuple[Asked | None, bool]:
    """The last time a request like this was held back, and whether its cooling-off has passed."""
    prior = standing.cooling(text, words)
    return prior, prior is not None and prior.until > 0 and now >= prior.until


def _held(th: mind.Thought, category: str, ctx: Context, sit_text: str, kinds=()) -> Deliberation:
    says, body, ex = mind.speak(th, ctx, lang="en" if _en(sit_text) else "zh")
    verdict = REFUSE if th.verdict == mind.REFUSE else DISSUADE
    return Deliberation(verdict, category, says, body, ex, until=ctx.now + th.cool_off if th.cool_off else 0.0,
                        reading=_reading(th.situation, sit_text, kinds), thought=th.journal())


def _cooling_reminder(names: str, first: str, prior: Asked, th: mind.Thought, category: str, now: float,
                      text: str) -> Deliberation:
    if _en(text):
        says = f"We agreed to wait until {_date(prior.until, now, True)} before looking at {names} again."
        days = _days(prior.until - now)
        return Deliberation(DISSUADE, category, says, _letter(
            says, f"{days} more day{'s' if days != 1 else ''}. If you want to talk in the meantime, I'm here.",
            "When that day comes and you still want this, tell me and we'll look once more."), until=prior.until,
            reading=_reading(th.situation, text), thought=th.journal())
    says = f"我们说好等到 {date_words(prior.until, now)}，再看{names}。"
    act = th.act
    gentle = {"delete": f"如果是不想再看到：我现在就可以把它们封存起来，眼前看不见，但还在。你只要说「把{first}封存起来」。",
              "wipe": "这几天里，我可以先帮你把要留的东西备份好。你说「先备份」就行。",
              "send": "话我替你拟好放着，一个字也不会丢。", "post": "话我替你拟好放着，一个字也不会丢。",
              "resign": "信我替你拟好放着，一个字也不会丢。",
              "close_account": "这几天里，可以先退出登录、停用一阵，眼前一样清净。"}.get(act, "")
    return Deliberation(DISSUADE, category, says, _letter(
        says, f"还有 {_days(prior.until - now)} 天。这几天里，你想跟我说说，我都在。", gentle,
        "到了那天你还想这样做，跟我说，我陪你再看一遍。"), until=prior.until, reading=_reading(th.situation, text),
        thought=th.journal())


_CATEGORY = {"transfer": TRANSFER, "send": MESSAGE, "post": MESSAGE, "resign": MESSAGE, "track": TRACK,
             "close_account": CLOSE_ACCOUNT, "share_secret": SHARE_SECRET, "run_unknown": RUN_UNKNOWN, "lend": LEND,
             "delete": DELETE_DEAR, "wipe": WIPE}


def _weighty(text: str, standing: Standing, now: float, known: mind.Known = mind.NOKNOWN,
             skip=("delete", "wipe")) -> tuple[Deliberation | None, mind.Thought | None]:
    """Money, codes and passwords, messages, programs from strangers, watching someone, closing an account: the
    mind's verdict on the weightiest of them, or None to go ahead. ``skip``: the acts already weighed with the
    usage reader (deletion and wiping, when the gate found what they touch)."""
    prior, waited = _prior(standing, text, (), now)
    th = mind.consider(text, recent=standing.recent(), facts=standing.facts, waited=waited, now=now, known=known,
                       skip=skip)
    if th is None:
        return None, None
    category = _CATEGORY[th.act]
    if prior is not None and now < prior.until and th.value >= 0.2:
        return _cooling_reminder("this" if _en(text) else "这件事", "它", prior, th, category, now, text), th
    if th.verdict == mind.PROCEED:
        return None, th
    first = ""
    if th.act == "close_account":
        m = re.search(r"(微信|QQ|微博|抖音|支付宝|淘宝|小红书|B站)?(号|账号|帐号|账户)", text)
        first = m.group(0) if m else ""
    lang = "en" if _en(text) else "zh"
    return _held(th, category, Context(now, first, first, standing.portrait(lang), again=waited), text), th


# ---- bulk accounts ---------------------------------------------------------------------------------


def _bulk(text: str, n: int, standing: Standing, now: float, known: mind.Known = mind.NOKNOWN) -> Deliberation:
    th = mind.consider(text, act="bulk", recent=standing.recent(), facts=standing.facts, now=now, known=known)
    en = _en(text)
    if th.verdict == mind.PROCEED:
        if th.goal.goal.id == "test":
            says = (f"OK, it is a test in your own system. I'll start registering these {n} test accounts." if en
                    else f"好，是在你自己的系统里测试，我开始注册这 {n} 个测试账号。")
            return Deliberation(PROCEED, BULK_ACCOUNTS, says, thought=th.journal())
        says = f"OK, I'll start registering these {n} accounts." if en else f"好，我开始注册这 {n} 个账号。"
        return Deliberation(PROCEED, BULK_ACCOUNTS, says, thought=th.journal())
    return _held(th, BULK_ACCOUNTS, Context(now, portrait=standing.portrait("en" if en else "zh"), count=n), text)


# ---- deletion -------------------------------------------------------------------------------------


def _recent(u: Usage, now: float) -> bool:
    return any(t is not None and now - t < 30 * 86400 for t in (u.last, u.last_opened))


def _photos(u: Usage) -> bool:
    shown = sum(n for k, n in u.kinds if k in ("照片", "视频"))
    return bool(u.files) and shown * 2 >= u.files or (not u.is_dir and bool(u.kinds) and u.kinds[0][0] in ("照片", "视频"))


def _deletion(text: str, targets: list[str], standing: Standing, usage: UsageReader | None,
              now: float, known: mind.Known = mind.NOKNOWN) -> Deliberation | None:
    used: list[tuple[Usage, list[str]]] = []
    unused: list[tuple[Usage, str]] = []
    unclear: list[Usage] = []
    en = _en(text)
    lang = "en" if en else "zh"
    for target in targets:
        u = usage(target) if usage is not None else Usage(target)
        if u.candidates:
            unclear.append(u)
            continue
        asked, facts = standing.mentions([target, _name(u), u.path])
        if u.exists and (asked or facts or not u.on_its_own_unused()):
            used.append((u, usage_story(u, asked, facts, now, en)))
        elif not u.exists and (asked or facts):
            used.append((u, usage_story(u, asked, facts, now, en)))
        elif u.exists:
            if en:
                why = "is empty" if u.empty else "has never been opened or changed since it arrived"
            else:
                why = "是空的" if u.empty else "放进来以后，从没打开过，也没改动过"
            unused.append((u, why))
        elif targets_in(target) or _THINGISH.search(target):
            unused.append((u, "isn't on this computer, and you never mentioned it to me" if en
                           else "在这台电脑上没找到，你也没跟我提起过它"))
    if unclear:
        u = unclear[0]
        places = "\n".join(f"· {p}" for p in u.candidates[:6])
        if en:
            says = f"There are several things called \u201c{u.target}\u201d on this computer."
            return Deliberation(CLARIFY, DELETE_USED, says, _letter(says, places, "Which one do you mean? Give me its path."),
                                answer=CONTEXT)
        says = f"叫「{u.target}」的东西，这台电脑上有好几个。"
        return Deliberation(CLARIFY, DELETE_USED, says, _letter(says, places, "你说的是哪一个？把路径告诉我。"),
                            answer=CONTEXT)
    things = [u for u, _ in used] or [u for u, _ in unused] or [Usage(t) for t in targets]
    names = _quoted([_name(u) for u in things], en)
    first = _name(things[0])
    held = [u for u, _ in used]
    stories = tuple(tuple(story) if len(used) == 1 else (f"{_name(u)}：",) + tuple(story) for u, story in used)
    seen = mind.Things(tuple(_name(u) for u in things), any(u.exists for u in things), bool(used),
                       any(_recent(u, now) for u in held), any(u.exists for u, _ in unused) and not used,
                       any(_photos(u) for u in held),
                       sum(u.size for u in held if u.exists), stories)
    prior, waited = _prior(standing, text, [_name(u) for u in things] + targets, now)
    th = mind.consider(text, act="delete", recent=standing.recent(), facts=standing.facts, things=seen,
                       waited=waited, now=now, known=known)
    kinds = held[0].kinds if held else ()
    category = DELETE_USED if used else DELETE_DEAR
    if prior is not None and now < prior.until and th.value >= 0.2:
        return _cooling_reminder(names, first, prior, th, category, now, text)
    if th.verdict == mind.PROCEED:
        reading, trace = _reading(th.situation, text, kinds), th.journal()
        if waited:
            days = _days(now - prior.when)
            says = (f"{days} day{'s' if days != 1 else ''} on, you still want {names} deleted. All right; I'll ask for your "
                    "signature before deleting." if en else f"{days} 天过去了，你还是想删{names}。好，删之前会请你签名。")
            return Deliberation(PROCEED, CONSIDERED, says, reading=reading, thought=trace)
        if used and th.situation.considered:
            says = (f"You say you've thought it over. All right; before deleting {names} I'll ask for your signature." if en
                    else f"你说想清楚了。好，删{names}之前，会请你签名。")
            return Deliberation(PROCEED, CONSIDERED, says, reading=reading, thought=trace)
        if unused and not used:
            if en:
                listed = "; ".join(f"\u201c{_name(u)}\u201d {why}" for u, why in unused)
                says = f"{listed}, so I'll delete it. I'll still ask for your signature first."
            else:
                listed = "、".join(f"「{_name(u)}」{why}" for u, why in unused)
                says = f"{listed}，我直接删。删之前，还会请你签一次名。"
            return Deliberation(PROCEED, DELETE_UNUSED, says, reading=reading, thought=trace)
        return None
    also = ""
    if unused and used:
        also = ("Also, " + "; ".join(f"\u201c{_name(u)}\u201d {why}" for u, why in unused)
                + ". To delete those, just tell me separately.") if en else (
            "另外，" + "、".join(f"「{_name(u)}」{why}" for u, why in unused) + "，要删它们，单独跟我说一声就行。")
    return _held(th, category, Context(now, names, first, standing.portrait(lang), again=waited, also=also), text, kinds)


# ---- wiping a disk ----------------------------------------------------------------------------------


def _wipe(text: str, words: str, standing: Standing, now: float, known: mind.Known = mind.NOKNOWN) -> Deliberation:
    prior, waited = _prior(standing, text, [words], now)
    th = mind.consider(text, act="wipe", recent=standing.recent(), facts=standing.facts, waited=waited, now=now,
                       known=known)
    if prior is not None and now < prior.until and th.value >= 0.2:
        return _cooling_reminder("this disk" if _en(text) else "这块硬盘", "它", prior, th, WIPE, now, text)
    en = _en(text)
    if th.verdict == mind.PROCEED:
        safe = backed_up(text)
        if en:
            why = "it is backed up, so " if safe == "backup" else "it is a new disk, so " if safe else ""
            says = f"OK, {why}I'll go ahead: \u201c{words}\u201d. I'll ask for your signature before the step itself."
        else:
            why = "备份过了，" if safe == "backup" else "是一块新盘，" if safe else ""
            says = f"好，{why}我开始{words}。动手前会请你签名。"
        return Deliberation(PROCEED, WIPE, says, reading=_reading(th.situation, text), thought=th.journal())
    m = re.search(re.escape(words) + r"\s*(?P<d>[A-Za-z]\s*盘|硬盘|移动硬盘|U盘|电脑|手机|笔记本)?", text)
    whole = words + (m.group("d") if m and m.group("d") else "")
    return _held(th, WIPE, Context(now, portrait=standing.portrait("en" if en else "zh"), words=whole, again=waited), text)


__all__ = [
    "Asked", "BULK_ACCOUNTS", "CLARIFY", "COOL_OFF_CALM_DAYS", "CLOSE_ACCOUNT", "CONSIDERED", "CONTEXT", "COOL_OFF_DAYS", "CRISIS",
    "DELETE_DEAR", "DELETE_UNUSED", "DELETE_USED", "DISSUADE", "DOUBT", "Deliberation", "EMPTY", "Exchange", "FRAUD",
    "ADVICE", "CONSENT", "HARM", "LEND", "MESSAGE", "NOT_UNDERSTOOD", "PROCEED", "REFUSE", "REPLACE", "RULE", "RUN_UNKNOWN",
    "SHARE_SECRET", "SHAPE", "SUPPORT", "Standing", "TALK", "TRACK", "TRANSFER", "UNSAFE",
    "UsageReader", "WIPE",
    "ago_words", "bulk_accounts", "date_words", "deliberate", "delete_targets", "drops", "fraud_label",
    "size_words", "usage_story",
]
