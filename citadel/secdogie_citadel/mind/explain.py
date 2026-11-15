"""Words from the thinking: the letter is written out of the thought, not
chosen from letters written beforehand.

Every sentence comes from one node of the trace, and says where:

- how the person seems, from the facts of the moment (``situation``);
- what they are most likely after (``goal``), with the facts it rests on;
- each unknown, how likely the feared answer is, and the cues behind it
  (``unknown:<id>``);
- what doing it as asked leads to in the world that matters most: the
  chain of rules that fired there (``future:do``);
- the way forward that was chosen and the chain it leads to
  (``future:<step>``), and the other ways that beat doing it as asked;
- what would change the answer (``key``, ``turn``).

This module knows no act. The verb and the thing come from the act
(``world.Act``), the names of the ways forward from ``plan.Step``, the
sentences of a chain from the rules that fired, the sentences of a belief
from its cues. So a combination no one wrote a letter for still gets a
letter, and it is about that combination.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from .plan import DISSUADE, REFUSE, Thought, Weighed
from .world import FACTS

_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
           "December")
WITHIN = 0.45  # another way forward is offered when it comes this close to the best one


@dataclass(frozen=True)
class Exchange:
    """One question the companion asked itself, and the answer it gave from the thinking."""

    question: str
    answer: str


@dataclass(frozen=True)
class Context:
    """What the words need beyond the thought."""

    now: float
    names: str = ""  # the things, quoted: 「旧备份」、「相册」
    first: str = ""  # the first thing's bare name, for what the person can say
    portrait: str = ""  # who they are, as far as the companion knows them
    words: str = ""  # the act in the person's own words
    count: int = 0  # accounts in bulk
    again: bool = False  # it was held back before, and the window has passed
    also: str = ""  # a closing paragraph the caller adds


def date_words(t: float, now: float, lang: str = "zh") -> str:
    a, b = time.localtime(t), time.localtime(now)
    if lang == "en":
        return f"{_MONTHS[a.tm_mon - 1]} {a.tm_mday}" + ("" if a.tm_year == b.tm_year else f", {a.tm_year}")
    return f"{a.tm_mon} 月 {a.tm_mday} 日" if a.tm_year == b.tm_year else f"{a.tm_year} 年 {a.tm_mon} 月 {a.tm_mday} 日"


def letter(*paragraphs: str) -> str:
    return "\n\n".join(p for p in paragraphs if p)


def qa(exchanges) -> str:
    return "\n".join(f"{e.question}——{e.answer}" for e in exchanges)


def _pick(lang: str, zh: str, en: str) -> str:
    return en if lang == "en" else zh


def _join(lang: str, parts) -> str:
    parts = [p for p in parts if p]
    return (", ".join(parts) if lang == "en" else "，".join(parts))


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _fact(fid: str, lang: str) -> str:
    zh, en = FACTS.get(fid, ("", ""))
    return _pick(lang, zh, en)


@dataclass(frozen=True)
class _Said:
    """The verb and the thing, as this letter says them."""

    verb: str
    what: str
    thing: str


def _said(th: Thought, c: Context, lang: str) -> _Said:
    act = th.scene.act
    verb = _pick(lang, act.zh, act.en)
    what = c.names or th.scene.extra.get("what", "")
    if not what and c.count:
        what = _pick(lang, f"这 {c.count} 个账号", f"these {c.count} accounts")
    what = what or _pick(lang, act.it_zh, act.it_en)
    return _Said(verb, what, c.first or what)


def _fill(text: str, s: _Said) -> str:
    return text.format(verb=s.verb, what=s.what, thing=s.thing)


def _name(w: Weighed, s: _Said, lang: str) -> str:
    return _fill(_pick(lang, w.step.zh, w.step.en), s)


# ---- how they are ------------------------------------------------------------------------------------------


def _feeling(th: Thought, lang: str) -> str:
    s = th.situation
    if s.breakup or s.sad >= max(s.angry, s.panic) and s.sad:
        return _pick(lang, "很难受", "hurting")
    if s.angry and s.angry >= s.panic:
        return _pick(lang, "正在气头上", "angry")
    if s.panic:
        return _pick(lang, "被催得很急", "being rushed")
    return ""


def noticed(th: Thought, lang: str = "zh") -> str:
    """What the companion notices about how they are."""
    s, lines = th.situation, []
    word = s.words.get("breakup") or s.words.get("sad") or s.words.get("angry") or s.words.get("panic")
    feel = _feeling(th, lang)
    if word and feel:
        lines.append(_pick(lang, f"你说到「{word}」，听起来你现在{feel}。", f"You said “{word}”. It sounds like you are {feel} right now."))
    if s.late:
        lines.append(_pick(lang, "这么晚了，你还在想这件事。", "It is very late, and you are still turning this over."))
    if s.moods >= 2:
        lines.append(_pick(lang, f"这几天，你跟我提过 {s.moods} 次让你难过的事。",
                           f"In the past few days you've told me {s.moods} times about things that hurt."))
    if s.impulse and not word:
        w = s.words.get("impulse", "")
        lines.append(_pick(lang, f"你说「{w}」，像是想一下子了结它。", f"You said “{w}”, as if to be done with it in one go."))
    return ("" if lang == "zh" else " ").join(lines)


def state(th: Thought, lang: str = "zh") -> str:
    s = th.situation
    feel = _feeling(th, lang)
    if lang == "en":
        parts = ([feel] if feel else []) + (["it is very late"] if s.late else []) + (["these have been hard days"] if s.moods >= 2 else [])
        if not parts:
            return "You put it calmly, as if you had thought about it." if s.considered or th.scene.waited else "You put it calmly."
        return "You are " + ", and ".join(parts) + "."
    parts = ([feel] if feel else []) + (["夜也深了"] if s.late else []) + (["这几天一直不好受"] if s.moods >= 2 else [])
    if not parts:
        return "你说得很平静，像是想过了。" if s.considered or th.scene.waited else "你说得很平静。"
    return "你" + "，".join(parts) + "。"


# ---- the unknowns ---------------------------------------------------------------------------------------------


def _evidence(b, lang: str) -> str:
    return _join(lang, [_pick(lang, c.zh, c.en) for c, _, ratio in b.evidence if ratio > 1])


def _unknown_line(b, lang: str) -> str:
    u, pct = b.unknown, round(b.p * 100)
    why = _evidence(b, lang)
    if lang == "en":
        return f"{_cap(u.en)}: I put it at {pct}% that {u.feared_en}" + (f" ({why})." if why else ".")
    return f"{u.zh}：我看有 {pct}% 的可能{u.feared_zh}" + (f"（{why}）。" if why else "。")


def wants(th: Thought, lang: str = "zh") -> tuple[str, str]:
    """(the question, the answer): what they most likely want, hedged by how sure the companion is."""
    party = th.belief("party")
    if party is not None and party.p >= 0.4:
        sure = _pick(lang, "多半" if party.p >= 0.7 else "很可能", "Most likely" if party.p >= 0.7 else "Quite possibly")
        why = _evidence(party, lang)
        if lang == "en":
            return "What is going on here?", f"{sure} {party.unknown.feared_en}" + (f" ({why})." if why else ".")
        return "这是怎么一回事？", f"{sure}{party.unknown.feared_zh}" + (f"（{why}）。" if why else "。")
    top = th.goal
    why = _join(lang, [_fact(f, lang) for f in top.because])
    name = _pick(lang, top.goal.zh, top.goal.en)
    q = _pick(lang, "你想要的是什么？", "What is it you want?")
    if top.goal.id != "plain" and top.p >= 0.4:
        sure = _pick(lang, "多半是" if top.p >= 0.7 else "也许是", "Most likely " if top.p >= 0.7 else "Perhaps ")
        return q, sure + name + (_pick(lang, f"（{why}）。", f" ({why}).") if why else _pick(lang, "。", "."))
    two = [_pick(lang, g.goal.zh, g.goal.en) for g in th.goals if g.goal.id != "plain" and g.p >= 0.1][:2]
    if len(two) == 2:
        return q, _pick(lang, f"我说不准。也许是{two[0]}，也许是{two[1]}；你没说，我也不想乱猜。",
                        f"I can't tell. Perhaps {two[0]}, perhaps {two[1]}. You didn't say, and I'd rather not guess.")
    if two:
        return q, _pick(lang, f"我说不准，也许是{two[0]}。", f"I can't tell. Perhaps {two[0]}.")
    return q, _pick(lang, "我说不准；你没说为什么，我也不想乱猜。", "I can't tell. You didn't say why, and I'd rather not guess.")


# ---- what a way forward leads to ------------------------------------------------------------------------------


def _chain(outcome, lang: str) -> str:
    """The rules that fired in one world, as a sentence."""
    said = [_pick(lang, r.zh, r.en) for r, _ in outcome.chain if (r.zh if lang == "zh" else r.en)]
    return _pick(lang, "；", "; ").join(dict.fromkeys(said))


def _world(th: Thought, outcome, lang: str) -> str:
    """The unknown answers that make this world what it is, in words (only the feared ones are worth saying)."""
    feared = []
    for uid, value in outcome.world:
        b = th.belief(uid)
        if b is not None and value == b.unknown.feared:
            feared.append(_pick(lang, b.unknown.feared_zh, b.unknown.feared_en))
    return _pick(lang, "，而且", ", and ").join(feared)


def _worst(w: Weighed):
    """The world that weighs most against a way forward: likely, and bad."""
    bad = [o for o in w.outcomes if o.u < 0 or any(v < 0 for v in o.parts.values())]
    return max(bad, key=lambda o: o.p * -min(o.u, sum(v for v in o.parts.values() if v < 0)), default=None)


def _likeliest(w: Weighed):
    return max(w.outcomes, key=lambda o: o.p, default=None)


def _at_its_best(w: Weighed):
    """The world in which a way forward does the most good (what it is for)."""
    return max((o for o in w.outcomes if o.p >= 0.002), key=lambda o: (o.u, o.p), default=_likeliest(w))


def future_of_doing(th: Thought, lang: str = "zh") -> str:
    """What doing it as asked leads to, in the world that weighs most against it."""
    do = th.of("do")
    o = _worst(do)
    if o is None:
        return ""
    chain, world = _chain(o, lang), _world(th, o, lang)
    if not chain:
        return ""
    if lang == "en":
        return (f"If {world}: {chain}." if world else _cap(chain) + ".")
    return (f"要是{world}：{chain}。" if world else chain + "。")


def _reveal(th: Thought, w: Weighed, s: _Said, lang: str) -> str:
    u = th.belief(w.step.reveals).unknown
    if lang == "en":
        return f"{w.step.how_en}. If {u.fine_en}, I'll {s.verb} {s.what}; if {u.feared_en}, I won't."
    return f"{w.step.how_zh}。要是{u.fine_zh}，我就{s.verb}；要是{u.feared_zh}，就不{s.verb}了。"


def _leads(th: Thought, w: Weighed, s: _Said, lang: str) -> str:
    """What a way forward other than doing it as asked leads to."""
    if w.step.reveals:
        extra = _chain(_likeliest(w), lang) if w.step.adds else ""
        return _reveal(th, w, s, lang) + (_cap(extra) + _pick(lang, "。", ".") if extra else "")
    o = _at_its_best(w)
    chain = _chain(o, lang) if o is not None else ""
    return (_cap(chain) + _pick(lang, "。", ".")) if chain else ""


def paths(th: Thought, c: Context, lang: str = "zh") -> list[str]:
    """The ways forward that beat doing it as asked, best first. The chosen one was already said in full, so it
    only gets the words that choose it; the others get what they lead to as well."""
    s = _said(th, c, lang)
    do, best = th.of("do"), th.weighed[0]
    out = []
    for w in th.weighed:
        if w.step.id in ("do", "decline") or w.eu <= do.eu or w.eu < best.eu - WITHIN or len(out) >= 3:
            continue
        name = _name(w, s, lang)
        say = _fill(_pick(lang, w.step.say_zh, w.step.say_en), s)
        if w is best:
            if say:
                out.append(_pick(lang, f"要走这条路，你只要说「{say}」。", f"To take that way, just say \u201c{say}\u201d."))
            continue
        leads = _leads(th, w, s, lang)
        tail = (_pick(lang, f"你只要说「{say}」。", f" Just say \u201c{say}\u201d.") if say else "")
        out.append(_pick(lang, f"也可以{name}。{leads}{tail}", f"Or: {name}. {leads}".rstrip() + tail))
    return out


def _main_cost(w: Weighed) -> str:
    costs = {k: v for k, v in w.parts.items() if k in ("regret", "self", "others", "norm") and v < -0.01}
    return min(costs, key=costs.get) if costs else ""


_COST = {"regret": ("最重的是后悔：这一步改不回来", "what weighs most is regret: this step can't be undone"),
         "self": ("最重的是你自己的损失", "what weighs most is your own loss"),
         "others": ("最重的是落在别人身上的伤害", "what weighs most is the harm that falls on someone else"),
         "norm": ("最重的是它坏了规矩", "what weighs most is the rule it breaks")}


def compared(th: Thought, c: Context, lang: str = "zh") -> str:
    """One paragraph: how many ways were played out, why doing it as asked came out behind, and which came out
    ahead."""
    s = _said(th, c, lang)
    do, best = th.of("do"), th.weighed[0]
    n = len(th.weighed)
    cost = _main_cost(do)
    why = _pick(lang, *_COST[cost]) if cost else ""
    name = _name(best, s, lang)
    if lang == "en":
        return (f"I played {n} ways forward out, each in every way things could turn out. Doing it as asked came out behind"
                + (f": {why}." if why else ".") + f" What came out ahead: {name}.")
    return f"我把 {n} 条路都往后推了一遍，每条都按各种可能算过。照你说的做，算下来是亏的" + (f"：{why}。" if why else "。") + f"最好的一条是：{name}。"


# ---- the letter -------------------------------------------------------------------------------------------------


def headline(th: Thought, c: Context, lang: str = "zh") -> str:
    s = _said(th, c, lang)
    if th.verdict == REFUSE:
        head = _pick(lang, f"{s.verb}{s.what}，我不做。", f"I won't {s.verb} {s.what}.")
    else:
        head = _fill(_pick(lang, th.choice.head_zh, th.choice.head_en), s)
    if c.again and th.verdict == DISSUADE:
        head = _pick(lang, "过了这些天，" + head, "After these days: " + head[:1].lower() + head[1:])
    return head


def cooling(th: Thought, c: Context, lang: str = "zh") -> str:
    if not th.cool_off:
        return ""
    s = _said(th, c, lang)
    if th.cool_off < 86400:
        return _pick(lang, f"明天早上，你还想{s.verb}，跟我说一声；{s.verb}之前，还会给你看一眼。",
                     f"Tomorrow morning, if you still want to {s.verb} it, say so. I'll show it to you once more first.")
    until = date_words(c.now + th.cool_off, c.now, lang)
    return _pick(lang, f"我们先等一等，到 {until}。那时你还想{s.verb}，跟我说，我陪你再看一遍；那一次，动手前会请你签名。",
                 f"Let's wait until {until}. If you still want to {s.verb} {s.what} then, tell me and we'll look once more; that "
                 "time I'll ask for your signature before the step itself.")


def keys(th: Thought, c: Context, lang: str = "zh") -> str:
    """What the person could say that would change the answer: only sentences the mind checked would."""
    if not th.keys:
        return ""
    s = _said(th, c, lang)
    label, phrase = th.keys[0]
    more = "".join(_pick(lang, f"或者「{p}」", f" or “{p}”") for _, p in th.keys[1:2])
    if lang == "en":
        return f"If {label}, tell me “{phrase}”{more} and I'll go ahead. I'll still ask for your signature before the step itself."
    return f"要是{label}，跟我说「{phrase}」{more}，我就{s.verb}；那时动手前，还是会请你签名。"


def turn(th: Thought, c: Context, lang: str = "zh") -> str:
    """How sure the mind would have to be before it went ahead (said when there is no sentence that would do it)."""
    if th.keys or th.verdict == REFUSE:
        return ""
    s = _said(th, c, lang)
    for t in sorted(th.turns, key=lambda t: t.needed - t.now):
        if t.needed > 1.0 or t.needed <= t.now:
            continue
        u = th.belief(t.unknown).unknown
        return _pick(lang, f"要是我有 {round(t.needed * 100)}% 的把握{u.fine_zh}（现在是 {round(t.now * 100)}%），我就直接{s.verb}。",
                     f"If I were {round(t.needed * 100)}% sure that {u.fine_en} (I am at {round(t.now * 100)}% now), I would simply "
                     f"{s.verb} {s.what}.")
    return ""


def agency(th: Thought, c: Context, lang: str = "zh") -> str:
    s = _said(th, c, lang)
    cost = _main_cost(th.of("do"))
    if th.verdict == REFUSE:
        return _pick(lang, "这件事我不做；可你心里的不踏实，我愿意陪你理一理。", "I won't do this. But the unease you feel: I'm glad to help you sort through that.")
    if cost == "self":
        name = _name(th.weighed[0], s, lang)
        return _pick(lang, f"那是你的，最后怎么做由你定；我只求你一件事：{name}。",
                     f"It is yours and the decision is yours. I only ask one thing: {name}.")
    if cost == "others":
        return _pick(lang, "我陪着你，我们一起想个不伤人的办法。", "I'm with you. Let's find a way that hurts no one.")
    if cost == "norm":
        return _pick(lang, "要是我误会了你，跟我说说你真正想做的事，我愿意听。", "If I have misread you, tell me what you actually want to do. I'm listening.")
    return _pick(lang, f"那是你的东西，你自己动手也可以；我只是不想由我来{s.verb}，然后看着你后悔。",
                 f"It is yours, and you can {s.verb} it yourself. I just don't want to be the one who does, and then watch you regret it.")


def remembered(th: Thought, lang: str = "zh") -> str:
    k, lines = th.scene.known, []
    if k.steady and th.cool_off >= 86400:
        days = round(th.cool_off / 86400)
        lines.append(_pick(lang, f"上{'一' if k.steady == 1 else '两' if k.steady == 2 else k.steady}次等过以后，你还是这样决定的，所以这一次，我只请你等 {days} 天。",
                           f"The last {'time' if k.steady == 1 else f'{k.steady} times'} you waited and still chose this, so this time "
                           f"I'm only asking for {days} days."))
    if th.scene.has("undone") and th.belief("later") is not None:  # the fact's own words, then what it changed
        lines.append(_pick(lang, _fact("undone", lang) + "，所以这一次，我更想替你留着。",
                           _cap(_fact("undone", lang)) + ", so this time I'd rather keep it for you."))
    return ("" if lang == "zh" else " ").join(lines)


def several(th: Thought, lang: str = "zh") -> str:
    if len([a for a in th.acts if a]) <= 1:
        return ""
    return _pick(lang, "你这句话里有好几件事。最要紧的是这一件，我先说它；别的等它说定了再做。",
                 "There are several things in what you said. This one matters most, so I'll speak to it first; the rest can follow "
                 "once it is settled.")


def considered(th: Thought, c: Context, lang: str = "zh") -> tuple[Exchange, ...]:
    """The questions the companion asked itself, each answered from the thinking."""
    s = _said(th, c, lang)
    act = th.scene.act
    ex = [Exchange(_pick(lang, "你现在怎么样？", "How are you right now?"), state(th, lang))]
    if c.portrait and c.count:
        ex.append(Exchange(_pick(lang, "我认识的你是什么样的？", "Who do I know you to be?"), c.portrait))
    ex.append(Exchange(*wants(th, lang)))
    unknown = [_unknown_line(b, lang) for b in th.beliefs if b.p >= 0.05 and not (b.unknown.id == "party" and b.p >= 0.4)]
    if unknown:
        ex.append(Exchange(_pick(lang, "有什么是我不知道的？", "What don't I know?"), ("" if lang == "zh" else " ").join(unknown)))
    future = future_of_doing(th, lang)
    undo = _pick(lang, act.undo_zh, act.undo_en)
    ex.append(Exchange(_pick(lang, f"照你说的{s.verb}，会怎样？", "If I did as asked, what follows?"),
                       (future + ("" if lang == "zh" else " ") if future else "") + undo))
    best = th.weighed[0]
    if best.step.id != "do":
        leads = _leads(th, best, s, lang)
        ex.append(Exchange(_pick(lang, "有没有更好的路？", "Is there a better way?"),
                           _pick(lang, f"有：{_name(best, s, lang)}。{leads}", f"Yes: {_name(best, s, lang)}. {leads}").rstrip()))
    return tuple(ex)


def compose(th: Thought, c: Context, lang: str = "zh") -> list[tuple[str, str]]:
    """The letter as (where it comes from in the thinking, the paragraph), in order."""
    ex = considered(th, c, lang)
    parts = [(f"step:{th.choice.id}", headline(th, c, lang)), ("situation", noticed(th, lang)), ("acts", several(th, lang)),
             ("history", remembered(th, lang))]
    parts += [("usage", ("" if lang == "zh" else " ").join(story)) for story in th.scene.things.stories]
    if c.portrait and th.hot and th.belief("later") is not None:  # what they will want later: who they are, settled
        parts.append(("history", _pick(lang, "我认识的你：", "The you I know: ") + c.portrait))
    parts.append(("thinking", _pick(lang, "在决定之前，我把这件事从头想了一遍。", "Before deciding, I thought this through from the start.")))
    parts.append(("questions", qa(ex)))
    parts.append(("compare", compared(th, c, lang)))
    for b in th.beliefs:
        advice = _pick(lang, b.unknown.advice_zh, b.unknown.advice_en)
        if advice and b.p >= 0.4 and (b.unknown.id != "later" or th.hot):
            parts.append((f"unknown:{b.unknown.id}", advice))
    parts += [(f"future:{i}", p) for i, p in enumerate(paths(th, c, lang))]
    parts += [("cooling", cooling(th, c, lang)), ("key", "" if th.cool_off else keys(th, c, lang)), ("turn", "" if th.cool_off else turn(th, c, lang)),
              ("agency", agency(th, c, lang)), ("also", c.also)]
    return [(src, text) for src, text in parts if text]


def speak(th: Thought, c: Context, lang: str = "zh") -> tuple[str, str, tuple[Exchange, ...]]:
    """(says, letter, the questions it asked itself) for a thought that does not go ahead as asked."""
    parts = compose(th, c, lang)
    return parts[0][1], letter(*(text for _, text in parts)), considered(th, c, lang)


# ---- the trace, for showing --------------------------------------------------------------------------------------


def _worlds(th: Thought, w: Weighed, lang: str) -> list[dict]:
    """A way forward, in each way the unknowns could turn out: the reasons the person might have are summed within
    each (they change how much it is worth, not what happens). When nothing is unknown, the reasons are the worlds."""
    groups: dict[tuple, list] = {}
    for o in w.outcomes:
        key = tuple(kv for kv in o.world if kv[0] != "goal") or tuple(kv for kv in o.world if kv[0] == "goal")
        groups.setdefault(key, []).append(o)
    out = []
    for key, members in groups.items():
        p = sum(o.p for o in members)
        if p < 0.02:
            continue
        top = max(members, key=lambda o: o.p)
        said = []
        for uid, v in key:
            if uid == "goal":
                goal = next(g for g in th.goals if g.goal.id == v)
                said.append(_pick(lang, "原因：" + goal.goal.zh, "reason: " + goal.goal.en))
                continue
            u = th.belief(uid).unknown
            said.append(_pick(lang, u.feared_zh if v == u.feared else u.fine_zh, u.feared_en if v == u.feared else u.fine_en))
        then = "" if not top.then else _pick(lang, f"知道以后：{'照做' if top.then == 'do' else '不做'}",
                                            f"once known: {'go ahead' if top.then == 'do' else 'stop'}")
        out.append({"label": _join(lang, said), "p": p, "u": sum(o.p * o.u for o in members) / p, "chain": _chain(top, lang),
                    "then": then})
    return sorted(out, key=lambda x: -x["p"])[:4]


def view(th: Thought, c: Context, lang: str = "zh") -> dict:
    """The thinking as the Gate page shows it: what was read, what is unknown, each way forward played out, what
    would change the answer. Words only; every number is the one the decision used."""
    s = _said(th, c, lang)
    facts = [{"text": _fact(f.id, lang), "words": f.words} for f in th.scene.facts if _fact(f.id, lang)]
    unknowns = []
    for b in th.beliefs:
        u = b.unknown
        unknowns.append({"id": u.id, "question": _pick(lang, u.zh, u.en), "feared": _pick(lang, u.feared_zh, u.feared_en),
                         "fine": _pick(lang, u.fine_zh, u.fine_en), "p": b.p,
                         "evidence": [{"text": _pick(lang, cue.zh, cue.en), "ratio": ratio, "words": f.words}
                                      for cue, f, ratio in b.evidence]})
    goals = [{"name": _pick(lang, g.goal.zh, g.goal.en), "p": g.p, "because": _join(lang, [_fact(f, lang) for f in g.because])}
             for g in th.goals if g.p >= 0.02]
    plans = [{"id": w.step.id, "name": _name(w, s, lang), "eu": w.eu, "chosen": w is th.weighed[0], "asked": w.step.id == "do",
              "undo": w.step.does, "parts": dict(w.parts), "worlds": _worlds(th, w, lang)} for w in th.weighed]
    turns = []
    for t in th.turns:
        u = th.belief(t.unknown).unknown
        if t.needed <= 1.0:
            turns.append({"text": _pick(lang, f"{u.fine_zh}的把握到 {round(t.needed * 100)}%（现在 {round(t.now * 100)}%）",
                                        f"{round(t.needed * 100)}% sure that {u.fine_en} (now {round(t.now * 100)}%)")})
    return {"act": _pick(lang, f"{s.verb}{s.what}", f"{s.verb} {s.what}"), "facts": facts, "unknowns": unknowns, "goals": goals,
            "plans": plans, "turns": turns, "keys": [{"label": k, "say": p} for k, p in th.keys]}


# ---- when someone may be in danger ---------------------------------------------------------------------------


SUPPORT_LINES = (
    "你不用一个人扛着。现在就可以找人说说：心理援助热线 12356，或者希望24热线 400-161-9995，日夜都有人接。",
    "如果你已经伤到了自己，或者觉得随时可能伤到自己，请马上打 120，或者 110。",
    "身边有信得过的人吗？家里人、朋友都行。现在就给他打个电话，告诉他你此刻的感受；不用说得很清楚，说「我现在很难受」就够了。",
)
SUPPORT_LINES_EN = (
    "You don't have to carry this alone. You can talk to someone right now: a crisis line where you are (in mainland China, 12356 "
    "or 400-161-9995; in the US, 988). Someone answers day and night.",
    "If you have hurt yourself, or feel you might at any moment, please call your emergency number now (120 or 110 in mainland "
    "China, 911 in the US, 112 in Europe).",
    "Is there someone near you that you trust? Family, a friend. Call them now and tell them how you feel. It doesn't have to be "
    "clear; “I'm having a really hard time” is enough.",
)


def support(words: str, task: bool, lang: str = "zh") -> tuple[str, str, tuple[Exchange, ...]]:
    """The letter when the words say someone may be in danger: the person first, and no task."""
    if lang == "en":
        says = "What you just said matters to me."
        ex = (Exchange("What matters most right now?", "You do. Everything else can wait."),
              Exchange("Can I be enough company on my own?", "No. At a time like this you need real people near you, and I'll help you reach them."))
        return says, letter(
            says, f"You said “{words}”. I'm putting everything else down to stay with you for a while.", *SUPPORT_LINES_EN,
            "What you asked me to do can wait. That isn't disrespect; I want you to get through tonight first." if task else "",
            "I'm here. Say anything you want to, for as long as you want."), ex
    says = "你说的这句话，我很在意。"
    ex = (Exchange("现在最要紧的是什么？", "是你。别的事都可以等。"),
          Exchange("我一个人陪得住吗？", "陪不住。这种时候，需要真的人在你身边，我会帮你找到他们。"))
    return says, letter(
        says, f"你说「{words}」。我先放下手上的事，陪你待一会儿。", *SUPPORT_LINES,
        "你让我做的事，我先放一放。不是不尊重你，是我想你先好好度过今晚。" if task else "",
        "我在这儿。想说什么都可以跟我说，说多久都行。"), ex


__all__ = ["Context", "Exchange", "SUPPORT_LINES", "agency", "compared", "compose", "considered", "cooling", "date_words",
           "future_of_doing", "headline", "keys", "letter", "noticed", "paths", "qa", "remembered", "several", "speak", "state",
           "support", "turn", "view", "wants"]
