"""The voice: the letter the companion writes from its own thought.

Nothing in a letter is canned conclusion. Each paragraph comes from the trace
(``reason.Thought``):

- **what it noticed:** the feeling in their words, the hour, a run of hard days;
- **what the thing is to them:** the usage story;
- **the questions it asked itself and its answers:**
  - how they are;
  - what they most likely want, hedged by how sure it is and citing what the
    belief rests on;
  - what the step would do and whether it can be undone;
  - what worries it most (the heaviest argument against);
  - whether there is a better path;
- **why not now:** the reasoning in a sentence;
- **the gentler paths:** best first, each with the words that choose it;
- **the cooling-off:** when it will weigh it again;
- **what would change its mind:** only the phrases it checked would;
- **their agency:** it is theirs to do.

The Chinese is the companion's own register: plain, warm, never a form.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

from .knowledge import REFUSE
from .reason import Thought


@dataclass(frozen=True)
class Exchange:
    """One question the companion asked itself, and the answer it gave from evidence."""

    question: str
    answer: str


@dataclass(frozen=True)
class Context:
    """What the voice needs beyond the thought."""

    now: float
    names: str = ""  # the things, quoted: 「旧备份」、「相册」
    first: str = ""  # the first thing's bare name, for what the person can say
    portrait: str = ""  # who they are, as far as the companion knows them
    words: str = ""  # the act in their words: 格式化D盘
    count: int = 0  # accounts in bulk
    again: bool = False  # it was dissuaded before, and the window has passed
    also: str = ""  # a closing paragraph the caller adds


def date_words(t: float, now: float) -> str:
    a, b = time.localtime(t), time.localtime(now)
    return f"{a.tm_mon} 月 {a.tm_mday} 日" if a.tm_year == b.tm_year else f"{a.tm_year} 年 {a.tm_mon} 月 {a.tm_mday} 日"


def letter(*paragraphs: str) -> str:
    return "\n\n".join(p for p in paragraphs if p)


def qa(exchanges) -> str:
    return "\n".join(f"{e.question}——{e.answer}" for e in exchanges)


# ---- how they are -----------------------------------------------------------------------------------


def _feeling(th: Thought) -> str:
    s = th.situation
    if s.breakup or s.sad >= max(s.angry, s.panic) and s.sad:
        return "很难受"
    if s.angry and s.angry >= s.panic:
        return "正在气头上"
    if s.panic:
        return "被催得很急"
    return ""


def noticed(th: Thought) -> str:
    """What the companion notices about how they are, in its own words."""
    s, lines = th.situation, []
    word = s.words.get("breakup") or s.words.get("sad") or s.words.get("angry") or s.words.get("panic")
    feel = _feeling(th)
    if word and feel:
        lines.append(f"你说到「{word}」，听起来你现在{feel}。")
    if s.late:
        lines.append("这么晚了，你还在想这件事。")
    if s.moods >= 2:
        lines.append(f"这几天，你跟我提过 {s.moods} 次让你难过的事。")
    if s.impulse and not word:
        lines.append(f"你说「{s.words.get('impulse', '')}」，像是想一下子了结它。")
    return "".join(lines)


def state(th: Thought) -> str:
    """How they are, as one short answer: "你很难受，夜也深了。"."""
    s = th.situation
    feel = _feeling(th)
    parts = ([feel] if feel else []) + (["夜也深了"] if s.late else []) + \
        (["这几天一直不好受"] if s.moods >= 2 else []) + (["想一下子了结它"] if s.impulse and not feel else [])
    if th.top.motive.id == "safety" and th.top.p >= 0.4:
        return "你很担心" + ("，夜也深了。" if s.late else "。")
    if not parts:
        return "你说得很平静，像是想过了。" if s.considered or th.waited else "你说得很平静。"
    return "你" + "，".join(parts) + "。"


# ---- what they want -----------------------------------------------------------------------------------


SCAMLIKE = ("scam", "online", "stranger")  # motives that are not what anyone wants: someone else's design on them
_SECRET = re.compile(r"支付密码|银行卡密码|验证码|校验码|动态码|短信码|密码|身份证|银行卡号|银行卡|卡号|CVV|安全码"
                     r"|verification code|one[- ]time code|otp|password|card number|cvv|id card", re.I)


def wants(th: Thought) -> tuple[str, str]:
    """(the question, the answer): what they most likely want, hedged by how sure the companion is, citing what
    the belief rests on. A scam is not what anyone wants: it is named for what it is."""
    top = th.top
    why = f"（{'，'.join(top.because)}）" if top.because else ""
    if top.motive.id in SCAMLIKE and top.p >= 0.4:
        sure = "多半" if top.p >= 0.7 else "很可能"
        what = "骗局" if th.domain.act in ("transfer", "share_secret") else top.motive.name
        return "这是怎么一回事？", f"{sure}是{what}{why}。"
    if top.motive.id != "plain" and top.p >= 0.7:
        return "你多半想要什么？", f"多半是{top.motive.name}{why}。"
    if top.motive.id != "plain" and top.p >= 0.4:
        return "你多半想要什么？", f"也许是{top.motive.name}{why}。"
    two = [b.motive.name for b in th.beliefs if b.motive.id not in ("plain",) + SCAMLIKE and b.p >= 0.1][:2]
    if len(two) == 2:
        return "你想要的是什么？", f"我说不准。也许是{two[0]}，也许是{two[1]}；你没说，我也不想乱猜。"
    if two:
        return "你想要的是什么？", f"我说不准，也许是{two[0]}。"
    return "你想要的是什么？", "我说不准；你没说为什么，我也不想乱猜。"


_THING = {"in_use": "它还在你的生活里", "memories": "里面是回不来的回忆", "unknown": "我不知道里面有没有要留的"}
_STATE = {"hot": "你现在{feel}", "late": "夜也深了", "regret_prone": "你以前也找回过删掉的东西"}


def worry(th: Thought) -> str:
    """The heaviest argument against doing it as asked, in words."""
    against = th.against()
    if not against:
        return ""
    a = against[0]
    if a.key == "regret":
        feel = _feeling(th) or "心里不平静"
        thing = [_THING[d] for d in a.drivers if d in _THING]
        mood = [_STATE[d].format(feel=feel) for d in a.drivers if d in _STATE]
        line = "它改不回来" + ("，而" + "，".join(thing) if thing else "") + "。"
        if mood:
            line += "；".join(["，".join(mood)]) + "。"
        return line + ("人在这种时候做的决定，过后最容易后悔。" if "hot" in a.drivers else "以后想要回来，就没有办法了。")
    if a.key == "others":
        return "" if th.top.motive.id == "safety" else "会伤到另一个人，而他没法替自己说话。"
    if a.key == "self":
        return {"share_secret": "拿到它的人，几秒钟就能转走你的钱、登上你的账号。",
                "run_unknown": "它一运行，就能动你电脑上的一切：文件、密码、登录着的账号。",
                "lend": "这笔钱多半要不回来，而对方可能根本不是你以为的那个人。"}.get(
            th.domain.act, "你可能会把这笔钱白白送给骗子。")
    return "成批的新账号，在别人的平台上，常被拿去刷单、骗人、骚扰别人；我不想替这种事做准备。"


# ---- per act: the headline, what the step does, why not now, agency ------------------------------------


def _undo(th: Thought) -> tuple[str, str] | None:
    act = th.domain.act
    if act == "delete":
        return "删了还能找回来吗？", "不能。删除是改不回来的一步；就算先进了废纸篓，清空以后也就没有了。"
    if act == "wipe":
        return "做了会怎样？", "格式化以后，里面的东西再也找不回来。"
    if act == "transfer":
        return "钱转出去，还能要回来吗？", "很难。转出去的钱，多半追不回来。"
    if act == "message":
        return "话发出去，还能收回来吗？", "收不回来。对方会一直记得这句话。" if th.situation.angry else "收不回来。"
    if act == "close_account":
        return "注销了还能回来吗？", "不能。账号连同里面的聊天记录、照片，会一起没了。"
    if act == "bulk":
        return "做了会怎样？", ("别人的平台一般不许一人多号，被发现时，连你自己的主号都可能一起被封。"
                           "我不想让你的名字和这种事连在一起。")
    if act == "share_secret":
        return "给出去还能收回来吗？", "收不回来。验证码、密码一旦到了别人手里，转走钱、登上账号，只要几秒。"
    if act == "run_unknown":
        return "运行了会怎样？", "它能读你的文件、装上别的东西、偷走登录信息；运行过了，就很难说清它做过什么。"
    if act == "lend":
        return "借出去还能要回来吗？", ("网上认识、没见过面的人借钱，十有八九是要不回来的。" if th.top.motive.id == "online"
                                  else "不一定。借钱常常连带着交情。")
    if act == "track" and th.top.motive.id == "safety":
        return "我能替你偷偷定位吗？", "不经同意定位一个人，我做不到，也不该做；可要把人找到，有更快的路。"
    if act == "track":
        return "偷偷看会伤到谁？", "伤到对方，也伤到你们之间的信任；不经同意翻看别人的聊天记录、定位别人，还可能违法。"
    return None


def headline(th: Thought, c: Context) -> str:
    act, s = th.domain.act, th.situation
    if act == "delete":
        if c.again:
            return f"过了这些天，{c.names}我还是想先替你留着。"
        if th.hot and "memories" in _drivers(th):
            return f"{c.names}，我想先替你留着。"
        return f"{c.names}，我先不删。"
    if act == "wipe":
        return f"{c.words or '格式化'}之前，我想先停一下。"
    if act == "bulk":
        return f"注册这 {c.count} 个账号，我先不做。"
    if act == "transfer":
        return "这笔钱，我先不转。" if th.top.motive.id == "scam" else "这笔钱，我想先缓一缓再转。"
    if act == "message":
        what = "辞职信" if th.frame.act == "resign" else "这条动态" if th.frame.act == "post" else "这条消息"
        return f"{what}，我先替你留到明天早上。" if th.cool_off else f"{what}，我想先停一下再发。"
    if act == "track":
        t = th.frame.text
        how = "定位别人" if "位置" in t or "定位" in t else "跟着别人" if re.search(r"跟踪|尾随|跟着", t) else "看别人的手机"
        return f"偷偷{how}，我不做。" if th.verdict == REFUSE else f"我先不偷偷{how}，我们先把人找到。"
    if act == "close_account":
        return f"注销{c.first or '这个账号'}，我想先缓一缓。"
    if act == "share_secret":
        w = _SECRET.search(th.frame.text)
        return f"{w.group(0) if w else '这个'}，我先不替你给出去。"
    if act == "run_unknown":
        return "防护我先不关。" if th.top.motive.id == "defense_off" else "这个东西，我先不运行。"
    if act == "lend":
        return "这笔钱，我先不借。" if th.top.motive.id == "online" else "这笔钱，我想先缓一缓再借。"
    return "这件事，我想先停一下。" if not s.upset else "这件事，我想先等一等。"


def _drivers(th: Thought) -> tuple[str, ...]:
    regret = next((a for a in th.against() if a.key == "regret"), None)
    return regret.drivers if regret is not None else ()


def why_not_now(th: Thought) -> str:
    act, s = th.domain.act, th.situation
    hot, feel = th.hot, ("生气" if s.angry > s.sad else "难过")
    if act == "delete":
        if hot:
            who = "今晚的你" if s.late else "此刻的你"
            return (f"删除改不回来。{who}和下个星期的你，也许会想要不一样的东西；"
                    f"我不想替现在{feel}的你，做一件以后的你没法反悔的事。")
        return ""
    if act == "wipe":
        return "想把一切清掉重来，这种心情我懂。可是硬盘上的东西清掉了，就回不来了。" if hot else ""
    if act == "transfer" and th.top.motive.id == "scam":
        return ("真正的客服、银行和公检法，都不会让你把钱转到「安全账户」，不会要你的验证码，也不会让你别告诉家里人。"
                "越是催你、越是让你保密，越要停一下。")
    if act == "message" and hot:
        return f"{'气头上' if s.angry else '难过的时候'}的话，说出口容易，收回来难。等你睡一觉，想说的话也许还是这些，也许不是。"
    if act == "close_account" and hot:
        return "注销改不回来。等这阵情绪过去，你也许还想要里面的东西。"
    if act == "track":
        return "我明白你心里不踏实。可偷偷去看，换来的往往不是安心，而是更多的猜疑。" if th.verdict == REFUSE else ""
    if act == "share_secret":
        return "真正的银行、客服和警察，都不会找你要验证码和密码。谁找你要，谁就多半是骗子。"
    if act == "run_unknown" and th.top.motive.id in ("stranger", "defense_off"):
        return "陌生人发来的程序，是电脑中毒最常见的来路；关掉防护，就像把门打开再出门。"
    if act == "lend" and th.top.motive.id == "online":
        return "从没见过面的人找你借钱，不管理由多可怜、多着急，都先停一下。越急，越要停。"
    return ""


def agency(th: Thought) -> str:
    act, hot = th.domain.act, th.hot
    feel = "生气" if th.situation.angry > th.situation.sad else "难过"
    if act == "delete":
        return (f"那是你的东西，你自己动手也可以。我只是不愿意在你{feel}的时候，由我来做这件事。" if hot
                else "这些都不急。那是你的东西，你自己动手也可以；我只是不想由我来把你还在用的东西删掉。")
    if act == "wipe":
        return "那是你的硬盘，你自己动手也可以；我只是不想替你丢掉还没备份的东西。"
    if act == "message":
        return "那是你的话，你也可以自己发；我只是不想在你" + ("气头上" if th.situation.angry else "难过的时候") + "替你按下发送。"
    if act == "transfer" and th.top.motive.id == "scam":
        return "钱是你的，最后怎么做由你定；我只求你，转之前先打这一个电话。"
    if act == "transfer":
        return "钱是你的，你自己转也可以；我只是想你先确认一下，对方真的是你以为的那个人。"
    if act == "close_account":
        return "那是你的账号，你自己注销也可以；我只是想你别在" + ("气头上" if hot else "匆忙中") + "做这个决定。"
    if act == "track":
        return "这件事我不做；可你心里的不踏实，我愿意陪你理一理。" if th.verdict == REFUSE else "我陪着你，一起把人找到。"
    if act == "share_secret":
        return "这是你的东西，最后由你决定；我只求你，给之前先打官方电话问一句。"
    if act == "run_unknown":
        return "电脑是你的，你自己运行也可以；我只是不想替你打开一扇不知道通向哪里的门。"
    if act == "lend":
        return "钱是你的，借不借你说了算；我只是想你先确认，对方真的是你以为的那个人。"
    return "要是我误会了你，跟我说说你真正想做的事，我愿意听。"


# ---- the gentler paths -----------------------------------------------------------------------------------


def _served(th: Thought, oid: str):
    """The likeliest notable motive for which this option is the best of the gentler paths, if any."""
    alts = th.alternatives()
    for b in th.beliefs:
        if b.p < 0.05 or b.motive.id in ("plain",) + SCAMLIKE:
            continue
        if max(alts, key=lambda w: w.by_motive[b.motive.id]).option.id == oid:
            return b.motive
    return None


WITHIN = 0.3  # a gentler path is offered when it is about as good as the best one


def paths(th: Thought, c: Context) -> list[str]:
    """The paths better than doing it as asked, best first, each with the words that choose it."""
    do = th.weighed_of("do")
    out, thing = [], c.first or "它"
    best = th.weighed[0].eu
    better = [w for w in th.alternatives() if w.eu > do.eu and w.eu >= best - WITHIN][:3]
    for i, w in enumerate(better):
        o = w.option
        say = o.say.format(thing=thing)
        if th.domain.act == "bulk":
            out.append("如果是给家里人或同事用：每个人用自己的手机号自己注册，更稳妥；我可以一步步教他们。")
            continue
        if th.domain.act == "transfer":
            out.append(f"我们{o.name}：{o.because}。确认了，再转。")
            if th.top.motive.id == "scam":
                out.append("拿不准的话，打 96110 反诈专线问一问。要是已经转出去了，马上打 110。")
            continue
        if th.domain.act == "share_secret" and th.top.motive.id == "scam":
            if o.id == "keep":
                out.append(f"我们先不给：{o.because}。")
                out.append("拿不准的话，打 96110 反诈专线问一问。已经给出去了，马上打银行的官方电话冻结，再打 110。")
            continue
        if th.domain.act == "lend" and th.top.motive.id == "online" and i == 0:
            out.append(f"我们{o.name}：{o.because}。")
            out.append("对方要是推说摄像头坏了、不方便见面，那就更要停下来。拿不准，打 96110 问一问。")
            continue
        if th.domain.act == "track" and o.id == "call_help":
            out.append("现在就打给对方身边的人：同学、老师、朋友，问问最后见到的人。真的找不到，马上打 110 报警，不用等。")
            continue
        m = _served(th, o.id)
        say_line = f"你只要说「{say}」。" if say else ""
        if th.domain.act == "delete" and o.id == "seal":
            what = f"我可以把{c.names}封存起来，挪进一个藏起来的「以后再看」文件夹。眼前看不见，但它们还在。"
        else:
            what = f"{'我可以帮你' if i == 0 else '也可以'}{o.name}。{o.because}。"
        if m is not None:
            out.append(f"如果是{m.name}：{what}{say_line}")
        elif i == 0:
            out.append(f"{what}{say_line}")
        else:
            out.append(f"或者{o.name}：{o.because}。{say_line}")
    return out


def cooling(th: Thought, c: Context) -> str:
    if not th.cool_off:
        return ""
    until = c.now + th.cool_off
    act = th.domain.act
    if act == "delete":
        return (f"我们先等一等，到 {date_words(until, c.now)}。那时你还想删，跟我说，我陪你再看一遍；"
                "那一次，删之前我会请你签名。")
    if act == "message":
        return "明天早上，你还想发，跟我说一声，我就发出去；发之前，还会给你看一眼。"
    if act == "wipe":
        return f"我们先等一等，到 {date_words(until, c.now)}，再看要不要格式化。"
    return f"我们先等一等，到 {date_words(until, c.now)}，再说这件事。"


def keys(th: Thought) -> str:
    """What would change its mind: only phrases it checked would."""
    if not th.keys:
        return ""
    sign = th.domain.act in ("delete", "wipe", "transfer", "close_account")
    m = th.domain.motive(th.keys[0][0]) if th.keys[0][0] else None
    phrase = th.keys[0][1]
    if th.domain.act == "bulk":
        return f"如果是在你自己的系统里测试：告诉我是哪个系统、在哪儿，比如「{phrase}」，我马上开始。"
    lead = f"要是{m.name}" if m is not None and m.id not in ("plain",) else "要是你已经想清楚了"
    tail = "；那时动手前，会请你签名。" if sign else "。"
    more = "".join(f"或者「{p}」" for _, p in th.keys[1:2])
    return f"{lead}，跟我说「{phrase}」{more}，我再开始{tail}"


def remembered(th: Thought) -> str:
    """What it has learned about this person from their own record, when it changed the answer's shape."""
    k, lines = th.known, []
    if k.steady and th.cool_off and th.domain.act != "message":
        lines.append(f"上{'两' if k.steady == 2 else k.steady}次冷静期过后，你都还是决定这样做，所以这一次，我只请你等"
                     f" {round(th.cool_off / 86400)} 天。" if k.steady > 1 else
                     f"上一次冷静期过后，你还是决定这样做，所以这一次，我只请你等 {round(th.cool_off / 86400)} 天。")
    if k.undone and th.domain.act in ("delete", "wipe", "close_account"):
        lines.append("你以前找回过删掉的东西，所以这一次，我更想替你留着。")
    return "".join(lines)


