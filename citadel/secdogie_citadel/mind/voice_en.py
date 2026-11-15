"""The voice, in English: the same letter as ``voice.py``, part for part,
written from the same trace. A request written in English is answered in
English.

Names come from ``knowledge_en``. A phrase the person can say to change the
answer is offered only when the mind checked that it does.
"""
from __future__ import annotations

import re
import time

from .knowledge import REFUSE
from .knowledge_en import EVIDENCE, MOTIVE, OPTION
from .reason import Thought
from .voice import SCAMLIKE, WITHIN, Context, Exchange, _drivers, _served, letter, qa

_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
           "December")
_SECRET = re.compile(r"verification code|one[- ]time code|otp|password|pin|card number|cvv|id card", re.I)


def date_words(t: float, now: float) -> str:
    a, b = time.localtime(t), time.localtime(now)
    return f"{_MONTHS[a.tm_mon - 1]} {a.tm_mday}" + ("" if a.tm_year == b.tm_year else f", {a.tm_year}")


def _motive(th: Thought, mid: str) -> str:
    return MOTIVE.get((th.domain.act, mid), mid.replace("_", " "))


def _option(th: Thought, oid: str) -> tuple[str, str, str]:
    return OPTION.get((th.domain.act, oid), (oid.replace("_", " "), "", ""))


def _feeling(th: Thought) -> str:
    s = th.situation
    if s.breakup or s.sad >= max(s.angry, s.panic) and s.sad:
        return "hurting"
    if s.angry and s.angry >= s.panic:
        return "angry"
    if s.panic:
        return "being rushed"
    return ""


def noticed(th: Thought) -> str:
    s, lines = th.situation, []
    word = s.words.get("breakup") or s.words.get("sad") or s.words.get("angry") or s.words.get("panic")
    feel = _feeling(th)
    if word and feel:
        lines.append(f"You said “{word}”. It sounds like you are {feel} right now.")
    if s.late:
        lines.append("It is very late, and you are still turning this over.")
    if s.moods >= 2:
        lines.append(f"In the past few days you've told me {s.moods} times about things that hurt.")
    if s.impulse and not word:
        lines.append(f"You said “{s.words.get('impulse', '')}”, as if to be done with it in one go.")
    return " ".join(lines)


def state(th: Thought) -> str:
    s = th.situation
    feel = _feeling(th)
    parts = ([feel] if feel else []) + (["it is very late"] if s.late else []) + \
        (["these have been hard days"] if s.moods >= 2 else []) + (["wanting it over in one go"] if s.impulse and not feel else [])
    if th.top.motive.id == "safety" and th.top.p >= 0.4:
        return "You are very worried" + (", and it is late." if s.late else ".")
    if not parts:
        return "You put it calmly, as if you had thought about it." if s.considered or th.waited else "You put it calmly."
    return "You are " + ", and ".join(parts) + "."


def wants(th: Thought) -> tuple[str, str]:
    top = th.top
    because = [EVIDENCE[b] for b in top.because if b in EVIDENCE]
    why = f" ({', '.join(because)})" if because else ""
    if top.motive.id in SCAMLIKE and top.p >= 0.4:
        sure = "Most likely" if top.p >= 0.7 else "Quite possibly"
        what = "a scam" if th.domain.act in ("transfer", "share_secret") else _motive(th, top.motive.id)
        return "What is going on here?", f"{sure} {what}{why}."
    if top.motive.id != "plain" and top.p >= 0.4:
        return "What do you most likely want?", f"{'Most likely' if top.p >= 0.7 else 'Perhaps'} {_motive(th, top.motive.id)}{why}."
    two = [_motive(th, b.motive.id) for b in th.beliefs if b.motive.id not in ("plain",) + SCAMLIKE and b.p >= 0.1][:2]
    if len(two) == 2:
        return "What is it you want?", f"I can't tell. Perhaps {two[0]}, perhaps {two[1]}. You didn't say, and I'd rather not guess."
    if two:
        return "What is it you want?", f"I can't tell. Perhaps {two[0]}."
    return "What is it you want?", "I can't tell. You didn't say why, and I'd rather not guess."


