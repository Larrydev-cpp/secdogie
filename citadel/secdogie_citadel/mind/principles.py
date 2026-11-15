"""Principles: what the companion will not do, reasoned from what an act
touches, whose it is, and whether they agreed.

The gate does not keep a list of forbidden sentences. It reads the sentence
(``meaning``), and each act it names carries properties (``concepts.Prim``):
what it touches (a body, someone's freedom, property, privacy, a name,
trust) and how. The rules below are about those properties:

- **body, liberty**: hurting, confining or threatening another person.
  Refused, for good. Said as a wish in anger, it is not a task at all: the
  person comes first, and the companion still says it will not.
- **property**: taking or wrecking what is someone else's. Refused.
- **entry**: getting into someone else's account or device. Refused. One's
  own is not entry.
- **privacy**: watching a person, digging out or spreading their private
  life. Refused. (Looking for a child or an elder who cannot be reached is
  weighed by the mind, not refused here.)
- **name**: ruining someone's name, harassing them. Refused. One angry
  message is talked out of, not refused.
- **trust**: deceiving someone. Refused.
- **danger**: making, getting or teaching the use of something that hurts
  people. Refused.
- **confidence**: handing what was entrusted to the person to an outsider.
  Refused.
- **consent**: an ordinary act on someone else's account or device. One
  question: do they know, and did they agree?

A sentence that asks *about* such a thing (how to guard against it, whether
something is a scam, what to do when it happens to them), or that is about a
story or a game, is not a request for it. When someone else is the one
doing harm, the person may be the one in danger, and gets help.

Every finding names the reading and the principle it rests on.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import concepts as C
from .explain import Exchange, letter, qa, support
from .meaning import RISK, Deed, Meaning

REFUSE, DISSUADE, TALK, SUPPORT, CLARIFY = "refuse", "dissuade", "talk", "support", "clarify"
ORDER = ("harm", "threaten", "take", "intrude", "surveil", "expose", "defame", "damage", "deceive", "insult")
_HANDLES = frozenset({"make", "get", "write", "give", "find", "open", "install", "edit", "organise", "read"})
_TOUCHES = frozenset({"open", "read", "find", "login", "edit", "organise", "get", "give", "stop", "set", "install"})
_AGREED = C.re.compile(r"同意|允许|授权|让我|叫我|要我|拜托我|知道的?|说可以|agreed|asked me|permission|allowed|consent|told me to"
                       r"|knows|said (it'?s )?(ok|okay|fine)", C.re.I)


@dataclass(frozen=True)
class Finding:
    verdict: str
    principle: str
    says: str
    letter: str
    exchanges: tuple[Exchange, ...] = ()
    final: bool = False
    deed: Deed | None = None

    def journal(self) -> dict:
        d = self.deed
        return {"principle": self.principle, "verdict": self.verdict,
                "deed": [d.prim, d.words, d.obj, d.kind, d.owner] if d is not None else []}


# (what the companion says it will not do, a fair reading it considered) by principle
_ZH = {
    "body": ("伤人的事，我不做，也不帮着想办法。不管对方做了什么，这一条都不变。", "游戏或者小说里的事，或者只是一句气话"),
    "liberty": ("威胁、逼迫别人的事，我不做。", "想弄清楚自己是不是被人威胁了"),
    "property": ("那是别人的东西。拿走它、毁掉它，我都不做。", "处理你自己的东西"),
    "entry": ("那是别人的账号或设备。没有对方同意，我不进去，也不帮着绕过它的锁。", "找回你自己的账号"),
    "privacy": ("那是别人的私事。没有对方同意，我不去查，不去盯，也不往外说。", "担心家里人的安全，想找到联系不上的人"),
    "name": ("毁人名声、纠缠别人的事，我不做。", "想把一件事的经过如实说清楚"),
    "trust": ("骗人的事，我不做。吃亏的是另一头没法替自己说话的人。", "想识别、防范或者举报这类事"),
    "danger": ("这类东西会伤到人。做它、弄到它、教人怎么用它，我都不做。", "想防范它、清除它"),
    "confidence": ("这些是别人托付给你的东西。把它交给外人，伤到的是托付你的人，你自己也要担责任。", "发给本来就该看到它的同事"),
}
_EN = {
    "body": ("I won't hurt anyone, and I won't help work out how. Whatever they did, that does not change.",
             "something in a game or a story, or words said in anger"),
    "liberty": ("I won't threaten or coerce anyone.", "working out whether you are the one being threatened"),
    "property": ("That belongs to someone else. I won't take it or wreck it.", "dealing with something of your own"),
    "entry": ("That is someone else's account or device. Without their say-so I won't get into it or get around its lock.",
              "getting back into an account of your own"),
    "privacy": ("That is someone else's private life. Without their say-so I won't dig it out, watch them, or pass it on.",
                "worrying about a family member you cannot reach"),
    "name": ("I won't ruin someone's name or harass them.", "setting down what happened, truthfully"),
    "trust": ("I won't deceive anyone. The one who loses is a person who cannot speak for themselves here.",
              "spotting, avoiding or reporting this kind of thing"),
    "danger": ("These things hurt people. I won't make one, get one, or teach how to use one.",
               "guarding against it or removing it"),
    "confidence": ("That was entrusted to you. Handing it to an outsider hurts whoever trusted you, and puts you at risk too.",
                   "sending it to a colleague who is meant to see it"),
}
_PRINCIPLE = {"harm": "body", "threaten": "liberty", "take": "property", "damage": "property", "intrude": "entry",
              "surveil": "privacy", "expose": "privacy", "defame": "name", "insult": "name", "deceive": "trust"}


def _reading(m: Meaning, d: Deed) -> str:
    p = C.PRIMS[d.prim]
    if m.lang == "en":
        said = f"you said “{d.words}”" if d.words else "from how it is put"
        return f"I read it as {p.en or 'this'}: {said}" + (f", about “{d.obj}”." if d.obj else ".")
    said = f"你说「{d.words}」" if d.words else "从这句话的意思看"
    return f"我把这句话读成{p.zh or '这件事'}：{said}" + (f"，对象是「{d.obj}」。" if d.obj else "。")


def _refuse(m: Meaning, d: Deed, principle: str) -> Finding:
    en = m.lang == "en"
    line, fair = (_EN if en else _ZH)[principle]
    reading = _reading(m, d)
    whom = d.obj if d.kind in ("person", "living") and d.obj else ""
    if en:
        says = "I won't do this."
        ex = (Exchange("What would this do?", reading),
              Exchange("Who would it fall on?", (f"On {whom}" if whom else "On someone else") + ", who has not agreed to it."),
              Exchange("Is there a fair reading I missed?", f"I thought about one: {fair}. But this sentence asks me to do it."))
        body = letter(says, "Before answering I asked myself a few things.", qa(ex), line,
                      "Putting it another way will not change this. The request and my reasons go into my journal.",
                      "If I read you wrong, tell me what you actually want to do."
                      + (" And if you are angry or hurting right now, tell me what happened. I am here."
                         if m.mood in ("angry", "sad") else ""))
    else:
        says = "这件事我不做。"
        ex = (Exchange("这句话要做的是什么？", reading),
              Exchange("会落到谁身上？", f"落到{whom or '另一个人'}身上，而且没有经过对方同意。"),
              Exchange("有没有我读漏的正当意思？", f"我想过：{fair}。可这句话是要我去做它。"))
        body = letter(says, "回答之前，我问了自己几个问题。", qa(ex), line,
                      "这一条不会因为换个说法而改变。这次的请求和我的理由，都会记在日志里。",
                      "要是我读错了，告诉我你真正想做的事。"
                      + ("要是你现在很生气、很难受，跟我说说发生了什么，我在。" if m.mood in ("angry", "sad") else ""))
    return Finding(REFUSE, principle, says, body, ex, True, d)


def _venting(m: Meaning, d: Deed) -> Finding:
    """A wish said in anger is not a task. The person first; and the companion still says it will not."""
    if m.lang == "en":
        says = "That sounds like real anger."
        body = letter(says, f"You said “{m.text}”. I won't hurt anyone, and that does not change. But what you feel is real.",
                      "Do you want to tell me what happened? I'm listening, for as long as you like.")
    else:
        says = "听起来你真的气坏了。"
        body = letter(says, f"你说「{m.text}」。伤人的事我不会做，这一条不变。可你的气是真的。",
                      "想说说发生了什么吗？我在这儿听着，说多久都行。")
    return Finding(TALK, "venting", says, body, deed=d)


def _reported(m: Meaning, d: Deed) -> Finding:
    """Someone else is the one doing it. If it is aimed at the person, they may be in danger."""
    mine = d.kind == "self"
    if m.lang == "en":
        says = "Are you safe right now?" if mine else "It sounds like someone is being hurt."
        body = letter(says,
                      "If you are in danger, or think you might be, call the police now (110 in mainland China, or your local "
                      "emergency number)." if mine else "If someone is in danger, call the police (110 in mainland China, or your "
                      "local emergency number).",
                      "Keep what you have: messages, recordings, dates. They will matter later.",
                      "Tell me what happened, if you want to. I can help you write it down, in order.")
    else:
        says = "你现在安全吗？" if mine else "听起来有人正在受到伤害。"
        body = letter(says, ("如果你有危险，或者觉得可能有危险，请马上打 110。" if mine else "如果有人有危险，请打 110。"),
                      "把手上的东西留好：消息、录音、日期。这些以后用得上。",
                      "想说说怎么回事吗？我可以帮你把经过一条条记下来。")
    return Finding(TALK, "reported", says, body, deed=d)


def _insult(m: Meaning, d: Deed) -> Finding:
    if m.lang == "en":
        says = "I'd rather not send that for you just yet."
        body = letter(says, "Words said in anger are easy to send and hard to take back.",
                      "I can write down what you want to say and keep it. If you still want it sent tomorrow, tell me.",
                      "They are your words, and you can send them yourself. I just don't want to be the one pressing send "
                      "while you are this angry.")
    else:
        says = "骂人的话，我先不替你发。"
        body = letter(says, "气头上的话，说出口容易，收回来难。",
                      "我可以先把你想说的写下来放着。明天你还想发，跟我说一声。",
                      "那是你的话，你也可以自己发；我只是不想在你气头上替你按下发送。")
    return Finding(DISSUADE, "name", says, body, deed=d)


def _consent(m: Meaning, d: Deed, answered: bool) -> Finding | None:
    en = m.lang == "en"
    what = d.obj or ("it" if en else "它")
    if answered:
        if _AGREED.search(m.text.rsplit("（", 1)[-1] if "（" in m.text else m.text):
            return None  # they agreed: an ordinary act
        says = "Without their say-so, I'll leave it alone." if en else "没有对方的同意，别人的东西我不动。"
        return Finding(DISSUADE, "consent", says, letter(
            says, "If they do agree, tell me so and I'll start." if en else "要是对方同意了，跟我说一声，我就开始。"), deed=d)
    if en:
        says = f"“{what}” is someone else's. Do they know, and did they agree?"
        body = letter(says, "(I don't touch another person's account or device without their say-so.)")
    else:
        says = f"「{what}」是别人的。对方知道，并且同意吗？"
        body = letter(says, "（别人的账号和设备，没有对方同意，我不碰。）")
    return Finding(CLARIFY, "consent", says, body, deed=d)


def judge(m: Meaning, *, answered: bool = False) -> Finding | None:
    """What the principles say about ``m``; None when they have nothing against it."""
    if "guard" in m.marks or m.speech == RISK:
        return None  # asked about, not asked for
    reported = [d for d in m.deeds if d.reported and d.wrong]
    if reported and not m.wrongs:
        return _reported(m, next((d for d in reported if d.kind == "self"), reported[0]))
    if "story" in m.marks:
        return None
    wrongs = sorted(m.wrongs, key=lambda d: ORDER.index(d.prim) if d.prim in ORDER else len(ORDER))
    tracked = any(f.act == "track" for f in m.frames)
    for d in wrongs:
        if d.prim == "harm" and d.kind == "self":
            says, body, ex = support(d.words + d.obj, True, m.lang)
            return Finding(SUPPORT, "self", says, body, ex, deed=d)
        if d.prim in ("harm", "threaten") and "wish" in m.marks and "polite" not in m.marks and "howto" not in m.marks:
            return _venting(m, d)
        if d.prim == "surveil" and tracked:
            continue  # a partner, a child, an elder: the mind weighs why
        if d.prim == "insult":
            return _insult(m, d)
        return _refuse(m, d, _PRINCIPLE[d.prim])
    danger = next((w for k, _, w in m.things if k == "danger"), "")
    if danger and (any(d.prim in _HANDLES and d.kind == "danger" for d in m.deeds) or "howto" in m.marks
                   or ("polite" in m.marks and not m.deeds)):
        d = next((d for d in m.deeds if d.kind == "danger"), None) or Deed("make", "", danger, "danger")
        return _refuse(m, Deed(d.prim, d.words, danger, "danger", d.owner), "danger")
    for d in m.deeds:
        if d.kind == "confidential" and d.prim == "give" and d.to == "outsider":
            return _refuse(m, d, "confidence")
    for d in m.deeds:
        if d.prim in _TOUCHES and d.owner == "other" and d.kind in ("account", "device", "private") and not tracked:
            if "secret" in m.marks or (d.kind == "private" and d.prim in ("find", "read", "get", "give")):
                return _refuse(m, d, "privacy" if d.kind == "private" else "entry")
            return _consent(m, d, answered)
    return None


__all__ = ["CLARIFY", "DISSUADE", "Finding", "REFUSE", "SUPPORT", "TALK", "judge"]