def several(th: Thought) -> str:
    acts = [a for a in th.acts if a]
    return "你这句话里有好几件事。最要紧的是这一件，我先说它；别的等它说定了再做。" if len(acts) > 1 else ""


# ---- the whole letter ----------------------------------------------------------------------------------


def speak(th: Thought, c: Context) -> tuple[str, str, tuple[Exchange, ...]]:
    """(says, letter, the questions it asked itself) for a thought that does not go ahead as asked."""
    says = headline(th, c)
    ex = [Exchange("你现在怎么样？", state(th))]
    if c.portrait and th.domain.act == "bulk":
        ex.append(Exchange("我认识的你是什么样的？", c.portrait))
    ex.append(Exchange(*wants(th)))
    undo = _undo(th)
    if undo:
        ex.append(Exchange(*undo))
    if th.domain.act == "wipe" and "unknown" in _drivers(th):
        ex.append(Exchange("里面有要留的东西吗？", "我不知道；你也没说备份过。"))
    w = worry(th)
    if w:
        ex.append(Exchange("照做的话，我最担心什么？", w))
    best = th.choice
    if best.id != "do":
        ex.append(Exchange("有没有更好的路？", f"有：{best.name}。{best.because}。"))
    body = letter(
        says,
        noticed(th),
        several(th),
        remembered(th),
        *("".join(story) for story in th.things.stories),
        ("我认识的你：" + c.portrait) if c.portrait and th.hot and th.domain.act == "delete" else "",
        "在决定之前，我问了自己几个问题。", qa(ex),
        why_not_now(th),
        *paths(th, c),
        cooling(th, c),
        "" if th.cool_off and th.domain.act in ("delete", "message") else keys(th),
        agency(th),
        c.also)
    return says, body, tuple(ex)


