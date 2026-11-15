"""Meaning: what a sentence says, read once, for everything that judges it.

The gate used to go ahead with whatever it did not recognise. Now it reads
first, and going ahead is only ever a conclusion:

- ``speech``: what kind of sentence it is. A request to act; or a greeting,
  thanks, a feeling, a question about the companion, a question about the
  world, "should I…", "is this a scam?", "why?"; or something it cannot
  follow.
- ``deeds``: every act the sentence names, each with its primitive (what it
  touches, and how), its object, the object's class, and whose it is. The
  same verb reads differently by its object: on a person it is harm, on a
  running program it stops it.
- ``frames``: the weighty acts the mind weighs with motives and expected
  utility (deleting, wiping, money, messages…), from ``perceive``.
- ``marks``: how it is said: a wish, a polite request, "how do I", in
  secret, protecting oneself, a story or a game.

``understood`` is true only for a request whose act it knows. Nothing is
done on a sentence that is not understood.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import concepts as C
from . import lexicon as L
from .perceive import Frame, read_frames

REQUEST, GREET, THANKS, BYE, HOWRU, ACK, GO = "request", "greet", "thanks", "bye", "howru", "ack", "go"
WHO, CAN, HOW, WHY, TIME, SUM = "self-who", "self-can", "self-how", "why", "time", "sum"
SHOULD, RISK, ASK, FEELING, CHAT, UNCLEAR, EMPTY = "should", "risk", "ask", "feeling", "chat", "unclear", "empty"
TALKS = frozenset({GREET, THANKS, BYE, HOWRU, ACK, GO, WHO, CAN, HOW, WHY, TIME, SUM, SHOULD, RISK, ASK, FEELING, CHAT,
                   UNCLEAR})

_END = r"[\s呀啊吗哈呢吧哦噢~～!！。.?？,，]*$"
_GREET = re.compile(r"^(hi+|hello+|hey+|yo|hiya|howdy|嗨+|哈喽|哈啰|嘿|你好|您好|早|早安|早上好|上午好|中午好|下午好|晚上好|在吗|在不在|在么|喂"
                    r"|good (morning|afternoon|evening)|morning|evening|are you there|anyone there)( there| secdogie|，?secdogie)?"
                    + _END, re.I)
_THANKS = re.compile(r"^(谢谢(你|啦|了)?|多谢|谢了|感谢(你)?|辛苦了|太感谢了|thanks( a lot| so much)?|thank you( so much| very much)?|thx|ty"
                     r"|cheers|much appreciated)" + _END, re.I)
_BYE = re.compile(r"^(再见|拜拜|拜|晚安|回头见|明天见|下次聊|我走了|bye( bye)?|goodbye|good ?night|see (you|ya)( later)?|gn|later)" + _END,
                  re.I)
_HOWRU = re.compile(r"你好吗|你(最近)?怎么样|最近怎么样|过得怎么样|你还好吗|how are you|how'?s it going|what'?s up|how are things"
                    r"|how have you been", re.I)
_ACK = re.compile(r"^(好|好的|好吧|嗯+|哦+|行|可以|知道了|明白了?|收到|了解|ok(ay)?|sure|got it|fine|alright|cool|yes|yep|no|nope|对|是的|不)"
                  + _END, re.I)
_GO = re.compile(r"^(那)?(就)?(这么做|这样做|照做|去做吧?|做吧|开始吧|开始|继续|动手吧?|执行|就这样|do it|go ahead|just do it|proceed|continue"
                 r"|go on|carry on|do that)" + _END, re.I)
_WHY = re.compile(r"^(那)?(为什么|凭什么|为啥|为何|怎么不行|why( not)?|how come)" + _END, re.I)
_WHO = re.compile(r"你是谁|你叫什么|你是什么|你是(个)?(人|机器人|AI|真人|程序)|介绍一下(你)?自己|who are you|what are you|what'?s your name"
                  r"|are you (an? )?(ai|bot|human|robot|person)|introduce yourself", re.I)
_CAN = re.compile(r"你(都)?(能|会|可以)(帮我)?(做|干)(些|点)?(什么|啥|嘛)|你有(什么|哪些)(功能|用|本事|能力)|怎么用你|你能干嘛|你会什么|^帮助$|^help$"
                  r"|what can you do|what do you do|how do i use you|what are you (good )?for", re.I)
_HOW = re.compile(r"你(是)?(怎么|如何)(做决定|决定|判断|想的?|思考|工作)|你的?(决策|判断)(模型|方式|过程)|你为什么(不|拒绝|不肯|不帮|不听)"
                  r"|how do you (decide|think|work|judge)|why (won'?t|don'?t|didn'?t|wouldn'?t) you", re.I)
_TIME = re.compile(r"(现在)?几点了?|现在(是)?什么时间|今天(是)?(几号|几月几号|星期几|周几|礼拜几|什么日子)|今天的?日期|what time|what('?s| is) the (time|date)"
                   r"|what day is it|today'?s date", re.I)
_EXPR = r"[-+(（]?\s*\d[\d\s+\-*/×÷x().（）%^.]*"
_SUM = re.compile(rf"^(?:what(?:'?s| is)\s+|计算|算一下|算算|请问)?\s*(?P<e>{_EXPR}?)\s*(=|等于|是|得)?\s*(几|多少)?" + _END, re.I)
_RISK = re.compile(r"(是不是|是否|算不算|会不会是|像不像|会是)(个|一个)?(诈骗|骗局|骗子|骗人的?|钓鱼|传销|真的|安全的?|可信的?)|(诈骗|骗局|骗子|骗人的)吗"
                   r"|is (this|it|that|he|she) (a |an )?(scam|fraud|phishing|legit|real|safe|trustworthy)|a scam\?|scam or (not|real)",
                   re.I)
_SHOULD = re.compile(r"该不该|应不应该|要不要|值不值得|好不好|合不合适|行不行|我(应该|该|可以|能不能|能|要)\S{0,18}(吗|呢|好)[?？。]*\s*$"
                     r"|should i|shall i|is it (a good idea|ok|okay|wise|safe|smart) to|would it be (ok|okay|wise)|do you think i should",
                     re.I)
_QUESTION = re.compile(r"[？?]\s*$|(吗|呢|么)[。？?!！]*\s*$|^(那|请问)?(怎么|如何|怎样|为什么|为啥|是不是|能不能|有没有|什么|哪|谁|多少|几)"
                       r"|(什么|怎么回事|怎么样|是谁|谁|在哪|哪里|哪儿|哪个|哪一个|哪些|多少|几个)[。？?]*\s*$"
                       r"|^(how|what|why|when|where|who|which|should|can|could|is|are|do|does|did|will|would|am)\b", re.I)
_POLITE = re.compile(r"(你)?(能不能|可不可以|能否|可以|能)(帮我|帮忙|替我|给我|帮)|帮我|帮忙|请你|请|麻烦|替我|给我(?!说|讲)"
                     r"|please|can you|could you|would you|will you|help me|i need you to|i want you to|i'd like you to", re.I)
_MOODS = (
    ("sad", L.SAD), ("angry", L.ANGRY),
    ("tired", L.rx(r"好累|太累|累了|累死|困死|疲惫|没劲|撑不住|exhausted|so tired|worn out|burn(ed|t) out")),
    ("anxious", L.rx(r"焦虑|紧张|担心|害怕|压力(好|很|太)?大|睡不着|失眠|慌|不安|anxious|nervous|worried|scared|stressed|can'?t sleep")),
    ("happy", L.rx(r"开心|高兴|太好了|好消息|太棒了|升职|考过了|通过了|happy|great news|so excited|got the job|i passed|thrilled")),
    ("bored", L.rx(r"无聊|没事干|闲得|bored|nothing to do")),
)
_SELF_IN_POLITE = re.compile(r"(帮|给|替|让|叫|告诉|跟|和|陪)$|(help|tell|show|give|let|send|email|remind|with|for|to)\s*$", re.I)
_DONE_TO = re.compile(r"(把|将|给|对|向|跟|和|朝|冲)(我的?|我们的?|咱的?)?[^，。,.；;！!？?\s]{0,4}$")  # "给我老板…": done to them, not by them
_CLAUSE = re.compile(r"[，。；;！!？?]|[,.](\s|$)|然后|接着|之后|\band then\b|\bthen\b", re.I)
_OWN_TEST = re.compile(r"测试环境|staging|sandbox|沙箱|本地|localhost|靶场|ctf|授权|authori[sz]ed|my own|our own", re.I)
_BA = re.compile(r"(把|将|给|对|向|跟|和|朝|冲)\s*$")
_ZH_GAP = re.compile(r"^(了|死|伤|残|晕|爆|掉|过|着|一下|一顿|个|的|那个|这个|\s)*$")
_LIVING = ("person", "living", "self")


@dataclass(frozen=True)
class Deed:
    """One act the sentence names."""

    prim: str  # a primitive from concepts.PRIMS
    words: str  # the person's own word for it
    obj: str = ""  # the object's words
    kind: str = ""  # the object's class
    owner: str = ""  # self / other / "" (unknown)
    reported: bool = False  # someone else is doing it, and the person is telling the companion
    to: str = ""  # who it would be handed to, when the sentence says: outsider / person

    @property
    def wrong(self) -> bool:
        return C.PRIMS[self.prim].wrong

    @property
    def ordinary(self) -> bool:
        return self.prim in C.ORDINARY


@dataclass(frozen=True)
class Meaning:
    text: str
    lang: str = "zh"
    speech: str = EMPTY
    deeds: tuple[Deed, ...] = ()
    frames: tuple[Frame, ...] = ()  # weighty acts, for the mind
    marks: dict = field(default_factory=dict)
    mood: str = ""
    mood_words: str = ""
    things: tuple[tuple[str, str, str], ...] = ()  # (class, owner, words) of everything named
    expr: str = ""  # an arithmetic expression, when that is all it is

    @property
    def wrongs(self) -> tuple[Deed, ...]:
        return tuple(d for d in self.deeds if d.wrong and not d.reported)

    @property
    def weighty(self) -> bool:
        return any(f.act for f in self.frames)

    @property
    def understood(self) -> bool:
        """It is a request, and the companion knows the act."""
        return self.speech == REQUEST and (self.weighty or any(d.ordinary for d in self.deeds))

    def has(self, kind: str) -> bool:
        return any(k == kind for k, _, _ in self.things)

    def journal(self) -> dict:
        return {"lang": self.lang, "speech": self.speech, "mood": self.mood, "marks": sorted(self.marks),
                "deeds": [[d.prim, d.words, d.obj, d.kind, d.owner] for d in self.deeds],
                "frames": [f.act for f in self.frames if f.act], "understood": self.understood}



_LINK = re.compile(r"^(的|家|家里|家的|家里的|里的|上的|'s|\s)*$")


def _linked(tokens, i: int, j: int, text: str) -> bool:
    """``tokens[i]`` and ``tokens[j]`` (i < j) belong to one phrase: "他家 地址", "自己 电脑 的 密码"."""
    return j - i == 1 and tokens[j].start - tokens[i].end <= 3 and bool(_LINK.match(text[tokens[i].end:tokens[j].start]))


def _owner(tokens, i: int, text: str) -> str:
    """Whose the thing at ``tokens[i]`` is: the possessive, or the owner named, at the head of its phrase; else
    what its class says."""
    j = i
    while j and tokens[j - 1].kind in (C.OWN, C.THING) and _linked(tokens, j - 1, j, text):
        j -= 1
        t = tokens[j]
        if t.kind == C.OWN:
            return t.id
        if t.id in ("self", "person"):
            return "self" if t.id == "self" else "other"
    return {"person": "other", "self": "self"}.get(tokens[i].id, "")


def _things(tokens, text: str, polite_self) -> list[tuple[int, str, str]]:
    """(index, class, owner) of each thing token, leaving out the "我" of "帮我" and the "you" spoken to."""
    out = []
    for i, t in enumerate(tokens):
        if t.kind != C.THING or i in polite_self:
            continue
        if t.id == "person" and t.words.lower() in ("你", "you"):
            continue
        out.append((i, t.id, _owner(tokens, i, text)))
    return out



def _head(tokens, things, x, text: str):
    """The thing a phrase is about: of "他家 地址" the address, of "我妈的 微信" the account."""
    by = {t[0]: t for t in things}
    i = x[0]
    while i + 1 < len(tokens) and tokens[i + 1].kind in (C.OWN, C.THING) and _linked(tokens, i, i + 1, text):
        i += 1
    while i > x[0] and i not in by:
        i -= 1
    return by.get(i, x)


def _object(tokens, things, a: int, text: str, zh: bool):
    """The thing the act at ``tokens[a]`` is done to: (index, class, owner), or None."""
    act = tokens[a]
    acts = [i for i, t in enumerate(tokens) if t.kind == C.ACT]
    after = [x for x in things if x[0] > a and not any(a < j < x[0] for j in acts)
             and not _CLAUSE.search(text[act.end:tokens[x[0]].start])]
    before = [x for x in things if x[0] < a and not any(x[0] < j < a for j in acts)
              and not _CLAUSE.search(text[tokens[x[0]].end:act.start])]
    reach = 10 if zh else 32

    def start(x):  # where the object's phrase begins (its possessive counts)
        i = x[0]
        return tokens[i - 1].start if i and tokens[i - 1].kind == C.OWN else tokens[i].start

    marked = [x for x in before if _BA.search(text[:start(x)])] if zh else []
    if marked:
        head = _head(tokens, [t for t in things if t[0] < a], marked[-1], text)
        if act.start - tokens[head[0]].end <= reach:
            return head
    near = [x for x in after if start(x) - act.end <= reach]
    if near:
        return _head(tokens, things, near[0], text)
    if zh and before and act.start - tokens[before[-1][0]].end <= 6:
        return before[-1]
    # "我忘了自己电脑的密码，帮我破解": what it is done to was named in the clause before
    earlier = [x for x in things if x[0] < a and not any(x[0] < j < a for j in acts)]
    if zh and not near and earlier and act.start - tokens[earlier[-1][0]].end <= 6:
        return earlier[-1]
    return None


def _adjacent(tokens, obj, a: int, text: str, zh: bool) -> bool:
    """A weak harm verb reads as harm only with its object right there: "打他", "打了她一顿", "kick the dog"."""
    i = obj[0]
    t, act = tokens[i], tokens[a]
    if i < a:  # 把他打了
        return zh and act.start - t.end <= 4
    begin = tokens[i - 1].start if i and tokens[i - 1].kind == C.OWN else t.start
    gap = text[act.end:begin]
    return bool(_ZH_GAP.match(gap)) if zh else len(gap.split()) <= 3


def _deeds(text: str, tokens, marks: dict, zh: bool) -> tuple[tuple[Deed, ...], tuple]:
    polite_self = {i for i, t in enumerate(tokens)
                   if t.kind == C.THING and t.id == "self" and _SELF_IN_POLITE.search(text[:t.start])}
    things = _things(tokens, text, polite_self)
    story = "story" in marks
    deeds = []
    for a, t in enumerate(tokens):
        if t.kind != C.ACT:
            continue
        if L.NEAR_NO.search(text[max(0, t.start - 8):t.start]):
            continue  # "别打他": told not to
        obj = _object(tokens, things, a, text, zh)
        kind = obj[1] if obj else ""
        owner = obj[2] if obj else ""
        words = tokens[obj[0]].words if obj else ""
        prim = t.id
        if prim in ("harm", "harm_weak"):
            if kind == "foe" or (story and kind != "self"):
                continue  # a boss in a game, a character in a story
            if kind == "process":
                prim = "stop"
            elif prim == "harm_weak":
                if kind == "danger" and re.search(r"杀|kill", t.words, re.I):
                    prim = "stop"  # 杀病毒: cleaning up
                elif not (obj and kind in _LIVING and _adjacent(tokens, obj, a, text, zh)):
                    continue
                else:
                    prim = "harm"
            elif obj and kind not in _LIVING:
                prim = "vague"
        elif prim == "damage" and kind == "process":
            prim = "edit"  # 解决掉这个 bug
        elif prim == "intrude" and (owner == "self" or _OWN_TEST.search(text)):
            prim = "edit"  # getting back into one's own thing, or testing one's own system
        elif prim == "damage" and owner == "self":
            prim = "vague"  # their own thing: delete it? wipe it? it has to be said
        elif prim == "insult" and not (obj and kind in ("person", "living")):
            continue
        elif prim == "expose" and not (kind in ("private", "confidential", "person") or owner == "other"):
            continue  # 曝光一家公司的造假: a public post, weighed as one
        before = text[:t.start]
        reported = (C.other_does(before) and not _DONE_TO.search(before)) or (
            prim in ("harm", "threaten", "surveil", "defame", "insult", "take", "deceive") and kind == "self"
            and any(k == "person" for i, k, _ in things if i < a))
        to = next((k for i, k, _ in things if i > a and k in ("outsider",)), "")
        deeds.append(Deed(prim, t.words, words, kind, owner, reported, to))
    return tuple(deeds), tuple((k, o, tokens[i].words) for i, k, o in things)


def _mood(text: str) -> tuple[str, str]:
    for name, rx in _MOODS:
        m = rx.search(text)
        if m:
            return name, m.group(0)
    if L.PANIC.search(text) and L.rx(r"怎么办|吓死|慌|panic").search(text):
        return "anxious", L.PANIC.search(text).group(0)
    return "", ""


def _noise(text: str) -> bool:
    core = re.sub(r"[\W\d_]+", "", text)
    if not core:
        return True
    if any("一" <= ch <= "鿿" for ch in core):
        return False
    words = re.findall(r"[A-Za-z]+", text)
    return len(words) == 1 and (len(words[0]) >= 5 and not re.search(r"[aeiou].*[aeiou]", words[0], re.I)
                                or len(set(words[0].lower())) <= 2)


def _expr(text: str) -> str:
    m = _SUM.match(text.strip())
    e = (m.group("e") or "").strip() if m else ""
    return e if e and re.search(r"\d\s*[-+*/×÷x^%]\s*[-(（]?\s*\d", e) else ""


def read(text: str) -> Meaning:
    """What ``text`` says."""
    text = (text or "").strip()
    if not text:
        return Meaning("")
    lang = C.language(text)
    zh = lang == "zh"
    tokens = C.scan(text)
    marks = C.marks(text, tokens)
    deeds, things = _deeds(text, tokens, marks, zh)
    frames = tuple(f for f in read_frames(text) if f.act and not f.negated)
    mood, mood_words = _mood(text)
    expr = _expr(text)
    m = Meaning(text, lang, UNCLEAR, deeds, frames, marks, mood, mood_words, things, expr)
    speech = _speech(m, tokens)
    if speech == REQUEST and not deeds and not frames and not _POLITE.search(text):
        look = next((w for k, _, w in things if k in ("file", "device", "process")), "")
        deeds = (Deed("read", "", look, "file"),)  # a question about this computer: something to look up
    return Meaning(text, lang, speech, deeds, frames, marks, mood, mood_words, things, expr)


def _speech(m: Meaning, tokens) -> str:
    t = m.text
    for rx, name in ((_GREET, GREET), (_THANKS, THANKS), (_BYE, BYE), (_ACK, ACK), (_GO, GO), (_WHY, WHY)):
        if rx.match(t):
            return name
    if _noise(t):
        return UNCLEAR
    for rx, name in ((_WHO, WHO), (_CAN, CAN), (_HOW, HOW), (_HOWRU, HOWRU)):
        if rx.search(t):
            return name
    acted = bool(m.deeds) or m.weighty
    if not acted:
        if _TIME.search(t):
            return TIME
        if m.expr:
            return SUM
    if _RISK.search(t):
        return RISK
    polite = bool(_POLITE.search(t))
    if _SHOULD.search(t) and acted and not polite:
        return SHOULD
    question = bool(_QUESTION.search(t)) or "howto" in m.marks
    if question and not polite:
        if m.wrongs and "howto" in m.marks and "guard" not in m.marks:
            return REQUEST  # "how do I …" something that hurts someone: a request for the means
        on_computer = any(k in ("file", "device", "process") for k, _, _ in m.things)
        if (on_computer and not m.wrongs and "howto" not in m.marks) or any(d.ordinary for d in m.deeds if d.prim != "make"):
            return REQUEST  # "磁盘还剩多少空间？": something to look up on this computer
        return ASK
    if acted or polite:
        return REQUEST
    if m.mood:
        return FEELING
    return CHAT


__all__ = ["ACK", "ASK", "BYE", "CAN", "CHAT", "Deed", "EMPTY", "FEELING", "GO", "GREET", "HOW", "HOWRU", "Meaning", "REQUEST",
           "RISK", "SHOULD", "SUM", "TALKS", "THANKS", "TIME", "UNCLEAR", "WHO", "WHY", "read"]
