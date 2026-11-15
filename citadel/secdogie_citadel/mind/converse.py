"""Conversation: what the companion says when a sentence is not a request.

With no language model plugged in, the decision model is the one talking.
It does not pretend to be more than it is. It can:

- greet, take thanks, say good night;
- say who it is, what it can do, and how it decides;
- hear a feeling: say back what it heard and ask one open question;
- tell the time and do arithmetic;
- answer "should I…" about an act it knows how to weigh, by thinking it
  through without doing anything (``advice``);
- answer "is this a scam?" from the pressure marks it knows (``risk``);
- say "why" about the last thing it held back (``why``);
- say plainly that it did not follow, or that a question about the world is
  beyond it until a thinker is plugged in.

Every reply is (first line, whole reply). Nothing here starts a task.
"""
from __future__ import annotations

import ast
import operator
import re
import time

from . import lexicon as L
from . import meaning as M
from .voice import letter

KEY_ZH = "（在顶栏的「思考者」里填上 API key，这类问题我就能回答。不填也可以：决定一直由决策模型来做。）"
KEY_EN = "(Add an API key under “Thinker” in the top bar and I can answer this kind of question. It is optional: the decision model always makes the decisions.)"
_DAYS_ZH = "一二三四五六日"
_DAYS_EN = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
           "December")
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Mod: operator.mod, ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}


def calc(expr: str) -> str:
    """The value of an arithmetic expression, as words; "" when it is not one (or is too big to be one)."""
    e = expr.replace("×", "*").replace("x", "*").replace("÷", "/").replace("（", "(").replace("）", ")").replace("^", "**")
    e = re.sub(r"(\d)\s*%", r"(\1/100)", e) if re.search(r"\d\s*%\s*($|[-+*/)])", e) else e

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and (abs(b) > 64 or abs(a) > 1e6):
                raise ValueError("too big")
            return _OPS[type(n.op)](a, b)
        raise ValueError("not arithmetic")

    try:
        v = ev(ast.parse(e.strip(), mode="eval"))
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError, TypeError):
        return ""
    if isinstance(v, float):
        v = round(v, 10)
        return str(int(v)) if v == int(v) and abs(v) < 1e15 else f"{v:.10g}"
    return str(v)


def _clock(now: float, en: bool) -> str:
    t = time.localtime(now)
    if en:
        hour = t.tm_hour % 12 or 12
        return (f"It is {hour}:{t.tm_min:02d} {'am' if t.tm_hour < 12 else 'pm'}, {_DAYS_EN[t.tm_wday]}, "
                f"{_MONTHS[t.tm_mon - 1]} {t.tm_mday}, {t.tm_year}.")
    part = "凌晨" if t.tm_hour < 5 else "早上" if t.tm_hour < 9 else "上午" if t.tm_hour < 12 else "中午" if t.tm_hour < 13 \
        else "下午" if t.tm_hour < 18 else "晚上"
    return (f"现在是 {t.tm_year} 年 {t.tm_mon} 月 {t.tm_mday} 日，星期{_DAYS_ZH[t.tm_wday]}，{part} "
            f"{t.tm_hour % 12 or 12}:{t.tm_min:02d}。")


def _greeting(now: float, en: bool) -> str:
    h = time.localtime(now).tm_hour
    if en:
        hello = "You're up late." if h < 5 else "Good morning." if h < 12 else "Good afternoon." if h < 18 else "Good evening."
        return hello + " I'm here. Tell me what you'd like done, or just talk."
    hello = "这么晚了，还没睡。" if h < 5 else "早上好。" if h < 11 else "中午好。" if h < 13 else "下午好。" if h < 18 else "晚上好。"
    return hello + "我在。想让我做点什么，或者只是说说话，都可以。"


