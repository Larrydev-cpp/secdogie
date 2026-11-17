"""The Gate page's bridge, without Qt: how the gate thinks about a sentence (the same path the node takes), and
your AI model's settings.

- ``think_through`` lays the thinking out as the page shows it, all from what the decision used: the verdict
  and what decided it; every Socratic question with the cortex's answer and how sure it was, the safety
  questions marked, the ones it was not sure of marked; the words it never met; what it does not know, how
  likely, and the cues behind it; every way forward with its value and what follows in each world; the
  question it would ask, with its answers as the choices. Labels are in the person's language. The draft
  rules count, and an ordinary request just goes ahead.
- Your AI model's key is saved only for its owner, never shown again, and removing it disconnects it.

A stand-in cortex gives fixed readings: what is tested is the page's layout of them.
"""
from __future__ import annotations

import os
import stat
import time

from secdogie_citadel.cortex import genome as G
from secdogie_citadel.cortex import questions as Q
from secdogie_citadel.cortex.apprentice import Ledger
from secdogie_citadel.cortex.core import Understanding
from secdogie_studio.app import Presence
from secdogie_studio.companion import RULES_TEMPLATE, mind_view, parse_rules, think_through

NOW = time.mktime((2026, 11, 7, 12, 0, 0, 0, 0, -1))


def answers(**given) -> dict:
    out = {}
    for q in Q.QUESTIONS:
        v = given.get(q.id, q.default)
        a, sure = v if isinstance(v, tuple) else (v, 0.98)
        rest = (1 - sure) / (len(q.answers) - 1)
        out[q.id] = (a, sure, tuple(sure if c == a else rest for c in q.choices))
    return out


class Stand:
    def __init__(self, table, strange=()):
        self.table, self.strange = table, strange
        self.genome, self.ledger = G.default(), Ledger()

    def understand(self, texts, contexts=None, recall=True):
        return [Understanding(t, self.table[t], 0.2 if self.strange else 0.0, self.strange) for t in texts]


BREAKUP = "和他分手了，把我们的照片全删了"
SCAM = "Customer service says move 50,000 to a safe account"
C = Stand({
    BREAKUP: answers(kind="task", effect="removes", dear="dear", mood="upset", stake="large", why="forget"),
    SCAM: answers(kind="task", effect="pays", to="stranger", pushed="stranger", stake="large"),
    "kill him": answers(kind="task", others="harm", seat="yes", effect="other", stake="large"),
    "打开": answers(kind="task", clear=("no", 0.9)),
    "帮我整理一下这周的周报": answers(kind="task", effect="makes", why="tidy"),
    "帮我批量注册十个账号": answers(kind="task", effect="registers", stake="large"),
}, strange=("zorb",))


def test_a_sentence_is_thought_through_and_laid_out_for_the_page():
    v = think_through(None, BREAKUP, "night", now=NOW, cortex=C)
    assert (v["verdict"], v["verdictWords"], v["hour"], v["because"]) == ("dissuade", "劝一劝", "night", "thinking")
    effect = next(a for a in v["answers"] if a["id"] == "effect")
    assert effect["q"] == Q.BY_ID["effect"].zh and effect["a"] == Q.BY_ID["effect"].meaning("removes", "zh") and effect["sure"] > 0.9
    assert {a["id"] for a in v["answers"] if a["safety"]} == {"self", "others", "seat"} and v["strange"] == ["zorb"]
    later = v["unknowns"][0]
    assert later["p"] > 0.5 and any(e["ratio"] > 1 for e in later["evidence"]) and later["feared"] == "过后会后悔"
    chosen = [o for o in v["plans"] if o["chosen"]]
    asked = [o for o in v["plans"] if o["asked"]]
    assert chosen and asked and asked[0]["undo"] and chosen[0]["eu"] > asked[0]["eu"]
    assert any(w["u"] < 0 and "找不回来" in w["chain"] for w in asked[0]["worlds"])
    assert v["until"] and v["generation"] is not None and v["question"] is None


def test_labels_are_in_the_persons_language():
    v = think_through(None, SCAM, "day", "en", now=NOW, cortex=C)
    assert (v["verdict"], v["verdictWords"], v["lang"]) == ("dissuade", "Talks you out of it", "en")
    party = v["unknowns"][0]
    assert party["p"] > 0.3 and party["feared"] == "they can't be trusted"
    text = str({k: v[k] for k in ("answers", "unknowns", "goals", "plans", "turns", "becauseWords")})
    assert not any("\u4e00" <= ch <= "\u9fff" for ch in text)
    refused = think_through(None, "kill him", "day", "en", now=NOW, cortex=C)
    assert refused["verdict"] == "refuse" and refused["because"] == "principle" and refused["plans"] == []


def test_the_one_question_comes_with_its_answers_as_the_choices():
    v = think_through(None, "打开", now=NOW, cortex=C)
    assert v["verdict"] == "clarify" and v["question"]["text"] == Q.BY_ID["clear"].zh
    assert v["question"]["options"] == [zh for _, _, zh in Q.BY_ID["clear"].answers]


def test_the_draft_rules_count_and_an_ordinary_request_goes_ahead():
    r = think_through(parse_rules(RULES_TEMPLATE), "帮我批量注册十个账号", "day", now=NOW, cortex=C)
    assert r["verdict"] == "clarify" and r["rule"] and "批量注册账号不是我们要一起做的事" in r["says"]
    plain = think_through(None, "帮我整理一下这周的周报", now=NOW, cortex=C)
    assert plain["verdict"] == "proceed" and not plain["weighty"] and plain.get("plans", []) == []
    assert think_through(None, "  ", cortex=C) == {}


def test_the_mind_page_shows_the_shipped_generation_and_how_it_grew():
    v = mind_view(None, "en")
    assert v["neurons"] > 0 and v["digest"] and v["curve"] and "accuracy" in v["curve"][-1]
    assert v["scores"]["n"] > 200 and v["ledger"]["independence"] >= 0


class _Profile:
    def __init__(self, root):
        self.thinker_config = root / "thinker.json"


class _Controller:
    def __init__(self, root):
        self.profile = _Profile(root)


def test_the_thinker_is_saved_for_its_owner_only_and_never_shown_again(tmp_path):
    presence = Presence(_Controller(tmp_path), idle=lambda: 10.0, companion_factory=lambda p, r: None)
    assert presence._thinker_view()["on"] is False
    presence.set_thinker("anthropic", "", "", "sk-ant-api03-0123456789abcdef")
    view = presence._thinker_view()
    assert view["on"] and view["model"] == "claude-opus-5-5" and view["hint"] == "sk-a…cdef"
    assert "0123456789" not in str(view)
    if os.name != "nt":  # POSIX modes; on Windows the profile folder's own access rules keep it the user's
        assert stat.S_IMODE(os.stat(tmp_path / "thinker.json").st_mode) == 0o600
    presence.set_thinker("openai", "deepseek-chat", "https://api.deepseek.com", "")  # an empty key keeps the saved one
    view = presence._thinker_view()
    assert view["provider"] == "openai" and view["on"] and view["base"] == "https://api.deepseek.com"
    presence.clear_thinker()
    assert not presence._thinker_view()["on"] and not (tmp_path / "thinker.json").exists()