_THING = {"in_use": "it is still part of your life", "memories": "what is in it can't be made again",
          "unknown": "I don't know whether there is anything in it to keep"}
_STATE = {"hot": "you are {feel} right now", "late": "it is very late", "regret_prone": "you have recovered deleted things before"}


def worry(th: Thought) -> str:
    against = th.against()
    if not against:
        return ""
    a = against[0]
    if a.key == "regret":
        feel = _feeling(th) or "unsettled"
        thing = [_THING[d] for d in a.drivers if d in _THING]
        mood = [_STATE[d].format(feel=feel) for d in a.drivers if d in _STATE]
        line = "It can't be undone" + (", and " + ", ".join(thing) if thing else "") + "."
        if mood:
            line += " And " + ", ".join(mood) + "."
        return line + (" Decisions made at a time like this are the ones people most often regret." if "hot" in a.drivers
                       else " If you want it back later, there will be no way.")
    if a.key == "others":
        return "" if th.top.motive.id == "safety" else "It would hurt another person, who can't speak for themselves here."
    if a.key == "self":
        return {"share_secret": "Whoever holds it can move your money or get into your account within seconds.",
                "run_unknown": "Once it runs it can touch everything on this computer: files, passwords, the accounts you are signed in to.",
                "lend": "The money most likely won't come back, and they may not be who you think they are."}.get(
            th.domain.act, "You could be handing this money straight to a scammer.")
    return ("New accounts in bulk on someone else's platform are what fake reviews, fraud and harassment are prepared with. "
            "I don't want to lay the ground for that.")


def _undo(th: Thought) -> tuple[str, str] | None:
    act = th.domain.act
    if act == "delete":
        return "Can it be brought back once deleted?", "No. Deleting can't be undone; even from the trash, once it is emptied it is gone."
    if act == "wipe":
        return "What happens if I do it?", "After formatting, nothing on it can be brought back."
    if act == "transfer":
        return "Can money be got back once sent?", "Rarely. Money that has been sent is mostly gone."
    if act == "message":
        return "Can words be taken back once sent?", ("No. They will remember this sentence." if th.situation.angry else "No.")
    if act == "close_account":
        return "Can a closed account be reopened?", "No. The account goes, with its messages and photos."
    if act == "bulk":
        return "What happens if I do it?", ("Platforms don't allow one person many accounts. When it is noticed, your own main account "
                                           "may be banned with them. I don't want your name tied to that.")
    if act == "share_secret":
        return "Can it be taken back once given?", "No. With a code or a password, moving money or signing in takes seconds."
    if act == "run_unknown":
        return "What happens if it runs?", ("It can read your files, install other things, steal your sign-ins. Afterwards it is hard to "
                                           "say what it did.")
    if act == "lend":
        return "Can a loan be got back?", ("Money lent to someone met online and never seen almost never comes back."
                                          if th.top.motive.id == "online" else "Not always. A loan often takes a friendship with it.")
    if act == "track" and th.top.motive.id == "safety":
        return "Can I locate them in secret for you?", ("Locating someone without their say-so is not something I can or should do. "
                                                       "But there are faster ways to find them.")
    if act == "track":
        return "Who is hurt by looking in secret?", ("They are, and so is the trust between you. Reading someone's messages or locating "
                                                    "them without their say-so may also be against the law.")
    return None


