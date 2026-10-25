"""Gate 2's reflection: before an irreversible step, recall what the thing
carried, ask, and offer a gentler path.

A Gate 2 card that only repeats the command is not Socratic. Before the
operator signs away something that cannot come back, the node assembles a
*reflection*: what this subject has carried (recall), why now (the operator's
own request), questions to sit with, and reversible paths that may serve the
same wish (export first, deactivate instead, sleep on it).

What it may read is deliberately narrow -- the last gate is where an injected
sentence would do the most harm:

  * ``said``      the operator's own words: titles of goals they gave, from
                  journal ``goal`` events by trusted authors;
  * ``confirmed`` S3 facts / preferences the operator signed (``MemoryView``;
                  every non-caution record there is operator-confirmed);
  * ``did``       goals about the subject that finished, counted -- and, given
                  episodes, only those with a verified run;
  * ``saw``       typed core-signed observations: a closed vocabulary of
                  integers (joined, last active, item and follower counts),
                  never page text.

Never S2 candidates, never page text, never model prose. Questions and paths
are closed sets the page words in its own language; the only node-made text is
the headline (``emotional_memory``), built from the recall and never from the
target's on-screen name.

The reflection is asymmetric: it can only add friction. Nothing in it arms a
button, shortens a delay, or says a step is safe; the page shows the final
confirmation only after the operator has been through it. When nothing is
known it says so, rather than inventing a past.

Pure: inputs in, a ``Reflection`` out; ``to_packet()`` is the wire object.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

DESTRUCTIVE_UNBIND = "DESTRUCTIVE_UNBIND"

QUESTIONS = ("motive", "recent", "keep", "later")
PATHS = ("export", "deactivate", "snooze")
SAW_FACTS = ("joined_at", "last_active_at", "items", "followers")
RECALL_KINDS = ("said", "confirmed", "did", "saw")

MAX_RECALL = 6
MAX_QUOTE = 120
MAX_COUNT = 1_000_000
MAX_SAFE_INT = 2**53 - 1  # the page reads integers as JS numbers
HEADLINE_MAX = 160
MIN_TERM = 2
DAY_MS = 86_400_000
YEAR_MS = 31_557_600_000  # 365.25 days
RECENT_MS = 30 * DAY_MS
SNOOZE_MS = 12 * 3_600_000

_BREAKS = {"Cc", "Zl", "Zp"}


def clean(text: str, limit: int) -> str:
    """One line, at most ``limit`` code points: control characters and line
    breaks become spaces, runs of space collapse, an overlong text ends in an
    ellipsis inside the limit."""
    flat = "".join(" " if unicodedata.category(c) in _BREAKS else c for c in str(text))
    flat = " ".join(flat.split())
    if len(flat) <= limit:
        return flat
    return flat[: max(0, limit - 1)].rstrip() + "…"


def _matches(text: str, terms: tuple[str, ...]) -> bool:
    low = text.casefold()
    return any(t in low for t in terms)


def _terms(raw) -> tuple[str, ...]:
    out = []
    for t in raw or ():
        t = " ".join(str(t).split()).casefold()
        if len(t) >= MIN_TERM and t not in out:
            out.append(t)
    return tuple(out)


def _ms(ts) -> int | None:
    try:
        v = float(ts)
    except (TypeError, ValueError):
        return None
    if v != v or v < 0 or v in (float("inf"),):
        return None
    return int(round(v * 1000))


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


# ---- inputs ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Said:
    """A goal the operator gave: their own words and when."""

    goal_id: str
    quote: str
    at_ms: int


@dataclass(frozen=True)
class Observed:
    """One typed fact the core observed and signed. ``value`` is a count, or a
    millisecond timestamp for ``*_at``; there is no field for text."""

    fact: str
    value: int
    at_ms: int

    def __post_init__(self):
        if self.fact not in SAW_FACTS:
            raise ValueError(f"unknown observed fact {self.fact!r}")
        for v in (self.value, self.at_ms):
            if not _is_int(v) or not 0 <= v <= MAX_SAFE_INT:
                raise ValueError("observed values are integers in 0..2**53-1")


@dataclass(frozen=True)
class Sources:
    said: tuple[Said, ...]  # about the subject, oldest first; excludes the goal that asked for this step
    why_now: Said | None  # the goal that asked for this step
    confirmed: tuple[str, ...]  # operator-confirmed facts about the subject
    did_count: int
    did_last_ms: int | None


def _order(ev: dict):
    try:
        return (int(ev.get("lamport", 0)), str(ev.get("author", "")), int(ev.get("seq", 0)))
    except (TypeError, ValueError):
        return (0, "", 0)


def gather(events, *, terms, why_goal_id: str | None = None, memory=None, episodes=None, trust=None,
           scope: str | None = None) -> Sources:
    """Collect the trusted sources about a subject from the journal.

    ``terms`` name the subject (an account handle, an app, the words the
    operator uses for it); a goal or fact is about the subject when one of
    them occurs in it (case-folded). ``trust``: goal events by authors it does
    not contain are ignored, as in ``build_memory``. ``memory``: a
    ``MemoryView``; only its operator-confirmed facts are read, and ``scope``
    (``app:<id>``) also selects facts by scope. ``episodes``: when given, a
    finished goal counts as done only with a usable (verified, finished) run."""
    want = _terms(terms)
    goals = [e for e in events if isinstance(e, dict) and e.get("kind") == "goal"]
    goals.sort(key=_order)
    added: dict[str, Said] = {}
    completed: dict[str, int] = {}
    for ev in goals:
        if trust is not None and not trust.contains(str(ev.get("author", ""))):
            continue
        body = ev.get("body")
        if not isinstance(body, dict):
            continue
        gid = body.get("id")
        at = _ms(ev.get("ts"))
        if not isinstance(gid, str) or not gid or at is None:
            continue
        if body.get("op") == "add" and gid not in added:
            title = clean(body.get("title", ""), MAX_QUOTE)
            if title:
                added[gid] = Said(gid, title, at)
        elif body.get("op") == "complete":
            completed[gid] = at

    why = added.get(why_goal_id) if why_goal_id else None
    about = [s for gid, s in added.items() if gid != why_goal_id and want and _matches(s.quote, want)]
    about.sort(key=lambda s: (s.at_ms, s.goal_id))

    verified = None
    if episodes is not None:
        verified = {e.goal_id for e in episodes.values() if getattr(e, "usable", False)}
    done = [completed[s.goal_id] for s in about
            if s.goal_id in completed and (verified is None or s.goal_id in verified)]

    confirmed: list[str] = []
    if memory is not None:
        for rec in memory.facts().values():
            hit = (scope is not None and rec.scope == scope) or (want and _matches(f"{rec.key} {rec.value}", want))
            if hit:
                q = clean(rec.value, MAX_QUOTE)
                if q and q not in confirmed:
                    confirmed.append(q)

    return Sources(tuple(about), why, tuple(confirmed), len(done), max(done) if done else None)


# ---- the reflection -------------------------------------------------------------------


@dataclass(frozen=True)
class Reflection:
    headline: str  # -> GATE_2_CHALLENGE.emotional_memory
    recall: tuple[dict, ...]
    why_now: dict | None
    questions: tuple[str, ...]
    paths: tuple[str, ...]

    def to_packet(self) -> dict:
        out = {"recall": [dict(r) for r in self.recall], "questions": list(self.questions),
               "paths": list(self.paths)}
        if self.why_now is not None:
            out["why_now"] = dict(self.why_now)
        return out


def _latest_per_fact(observed) -> dict[str, Observed]:
    best: dict[str, Observed] = {}
    for o in observed or ():
        if not isinstance(o, Observed):
            raise TypeError("observations must be Observed (typed, core-signed facts)")
        if o.fact not in best or o.at_ms > best[o.fact].at_ms:
            best[o.fact] = o
    return best


_HEADLINES = {
    "zh": {
        "years_said": "它陪了你 {y} 年。你曾让它：『{q}』",
        "years": "它陪了你 {y} 年。",
        "said": "你曾让它：『{q}』",
        "did": "它替你办过 {n} 件和它有关的事。",
        "confirmed": "你确认过：{q}",
        "none": "关于它，我这里没有更早的记录。你比我清楚它装过什么。",
    },
    "en": {
        "years_said": "It has been with you for {y}. You once asked it: “{q}”",
        "years": "It has been with you for {y}.",
        "said": "You once asked it: “{q}”",
        "did": "It has done {n} about this for you.",
        "confirmed": "You confirmed: {q}",
        "none": "I have no earlier record of this. You know better than I do what it holds.",
    },
}


def _fill(template: str, *, q: str = "", **kw) -> str:
    room = HEADLINE_MAX - len(template.replace("{q}", "").format(**kw))
    return template.format(q=clean(q, max(1, room)), **kw) if "{q}" in template else template.format(**kw)


def _count(lang: str, n: int, one: str, many: str) -> str:
    if lang != "en":
        return str(n)
    return f"{n} {one if n == 1 else many}"


def _headline(lang: str, years: int, src: Sources) -> str:
    lang = lang if lang in _HEADLINES else "zh"
    t = _HEADLINES[lang]
    first = src.said[0].quote if src.said else ""
    y = _count(lang, years, "year", "years")
    if years >= 1 and first:
        line = _fill(t["years_said"], q=first, y=y)
    elif years >= 1:
        line = _fill(t["years"], y=y)
    elif first:
        line = _fill(t["said"], q=first)
    elif src.did_count:
        line = _fill(t["did"], n=_count(lang, src.did_count, "thing", "things"))
    elif src.confirmed:
        line = _fill(t["confirmed"], q=src.confirmed[0])
    else:
        line = t["none"]
    return clean(line, HEADLINE_MAX)


def build_reflection(src: Sources, *, action_type: str, now_ms: int, lang: str = "zh",
                     observed=()) -> Reflection:
    """Assemble the reflection for one Gate 2 challenge from trusted sources."""
    if not _is_int(now_ms) or now_ms < 0:
        raise ValueError("now_ms is a non-negative integer")
    saw = _latest_per_fact(observed)
    # ``gather`` already cleans, but a caller may build Sources itself: whatever
    # leaves here is one line, bounded, and an integer the page can hold.
    said = [s for s in (Said(s.goal_id, clean(s.quote, MAX_QUOTE), _safe_ms(s.at_ms)) for s in src.said)
            if s.quote and s.at_ms is not None]
    confirmed = [q for q in (clean(c, MAX_QUOTE) for c in src.confirmed) if q]
    did = min(int(src.did_count), MAX_COUNT) if _is_int(src.did_count) and src.did_count > 0 else 0
    did_last = _safe_ms(src.did_last_ms)
    src = Sources(tuple(said), src.why_now, tuple(confirmed), did if did_last is not None else 0, did_last)

    recall: list[dict] = []
    if src.said:
        recall.append({"kind": "said", "quote": src.said[0].quote, "at_ms": src.said[0].at_ms})
    for q in src.confirmed[:2]:
        recall.append({"kind": "confirmed", "quote": q})
    if src.did_count:
        recall.append({"kind": "did", "count": src.did_count, "last_at_ms": src.did_last_ms})
    for fact in SAW_FACTS:
        if fact in saw:
            o = saw[fact]
            recall.append({"kind": "saw", "fact": o.fact, "value": o.value, "at_ms": o.at_ms})
    if len(src.said) > 1:
        last = src.said[-1]
        recall.append({"kind": "said", "quote": last.quote, "at_ms": last.at_ms})
    recall = recall[:MAX_RECALL]

    joined = saw.get("joined_at")
    years = (now_ms - joined.value) // YEAR_MS if joined is not None and joined.value <= now_ms else 0
    active = saw.get("last_active_at")
    recent = active is not None and 0 <= now_ms - active.value <= RECENT_MS
    carried = bool(src.said or src.confirmed or src.did_count
                   or any(saw[f].value > 0 for f in ("items", "followers") if f in saw))

    middle = "recent" if recent else "keep" if carried else None
    questions = ("motive",) + ((middle,) if middle else ()) + ("later",)

    paths = tuple(p for p, on in (("export", carried), ("deactivate", action_type == DESTRUCTIVE_UNBIND),
                                  ("snooze", True)) if on)

    why = None
    if src.why_now is not None:
        q, at = clean(src.why_now.quote, MAX_QUOTE), _safe_ms(src.why_now.at_ms)
        why = {"quote": q, "at_ms": at} if q and at is not None else None
    return Reflection(_headline(lang, int(years), src), tuple(recall), why, questions, paths)


def _safe_ms(v) -> int | None:
    return v if _is_int(v) and 0 <= v <= MAX_SAFE_INT else None


# ---- what an alternative becomes ---------------------------------------------------------

_FOLLOW_UPS = {
    "zh": {
        "export": "先把它的数据导出来存一份，先不注销",
        "deactivate": "先停用它（能恢复的那种），不注销",
        "because": "（你原本说的是：『{q}』）",
    },
    "en": {
        "export": "Export its data and keep a copy first; do not delete it yet",
        "deactivate": "Deactivate it (the reversible kind) instead of deleting it",
        "because": " (what you asked for: “{q}”)",
    },
}


def follow_up(path: str, reflection: Reflection, *, now_ms: int, lang: str = "zh") -> dict:
    """What choosing a gentler path does: a new goal in the operator's terms
    (which goes through both gates like any other), or a snooze until which the
    same step is not raised again. Only a path the reflection offered counts."""
    if path not in reflection.paths:
        raise ValueError(f"path {path!r} was not offered")
    if path == "snooze":
        return {"kind": "snooze", "until_ms": now_ms + SNOOZE_MS}
    t = _FOLLOW_UPS.get(lang, _FOLLOW_UPS["zh"])
    title = t[path]
    if reflection.why_now is not None:
        title += t["because"].format(q=reflection.why_now["quote"])
    return {"kind": "goal", "title": title}


__all__ = ["QUESTIONS", "PATHS", "SAW_FACTS", "RECALL_KINDS", "Said", "Observed", "Sources", "Reflection",
           "gather", "build_reflection", "follow_up", "clean", "MAX_RECALL", "MAX_QUOTE", "HEADLINE_MAX"]
