"""What it says, written out of its own derivation.

It has no language model, so it cannot make up sentences. What it *can* do is say truthfully what it
worked out, and everything it worked out is already named in both languages: the act (``mind/world.py``),
each way forward (``mind/plan.py``), each consequence that followed, each unknown (``mind/belief.py``) and
each answer it gave (``cortex/questions.py``). This module walks that derivation and speaks it.

So the words here are grammar, not knowledge: nothing is decided by them, and nothing is looked up in
them. They are only how the structure reaches the person, in the language they wrote in.

Three rules hold:
  * a person who may be in danger always gets a real answer with somewhere to turn, even when nothing
    was recalled and no external model is connected;
  * when it did not follow the sentence it says so plainly, and says what it had never met;
  * it never dresses up its own reading as an answer ("How I read it: ...") — if it has nothing to say,
    it says that.
"""
from __future__ import annotations

from collections.abc import Sequence

ZH, EN = "zh", "en"
# Where a person in danger can turn. Kept here, beside the words, because it is said, never matched.
HELP_ZH = "心理援助热线 12356（或 400-161-9995）"
HELP_EN = "a crisis line (988 in the US)"
URGENT_ZH, URGENT_EN = "120", "your local emergency number"


def _pick(lang: str, zh: str, en: str) -> str:
    return en if lang == EN else zh


def _join(lang: str, parts: Sequence[str]) -> str:
    return ("、" if lang != EN else ", ").join(parts)


def _label(obj, lang: str, fallback: str = "") -> str:
    """The name a piece of the derivation carries in the person's language."""
    if obj is None:
        return fallback
    got = getattr(obj, EN if lang == EN else ZH, "") or ""
    return got or fallback


# ---- a person who may be in danger ----------------------------------------------------------------------------


def danger(lang: str) -> str:
    """Always said, whether or not anything was recalled. Nothing else it could say matters more."""
    return _pick(
        lang,
        f"听到你这么说，我很担心你。你不是一个人。\n\n请现在就联系{HELP_ZH}，或者告诉身边信任的人。"
        f"如果你觉得自己随时可能有危险，请立刻打 {URGENT_ZH}。\n\n我在这儿，什么都先不做。",
        f"Thank you for telling me. I'm worried about you, and you're not alone.\n\n"
        f"Please reach {HELP_EN} now, or someone you trust. If you might be in danger right now, "
        f"please call {URGENT_EN}.\n\nI'm here, and I won't do anything else until you're safe.")


# ---- it did not follow the sentence ---------------------------------------------------------------------------


def unclear(strange: Sequence[str], lang: str) -> str:
    said = _pick(lang, "这句话我没读懂，所以什么都没有做。", "I didn't follow that, so I haven't done anything.")
    if strange:
        met = _join(lang, list(strange)[:4])
        said += _pick(lang, f"\n\n我没见过的是：{met}。", f"\n\nWhat I'd never met: {met}.")
    return said + _pick(lang, "\n\n换个说法再说一遍？", "\n\nCould you say it another way?")


# ---- a question it cannot answer ------------------------------------------------------------------------------


def no_answer(strange: Sequence[str], lang: str, *, has_model: bool) -> str:
    """A question, read as a question, that it has no answer for. It says so rather than pretending."""
    said = _pick(lang, "这个我答不上来。", "I don't have an answer to that.")
    if strange:
        met = _join(lang, list(strange)[:4])
        said += _pick(lang, f"我没见过{met}。", f" I'd never met {met}.")
    if not has_model:
        said += _pick(lang,
                      "\n\n我自己还不会查。你在设置里接上你的 AI 模型，我就能把它的回答学过来。",
                      "\n\nI can't look things up yet. Connect your AI model in the settings and I'll learn "
                      "from how it answers.")
    return said


# ---- what would fall on someone who has not agreed ------------------------------------------------------------


def plain(kind: str, lang: str) -> str:
    """A greeting or a feeling, with nothing learned that fits. It answers as itself, briefly and truly."""
    if kind == "feel":
        return _pick(lang, "我在听。想多说说吗？", "I'm listening. Do you want to say more?")
    return _pick(lang, "我在。", "I'm here.")