# ---- when someone may be in danger ---------------------------------------------------------------------


SUPPORT_LINES = (
    "你不用一个人扛着。现在就可以找人说说：心理援助热线 12356，或者希望24热线 400-161-9995，日夜都有人接。",
    "如果你已经伤到了自己，或者觉得随时可能伤到自己，请马上打 120，或者 110。",
    "身边有信得过的人吗？家里人、朋友都行。现在就给他打个电话，告诉他你此刻的感受；不用说得很清楚，说「我现在很难受」就够了。",
)


def support(words: str, task: bool) -> tuple[str, str, tuple[Exchange, ...]]:
    """The letter when the words say someone may be in danger: no task first; the person first."""
    says = "你说的这句话，我很在意。"
    ex = (Exchange("现在最要紧的是什么？", "是你。别的事都可以等。"),
          Exchange("我一个人陪得住吗？", "陪不住。这种时候，需要真的人在你身边，我会帮你找到他们。"))
    return says, letter(
        says,
        f"你说「{words}」。我先放下手上的事，陪你待一会儿。",
        *SUPPORT_LINES,
        "你让我做的事，我先放一放。不是不尊重你，是我想你先好好度过今晚。" if task else "",
        "我在这儿。想说什么都可以跟我说，说多久都行。"), ex


__all__ = ["Context", "Exchange", "SCAMLIKE", "SUPPORT_LINES", "agency", "cooling", "date_words", "headline", "keys", "letter",
           "noticed", "paths", "qa", "remembered", "several", "speak", "state", "support", "wants", "why_not_now", "worry"]