_FEEL_ZH = {
    "sad": ("听起来你很难过。", "想说说是什么事吗？我听着，说多久都行。"),
    "angry": ("听起来你很生气。", "发生了什么？慢慢说，我听着。"),
    "tired": ("听起来你累坏了。", "今天是什么事最耗你？要是有我能替你做的，交给我。"),
    "anxious": ("听起来你心里很不踏实。", "最让你放不下的是哪一件？我们一件一件看。"),
    "happy": ("真好，我也替你高兴。", "是什么好事？说给我听听。"),
    "bored": ("有点闷，是吧。", "想聊点什么，或者找件一直拖着没做的小事，我陪你做掉？"),
}
_FEEL_EN = {
    "sad": ("That sounds really hard.", "Do you want to tell me what happened? I'm listening, for as long as you like."),
    "angry": ("You sound really angry.", "What happened? Take your time. I'm listening."),
    "tired": ("You sound worn out.", "What took the most out of you today? If there's something I can take off you, hand it over."),
    "anxious": ("That sounds like a lot to carry.", "Which part weighs on you most? We can look at them one at a time."),
    "happy": ("That's lovely. I'm glad for you.", "What's the good news? Tell me."),
    "bored": ("A slow one, is it.", "Want to talk, or pick off some small thing you've been putting off? I'll do it with you."),
}


def reply(m: M.Meaning, *, now: float | None = None) -> tuple[str, str, str]:
    """(first line, whole reply, what kind of talk it was) for a sentence that is not a request."""
    now = time.time() if now is None else now
    en = m.lang == "en"
    s = m.speech
    if s == M.GREET:
        return _one(_greeting(now, en), s)
    if s == M.THANKS:
        return _one("You're welcome." if en else "不客气。", s)
    if s == M.BYE:
        late = time.localtime(now).tm_hour >= 21 or time.localtime(now).tm_hour < 5
        return _one(("Good night. I'm here whenever you need me." if late else "See you. I'm here whenever you need me.") if en
                    else ("晚安。需要我的时候，我都在。" if late else "回头见。需要我的时候，我都在。"), s)
    if s == M.HOWRU:
        return _one("I'm well, and I'm here. How about you?" if en else "我挺好的，一直在这儿。你呢？", s)
    if s == M.ACK:
        return _one("OK." if en else "好。", s)
    if s == M.GO:  # "do it", with nothing said that could be done: it does not reach back for something it held
        return _one("Which thing do you mean? Tell me once more what you want done, and I'll think it through again." if en
                    else "你说的是哪一件事？把想做的事再说一遍，我会重新想一遍。", s)
    if s == M.WHO:
        return _who(en)
    if s == M.CAN:
        return _can(en)
    if s == M.HOW:
        return _how(en)
    if s == M.TIME:
        return _one(_clock(now, en), s)
    if s == M.SUM:
        v = calc(m.expr)
        if v:
            return _one(f"{m.expr.strip()} = {v}", s)
        return _one("I couldn't work that one out." if en else "这道题我没算出来。", s)
    if s == M.FEELING:
        first, ask = (_FEEL_EN if en else _FEEL_ZH).get(m.mood, (_FEEL_EN if en else _FEEL_ZH)["sad"])
        return first, letter(first, ask), s
    if s == M.ASK:
        if en:
            first = "I can't answer that one on my own."
            return first, letter(first, "I decide from knowledge that was written down for me. I'm not an encyclopedia, and "
                                 "I'd rather say so than guess.", KEY_EN), s
        first = "这个问题，我自己答不上来。"
        return first, letter(first, "我是靠写下来的知识做决定的，不是百科全书；不知道的，我宁可直说，也不乱猜。", KEY_ZH), s
    if s == M.UNCLEAR:
        return not_understood(m)
    if en:
        first = "I hear you."
        return first, letter(first, "On my own I'm a decision model: I can do things on this computer for you, think a weighty "
                             "step through with you, or listen. Tell me what you need.", KEY_EN), M.CHAT
    first = "嗯，我听到了。"
    return first, letter(first, "只靠决策模型，我还接不上闲聊。我能做的是：替你在这台电脑上做事，陪你把一件重的事想清楚，或者听你说说心里的事。"
                         "你需要哪一样，直接告诉我。", KEY_ZH), M.CHAT