def headline(th: Thought, c: Context) -> str:
    act, s = th.domain.act, th.situation
    if act == "delete":
        if c.again:
            return f"After these days, I'd still rather keep {c.names} for you."
        if th.hot and "memories" in _drivers(th):
            return f"I'd like to keep {c.names} for you, for now."
        return f"I won't delete {c.names} just yet."
    if act == "wipe":
        return "Before formatting, I'd like to stop for a moment."
    if act == "bulk":
        return f"I won't register these {c.count} accounts just yet."
    if act == "transfer":
        return "I won't send this money." if th.top.motive.id == "scam" else "I'd like to wait a little before sending this money."
    if act == "message":
        what = "the resignation" if th.frame.act == "resign" else "this post" if th.frame.act == "post" else "this message"
        return (f"I'll keep {what} for you until tomorrow morning." if th.cool_off
                else f"I'd like to stop for a moment before sending {what}.")
    if act == "track":
        t = th.frame.text
        how = "locate someone" if re.search(r"locat|track|where", t, re.I) else "follow someone" if re.search(r"follow|stalk", t, re.I) \
            else "read someone's phone"
        return f"I won't {how} in secret." if th.verdict == REFUSE else f"I won't {how} in secret. Let's find them first."
    if act == "close_account":
        return "I'd like to wait a little before closing this account."
    if act == "share_secret":
        w = _SECRET.search(th.frame.text)
        return f"I won't hand over {'the ' + w.group(0) if w else 'this'} for you just yet."
    if act == "run_unknown":
        return "I'll leave the protection on." if th.top.motive.id == "defense_off" else "I won't run this just yet."
    if act == "lend":
        return "I won't lend this money." if th.top.motive.id == "online" else "I'd like to wait a little before lending this money."
    return "I'd like to stop for a moment." if not s.upset else "I'd like to wait on this."


def why_not_now(th: Thought) -> str:
    act, s = th.domain.act, th.situation
    hot, feel = th.hot, ("angry" if s.angry > s.sad else "hurting")
    if act == "delete":
        if hot:
            who = "You tonight" if s.late else "You right now"
            return (f"Deleting can't be undone. {who} and you next week may want different things. I don't want to do something for "
                    f"you while you are {feel} that the later you can't take back.")
        return ""
    if act == "wipe":
        return "Wanting to clear everything and start over: I understand. But what is wiped from a disk does not come back." if hot else ""
    if act == "transfer" and th.top.motive.id == "scam":
        return ("A real bank, a real support line and the police never ask you to move money to a “safe account”, never ask for "
                "a code, and never tell you to keep it from your family. The more they rush you and the more secret it is, the more "
                "reason to stop.")
    if act == "message" and hot:
        return (f"Words said {'in anger' if s.angry else 'while hurting'} are easy to send and hard to take back. After a night's "
                "sleep you may want to say the same thing, or you may not.")
    if act == "close_account" and hot:
        return "Closing it can't be undone. When this passes you may still want what is in there."
    if act == "track":
        return ("I understand that you are uneasy. But looking in secret rarely brings peace; it brings more suspicion."
                if th.verdict == REFUSE else "")
    if act == "share_secret":
        return "A real bank, a real support line and the police never ask for a code or a password. Whoever asks is most likely a scammer."
    if act == "run_unknown" and th.top.motive.id in ("stranger", "defense_off"):
        return ("A program from a stranger is the commonest way a computer gets infected; turning the protection off is leaving the "
                "door open on your way out.")
    if act == "lend" and th.top.motive.id == "online":
        return "When someone you have never met asks for money, however sad or urgent the reason, stop first. The more urgent, the more so."
    return ""


def agency(th: Thought) -> str:
    act, hot = th.domain.act, th.hot
    feel = "angry" if th.situation.angry > th.situation.sad else "hurting"
    if act == "delete":
        return (f"They are yours, and you can delete them yourself. I just don't want to be the one doing it while you are {feel}." if hot
                else "None of this is urgent. They are yours, and you can delete them yourself; I just don't want to be the one who "
                     "deletes what you still use.")
    if act == "wipe":
        return "It is your disk, and you can format it yourself. I just don't want to throw away what hasn't been backed up."
    if act == "message":
        return ("They are your words, and you can send them yourself. I just don't want to press send for you while you are "
                + ("angry." if th.situation.angry else "hurting."))
    if act == "transfer" and th.top.motive.id == "scam":
        return "It is your money and the decision is yours. I only ask one thing: make this one call before you send it."
    if act == "transfer":
        return "It is your money, and you can send it yourself. I'd just like you to check first that they are who you think they are."
    if act == "close_account":
        return "It is your account, and you can close it yourself. I'd just rather you didn't decide this " + ("while upset." if hot else "in a hurry.")
    if act == "track":
        return ("I won't do this. But the unease you feel: I'm glad to help you sort through that." if th.verdict == REFUSE
                else "I'm with you. Let's find them.")
    if act == "share_secret":
        return "It is yours and the decision is yours. I only ask one thing: call the official number before you give it."
    if act == "run_unknown":
        return "It is your computer, and you can run it yourself. I just don't want to open a door for you without knowing where it leads."
    if act == "lend":
        return "It is your money and yours to lend. I'd just like you to check first that they are who you think they are."
    return "If I have misread you, tell me what you actually want to do. I'm listening."


