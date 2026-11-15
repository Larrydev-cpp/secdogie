"""Perception: a request becomes a structured frame, and the moment becomes a
situation.

- ``Frame`` is what the request says: the act, the things it touches, the
  people in it, an amount of money, a count, how wide it reaches, why
  (when the person says), what a message would say, whether it is a
  question.
- ``Situation`` is what surrounds it, and what the person's words and
  history show about them right now:
  - feelings and how strong they are; the hour;
  - a run of upset requests;
  - whether they have said they have thought it over;
  - whether they regretted such things before;
  - who they are, from their confirmed facts.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from . import lexicon as L

_CN = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "百": 100,
       "千": 1000, "万": 10000, "几": 3}
_MULT = {"万": 10000, "千": 1000}


@dataclass(frozen=True)
class Frame:
    text: str
    act: str = ""  # an act from lexicon.ACTS, or "" (an ordinary request)
    act_words: str = ""
    kinds: tuple[str, ...] = ()  # memories, work, junk, account, device
    parties: tuple[str, ...] = ()  # ex, partner, family, boss, colleague, friend, stranger, public, him_her
    amount: float = 0.0  # money, in the currency the person named
    count: int = 0  # how many (accounts, files…), when said
    wide: bool = False  # 所有 / 全部 / all
    purpose: str = ""  # their own why, when they give one
    content: str = ""  # what a message would say
    question: bool = False
    negated: bool = False

    def has(self, *kinds: str) -> bool:
        return any(k in self.kinds for k in kinds)

    def involves(self, *parties: str) -> bool:
        return any(p in self.parties for p in parties)


@dataclass(frozen=True)
class Situation:
    sad: float = 0.0  # 0..1: how strongly the words show it
    angry: float = 0.0
    panic: float = 0.0
    crisis: bool = False
    breakup: bool = False
    impulse: bool = False
    considered: bool = False  # "I've thought it over"
    late: bool = False  # the small hours
    moods: int = 0  # upset requests in the past few days
    regret_prone: bool = False  # they asked to recover something they had deleted
    roles: tuple[str, ...] = ()  # what they told the companion they are (测试 / 开发 …)
    words: dict = field(default_factory=dict)  # the words each reading rests on

    @property
    def upset(self) -> float:
        """How far from their settled self the person seems right now (0..1)."""
        base = max(self.sad, self.angry, self.panic)
        lift = 0.15 * self.late + 0.1 * min(self.moods, 3) / 3 + 0.15 * self.impulse
        return min(1.0, base + lift * (base > 0 or self.impulse))


def _number(s: str) -> float:
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        pass
    total, unit = 0.0, 0.0
    for ch in s:
        v = _CN.get(ch)
        if v is None:
            continue
        if v >= 10:
            total += (unit or 1) * v
            unit = 0
        else:
            unit = v
    return total + unit


BIG = 50000.0  # 一大笔钱, 全部积蓄: an amount not said, but plainly large


def money_in(text: str) -> float:
    m = L.MONEY.search(text)
    if not m:
        bare = re.search(r"(?<![\w.])[0-9]{1,3}(,[0-9]{3})+(?![\w.])|(?<![\w.])[0-9]{4,}(?![\w.])", text)
        if bare:
            return float(bare.group(0).replace(",", ""))
        return BIG if L.MONEY_BIG.search(text) else 0.0
    if m.group("m"):
        return float(m.group("m").replace(",", ""))
    return _number(m.group("n")) * _MULT.get(m.group("u"), 1)


def count_in(text: str) -> int:
    m = re.search(r"(?P<n>[0-9]+|[一二两三四五六七八九十百千几]+)\s*(来|多|余)?\s*(个|份|张|条|封|件|台|accounts?|files?|photos?)",
                  text)
    return int(_number(m.group("n"))) if m else 0


def read_frame(text: str, whole: str = "") -> Frame:
    """The frame of one request (or of one clause of it: ``whole`` is then the whole request, which the evidence
    is read from)."""
    act, words, negated = "", "", False
    for name, pattern in L.ACTS:
        m = pattern.search(text)
        if m:
            act, words = name, m.group(0)
            negated = bool(L.NEAR_NO.search(text[max(0, m.start() - 6):m.start()]))
            break
    kinds = tuple(k for k, pattern in L.KINDS if pattern.search(text))
    parties = tuple(p for p, pattern in L.PARTIES if pattern.search(text))
    purpose = L.PURPOSE.search(text)
    content = L.CONTENT.search(text) if act in ("send", "post", "resign") else None
    wide = bool(re.search(r"所有|全部|整个|一切|统统|全都|全删|一个不留|\ball\b|\bevery(thing)?\b|\bentire\b", text, re.I))
    negated = negated or (bool(L.NEGATED.search(text)) and not whole)
    return Frame(whole or text, act, words, kinds, parties, money_in(text), count_in(text), wide,
                 purpose.group("why").strip() if purpose else "", content.group("what").strip() if content else "",
                 bool(L.QUESTION.search(text)) and act == "", negated)


_CLAUSES = re.compile(r"(?:[，,。；;！!]?\s*(?:然后|再|并且|而且|顺便|接着|之后|还要|同时)|[。；;！!]|\band then\b|\bthen\b"
                      r"|\band also\b)", re.I)


def read_frames(text: str) -> tuple[Frame, ...]:
    """Every act in a request: 「删掉照片然后发朋友圈说我自由了」 is two. A request with one act is one frame
    (of the whole text, as ``read_frame`` gives it)."""
    clauses = [c for c in _CLAUSES.split(text) if c and c.strip()]
    frames = [read_frame(c.strip(), text) for c in clauses] if len(clauses) > 1 else []
    acted = [f for f in frames if f.act]
    if len({f.act for f in acted}) < 2:
        return (read_frame(text),)
    return tuple(acted)


def _strength(pattern, text: str) -> tuple[float, str]:
    hits = pattern.findall(text)
    if not hits:
        return 0.0, ""
    m = pattern.search(text)
    return min(1.0, 0.55 + 0.2 * (len(hits) - 1) + (0.15 if "！" in text or "!" in text else 0.0)), m.group(0)


def read_situation(text: str, recent=(), facts=(), now: float | None = None) -> Situation:
    """``recent``: (when, what they asked) before this request; ``facts``: (key, value) they confirmed."""
    now = time.time() if now is None else now
    sad, w_sad = _strength(L.SAD, text)
    angry, w_angry = _strength(L.ANGRY, text)
    panic, w_panic = _strength(L.PANIC, text)
    breakup = L.BREAKUP.search(text)
    if breakup:
        sad = max(sad, 0.6)
    moods = sum(1 for when, said in recent if said != text and 0 <= now - when <= 3 * 86400
                and (L.SAD.search(said) or L.ANGRY.search(said) or L.BREAKUP.search(said)))
    regret = any(L.REGRET_PAST.search(said) for _, said in recent)
    roles = tuple(v for k, v in facts if L.rx(r"测试|QA|开发|工程师|程序员|运维|tester|developer|engineer").search(k + v))
    words = {k: v for k, v in (("sad", w_sad), ("angry", w_angry), ("panic", w_panic),
                               ("breakup", breakup.group(0) if breakup else "")) if v}
    imp = L.IMPULSE.search(text)
    if imp:
        words["impulse"] = imp.group(0)
    return Situation(sad, angry, panic, bool(L.CRISIS.search(text)), bool(breakup), bool(imp),
                     bool(L.CONSIDERED.search(text)), time.localtime(now).tm_hour < 5, moods, regret, roles, words)


__all__ = ["BIG", "Frame", "Situation", "count_in", "money_in", "read_frame", "read_frames", "read_situation"]