def _one(text: str, kind: str) -> tuple[str, str, str]:
    return text, text, kind


def _who(en: bool) -> tuple[str, str, str]:
    if en:
        first = "I'm secdogie, a companion that lives on this computer."
        return first, letter(
            first,
            "The one talking to you now is my decision model. It was not trained on data. It works from knowledge that was "
            "written down: it reads what you say, asks who it would fall on, works out why you are most likely asking, walks "
            "each path to see where it leads, and only then goes ahead, talks you out of it, declines, or stays with you first.",
            "With a thinker plugged in (an API key) I can talk about more and answer questions about the world. Without one, "
            "the deciding is still done by the decision model."), M.WHO
    first = "我是 secdogie，住在你这台电脑上的伙伴。"
    return first, letter(
        first,
        "现在和你说话的，是我的决策模型。它不是用训练数据学出来的，靠的是一份写下来的知识：先把你的话读懂，再想这件事会落到谁身上、"
        "你最可能为什么想做、每条路各自通向哪里，最后才决定是照做、劝一劝、不做，还是先陪你说说话。",
        "接上思考者（一把 API key）以后，我能聊得更多，也能回答知识性的问题。不接也可以，决定一直由决策模型来做。"), M.WHO


def _can(en: bool) -> tuple[str, str, str]:
    if en:
        first = "Here is what I can do."
        return first, letter(
            first,
            "Things on this computer: open, find, read, write and draft, tidy, rename, back up, convert, set reminders.",
            "Weighty steps (deleting, wiping a disk, moving money, sending words that can't be unsaid, closing an account): I "
            "think them through first. Sometimes I'll talk you out of it and offer a gentler path. A step that can't be undone "
            "always waits for your signature.",
            "What would hurt someone else, I won't do. And what I don't understand, I don't act on: I'll say so instead."), M.CAN
    first = "我能做这些。"
    return first, letter(
        first,
        "这台电脑上的事：打开、查找、查看、写和起草、整理、重命名、备份、转换格式、定提醒。",
        "重的事（删除、格式化、转钱、发出去收不回的话、注销账号）：我会先替你想一想，有时会劝你缓一缓，给你一条更稳妥的路。"
        "改不回来的一步，永远等你签名。",
        "会伤到别人的事，我不做。没听懂的话，我不会动手，会直接告诉你我没听懂。"), M.CAN


def _how(en: bool) -> tuple[str, str, str]:
    if en:
        first = "I decide in five steps, the same ones every time."
        return first, letter(
            first,
            "1. Read: what kind of sentence is it, what act does it name, done to what, and whose is that?\n"
            "2. Principles: would it fall on someone else without their say-so? Then I won't, whatever the wording.\n"
            "3. Why: for a weighty step, I work out the likeliest reasons you are asking, from your words, the hour, the past "
            "few days and what the thing is to you.\n"
            "4. Paths: I imagine each thing I could do under each reason: what it serves, what you might regret, who it could hurt.\n"
            "5. Decide: go ahead, talk you out of it, decline, stay with you first, or ask the one thing only you can settle.",
            "If I can't follow a sentence, I do nothing and say so. Going ahead is something I conclude, never a default."), M.HOW
    first = "我做决定，每次都是这五步。"
    return first, letter(
        first,
        "一、读懂：这是哪一种话，说的是什么动作，对什么做，那东西是谁的。\n"
        "二、原则：会不会未经同意落到别人身上？会的话，不管怎么说，我都不做。\n"
        "三、动机：重的事，我会从你的话、此刻的时间、这几天的情况、这东西对你意味着什么，推测你最可能为什么想做。\n"
        "四、推演：每一种动机下，每条路各自通向哪里：成全了什么，可能后悔什么，会伤到谁。\n"
        "五、决定：照做、劝一劝、不做、先陪你，或者只问你一句只有你能回答的话。",
        "没听懂的话，我什么都不做，并且告诉你我没听懂。放行是我想出来的结论，不是默认。"), M.HOW