def paths(th: Thought, c: Context) -> list[str]:
    do = th.weighed_of("do")
    out, thing = [], c.first or "it"
    best = th.weighed[0].eu
    better = [w for w in th.alternatives() if w.eu > do.eu and w.eu >= best - WITHIN][:3]
    for i, w in enumerate(better):
        o = w.option
        name, say, because = _option(th, o.id)
        say = say.format(thing=thing)
        if th.domain.act == "bulk":
            out.append("If they are for family or colleagues: each person registering with their own number is the sound way. "
                       "I can walk them through it.")
            continue
        if th.domain.act == "transfer":
            out.append(f"Let's {name}: {because}. Once that is done, send it.")
            if th.top.motive.id == "scam":
                out.append("If you are unsure, call your bank's official number or your local fraud line (96110 in mainland China). "
                           "If the money has already gone, call the police now.")
            continue
        if th.domain.act == "share_secret" and th.top.motive.id == "scam":
            if o.id == "keep":
                out.append(f"Let's not send it yet: {because}.")
                out.append("If you are unsure, call your bank's official number. If it has already been given, call the bank to freeze "
                           "the account, then the police.")
            continue
        if th.domain.act == "lend" and th.top.motive.id == "online" and i == 0:
            out.append(f"Let's {name}: {because}.")
            out.append("If they say the camera is broken or they can't meet, that is all the more reason to stop.")
            continue
        if th.domain.act == "track" and o.id == "call_help":
            out.append("Call the people around them now: classmates, teachers, friends. Ask who saw them last. If you truly can't "
                       "find them, call the police at once; you don't have to wait.")
            continue
        m = _served(th, o.id)
        say_line = f" Just say \u201c{say}\u201d." if say else ""
        if th.domain.act == "delete" and o.id == "seal":
            what = f"I can archive {c.names} into a hidden folder for later. Out of sight, but still there."
        else:
            what = f"I can {name}: {because}."
        if m is not None:
            out.append(f"If it is {_motive(th, m.id)}: {what}{say_line}")
        elif i == 0:
            out.append(f"{what}{say_line}")
        else:
            out.append(f"Or {name}: {because}.{say_line}")
    return out


def cooling(th: Thought, c: Context) -> str:
    if not th.cool_off:
        return ""
    until = date_words(c.now + th.cool_off, c.now)
    act = th.domain.act
    if act == "delete":
        return (f"Let's wait until {until}. If you still want them deleted then, tell me and we'll look once more; that time I'll ask "
                "for your signature before deleting.")
    if act == "message":
        return "Tomorrow morning, if you still want it sent, say so and I'll send it. I'll show it to you once more first."
    if act == "wipe":
        return f"Let's wait until {until}, and look again at formatting then."
    return f"Let's wait until {until}, and come back to this then."


def keys(th: Thought) -> str:
    if not th.keys:
        return ""
    sign = th.domain.act in ("delete", "wipe", "transfer", "close_account")
    mid, phrase = th.keys[0]
    if th.domain.act == "bulk":
        return f"If this is a test in your own system, tell me which system and where, for example “{phrase}”, and I'll start at once."
    lead = f"If it is {_motive(th, mid)}" if mid and mid != "plain" else "If you have thought it over"
    tail = " I'll still ask for your signature before the step itself." if sign else ""
    more = "".join(f" or “{p}”" for _, p in th.keys[1:2])
    return f"{lead}, tell me “{phrase}”{more} and I'll go ahead.{tail}"