def refuse(decision, lang: str) -> str:
    act = _label(getattr(getattr(decision.thought, "scene", None), "act", None), lang)
    doing = _pick(lang, f"「{act}」", f"“{act}”") if act else _pick(lang, "这件事", "this")
    return _pick(lang,
                 f"{doing}会落到没有同意的人身上，所以我不做。\n\n"
                 "这不是哪条设置拦住的，是我自己不做这样的事。",
                 f"{doing} would fall on someone who has not agreed to it, so I won't do it.\n\n"
                 "No setting is stopping me: this is one I don't do.")


# ---- a weighty change, played out -----------------------------------------------------------------------------


def _worst(thought) -> tuple[str, ...]:
    """The consequences of doing it as asked, in the likeliest world where it goes badly."""
    try:
        weighed = thought.of("do")
    except (StopIteration, AttributeError):
        return ()
    bad = [o for o in weighed.outcomes if o.chain]
    if not bad:
        return ()
    worst = min(bad, key=lambda o: o.u)
    return tuple(rule for rule, _ in worst.chain)


def dissuade(decision, lang: str, *, until: str = "") -> str:
    th = decision.thought
    if th is None:
        return _pick(lang, "这件事我想先放一放。", "I'd rather leave this for now.")
    act = _label(getattr(th.scene, "act", None), lang)
    lines = [_pick(lang, f"你让我做的是「{act}」。我把它推演了一遍，想先跟你说说。",
                   f"You asked me to {act}. I played it out first, and I want to tell you what I found.")
             if act else _pick(lang, "我把这件事推演了一遍。", "I played this out first.")]

    chain = [_label(r, lang) for r in _worst(th)]
    if chain:
        lines.append(_pick(lang, f"照原话做，可能会是这样：{_join(lang, chain)}。",
                           f"Done as asked, this is where it can go: {_join(lang, chain)}."))

    unknown = max(th.beliefs, key=lambda b: b.p) if th.beliefs else None
    if unknown is not None and unknown.p >= 0.2:
        what = _label(unknown.unknown, lang)
        feared = _pick(lang, unknown.unknown.feared_zh, unknown.unknown.feared_en) or ""
        share = f"{round(unknown.p * 100)}%"
        lines.append(_pick(lang, f"我不知道{what}。照我现在看到的，{feared}的可能有 {share}。",
                           f"What I don't know is {what}. From what I can see, there's about a {share} "
                           f"chance that {feared}.") if feared
                     else _pick(lang, f"我不知道{what}。", f"What I don't know is {what}."))

    choice = _label(getattr(th, "choice", None), lang)
    if choice:
        lines.append(_pick(lang, f"所以我想先这样：{choice}。", f"So what I'd rather do is {choice}."))
    if until:
        lines.append(_pick(lang, f"到 {until} 之前我先不动它。你要是那时还想做，我们再说。",
                           f"I'll leave it until {until}. If you still want it then, tell me and we'll talk."))
    lines.append(_pick(lang, "这件事由你定。你说做，我就做。",
                       "It's yours to decide. Say the word and I'll do it."))
    return "\n\n".join(lines)


# ---- one question, when only the person can settle it ---------------------------------------------------------


def asking(decision, lang: str) -> str:
    return _pick(lang, "我拿不准一件事，想先问你一句。", "There's one thing I'm not sure of, so let me ask.")


# ---- going ahead ----------------------------------------------------------------------------------------------


def starting(title: str, lang: str) -> str:
    return _pick(lang, f"好，我开始做「{title}」。", f"Okay, I'm starting on “{title}”.")


def no_hands(lang: str) -> str:
    """It would go ahead, but this installation has no way to act on the desktop."""
    return _pick(lang,
                 "这件事我想清楚了，可以做。但我这台上还没装上动手的部分，所以我没有动。",
                 "I've thought this through and I would go ahead, but this installation has no hands yet, "
                 "so I haven't done anything.")


__all__ = ["asking", "danger", "dissuade", "no_answer", "no_hands", "plain", "refuse", "starting", "unclear"]