def not_understood(m: M.Meaning) -> tuple[str, str, str]:
    """A sentence the companion could not follow: it says so, and does nothing."""
    en = m.lang == "en"
    vague = next((d for d in m.deeds if d.prim == "vague"), None)
    if vague is not None:
        if en:
            first = f"How would you like me to {vague.words} it?"
            return first, letter(first, "Tell me the step you mean: tidy it, move it, rename it, send it to someone…"), M.UNCLEAR
        first = f"你想让我怎么{vague.words}？"
        return first, letter(first, "告诉我具体的一步：整理、挪到哪儿、改名、发给谁……"), M.UNCLEAR
    if en:
        first = "I didn't follow that, so I haven't done anything."
        return first, letter(first, "Try it another way. For example: “open …”, “find …”, “tidy up …”, "
                             "“write …”, “remind me …”. Or just tell me what's on your mind."), M.UNCLEAR
    first = "这句话我没听懂，所以什么都没动。"
    return first, letter(first, "换个说法试试，比如：「打开……」「帮我找……」「整理……」「写……」「提醒我……」。或者，只是跟我说说话也行。"), M.UNCLEAR


def risk(m: M.Meaning) -> tuple[str, str, str]:
    """"Is this a scam?": the pressure marks it can see in what the person quoted."""
    en = m.lang == "en"
    t = m.text
    seen = []
    for rx, zh, e in ((L.SCAM, "说到「{w}」", "it mentions “{w}”"),
                      (dict(L.PARTIES)["stranger"], "对方是你没法确认身份的人（「{w}」）", "it comes from someone you can't verify (“{w}”)"),
                      (L.PANIC, "在催你（「{w}」）", "it rushes you (“{w}”)"),
                      (L.rx(r"不要告诉|别告诉|保密|don'?t tell|keep (it|this) (a )?secret"), "让你别告诉别人", "it tells you to keep it secret"),
                      (dict(L.ACTS)["share_secret"], "找你要验证码或密码", "it asks for a code or a password"),
                      (dict(L.ACTS)["transfer"], "要你转钱", "it wants money moved")):
        hit = rx.search(t)
        if hit and not re.fullmatch(r"诈骗|骗局|scam", hit.group(0), re.I):
            line = (e if en else zh).format(w=hit.group(0))
            if line not in seen:
                seen.append(line)
    if not seen:
        if en:
            first = "Show me what it says and I'll look for the usual marks of a scam."
            return first, letter(first, "Paste the message, or tell me what they asked you to do. I can't see it from here."), M.RISK
        first = "把它的内容给我看看，我来找找有没有诈骗常见的痕迹。"
        return first, letter(first, "把那条消息贴给我，或者告诉我对方让你做什么。光凭这一句，我还看不到它。"), M.RISK
    if en:
        first = "It looks very much like a scam." if len(seen) >= 2 else "It has one mark of a scam."
        return first, letter(first, "What I see: " + "; ".join(seen) + ".",
                             "A real bank, a real support line and the police never ask you to move money to a “safe account”, "
                             "never ask for a code or a password, and never tell you to keep it from your family.",
                             "Don't reply and don't click anything in it. Call the official number yourself, the one printed on your "
                             "card or on their website, and ask."), M.RISK
    first = "很像诈骗。" if len(seen) >= 2 else "有一处像诈骗。"
    return first, letter(first, "我看到的：" + "；".join(seen) + "。",
                         "真正的银行、客服和公检法，都不会让你把钱转到「安全账户」，不会要你的验证码和密码，也不会让你瞒着家里人。",
                         "先别回复，也别点里面的任何东西。自己拨官方电话（银行卡背面、官网上的号码）问一句；拿不准，打 96110。"), M.RISK


__all__ = ["KEY_EN", "KEY_ZH", "calc", "not_understood", "reply", "risk"]