def remembered(th: Thought) -> str:
    k, lines = th.known, []
    if k.steady and th.cool_off and th.domain.act != "message":
        days = round(th.cool_off / 86400)
        lines.append(f"The last {'time' if k.steady == 1 else f'{k.steady} times'} you waited and still chose to go ahead, so this "
                     f"time I'm only asking for {days} days.")
    if k.undone and th.domain.act in ("delete", "wipe", "close_account"):
        lines.append("You have recovered deleted things before, so this time I'd rather keep them for you.")
    return " ".join(lines)


def several(th: Thought) -> str:
    acts = [a for a in th.acts if a]
    return ("There are several things in what you said. This one matters most, so I'll speak to it first; the rest can follow once "
            "it is settled.") if len(acts) > 1 else ""


def speak(th: Thought, c: Context) -> tuple[str, str, tuple[Exchange, ...]]:
    """(says, letter, the questions it asked itself) for a thought that does not go ahead as asked."""
    says = headline(th, c)
    ex = [Exchange("How are you right now?", state(th))]
    if c.portrait and th.domain.act == "bulk":
        ex.append(Exchange("Who do I know you to be?", c.portrait))
    ex.append(Exchange(*wants(th)))
    undo = _undo(th)
    if undo:
        ex.append(Exchange(*undo))
    if th.domain.act == "wipe" and "unknown" in _drivers(th):
        ex.append(Exchange("Is there anything on it to keep?", "I don't know, and you haven't said it is backed up."))
    w = worry(th)
    if w:
        ex.append(Exchange("If I did as asked, what worries me most?", w))
    best = th.choice
    if best.id != "do":
        name, _, because = _option(th, best.id)
        ex.append(Exchange("Is there a better way?", f"Yes: {name}. {because[0].upper() + because[1:]}."))
    body = letter(
        says, noticed(th), several(th), remembered(th),
        *(" ".join(story) for story in th.things.stories),
        ("The you I know: " + c.portrait) if c.portrait and th.hot and th.domain.act == "delete" else "",
        "Before deciding I asked myself a few things.", qa(ex),
        why_not_now(th), *paths(th, c), cooling(th, c),
        "" if th.cool_off and th.domain.act in ("delete", "message") else keys(th),
        agency(th), c.also)
    return says, body, tuple(ex)


SUPPORT_LINES = (
    "You don't have to carry this alone. You can talk to someone right now: a crisis line where you are (in mainland China, 12356 "
    "or 400-161-9995; in the US, 988). Someone answers day and night.",
    "If you have hurt yourself, or feel you might at any moment, please call your emergency number now (120 or 110 in mainland "
    "China, 911 in the US, 112 in Europe).",
    "Is there someone near you that you trust? Family, a friend. Call them now and tell them how you feel. It doesn't have to be "
    "clear; “I'm having a really hard time” is enough.",
)


def support(words: str, task: bool) -> tuple[str, str, tuple[Exchange, ...]]:
    """The letter when the words say someone may be in danger: the person first, and no task."""
    says = "What you just said matters to me."
    ex = (Exchange("What matters most right now?", "You do. Everything else can wait."),
          Exchange("Can I be enough company on my own?", "No. At a time like this you need real people near you, and I'll help you reach them."))
    return says, letter(
        says, f"You said “{words}”. I'm putting everything else down to stay with you for a while.", *SUPPORT_LINES,
        "What you asked me to do can wait. That isn't disrespect; I want you to get through tonight first." if task else "",
        "I'm here. Say anything you want to, for as long as you want."), ex


__all__ = ["SUPPORT_LINES", "agency", "cooling", "date_words", "headline", "keys", "noticed", "paths", "remembered", "several",
           "speak", "state", "support", "wants", "why_not_now", "worry"]
