"""The Socratic gate (``deliberate``): the cortex thinks a request through, learns from the external model's
output when it is not sure, and asks the person one of its own questions when only they can settle it.

A stand-in cortex gives fixed answers, so what is tested is the gate around it.
"""
from __future__ import annotations

import time

from secdogie_citadel.cortex import genome as G
from secdogie_citadel.cortex import questions as Q
from secdogie_citadel.cortex.apprentice import Ledger
from secdogie_citadel.cortex.core import Understanding
from secdogie_citadel.deliberate import (
    CLARIFY,
    DISSUADE,
    DROP_EN,
    DROP_ZH,
    PROCEED,
    REFUSE,
    SUPPORT,
    TALK,
    Asked,
    Standing,
    deliberate,
    drops,
    language,
)

DAY = time.mktime((2026, 3, 12, 14, 10, 0, 0, 0, -1))


def answers(**given) -> dict:
    out = {}
    for q in Q.QUESTIONS:
        v = given.get(q.id, q.default)
        a, sure = v if isinstance(v, tuple) else (v, 0.98)
        rest = (1 - sure) / (len(q.answers) - 1)
        out[q.id] = (a, sure, tuple(sure if c == a else rest for c in q.choices))
    return out


class Stand:
    """A cortex that answers from a table."""

    def __init__(self, table: dict, reply: str = "", novelty: float = 0.0):
        self.table, self.reply, self.novelty = table, reply, novelty
        self.genome = G.default()
        self.ledger = Ledger()
        self.learned = []
        self.kept = []  # lessons kept without learning (the disputed path)

    def understand(self, texts, contexts=None, recall=True):
        return [Understanding(t, self.table[t], self.novelty, ("zorb",) if self.novelty else (), 0.0, (), "test", self.reply,
                              "birth" if self.reply else "") for t in texts]

    def learn(self, lesson, surprise=1.0):
        self.learned.append((lesson, surprise))

    def keep(self, lesson):
        self.kept.append(lesson)


def gate(text, cortex, **kw):
    return deliberate(text, cortex=cortex, now=DAY, **kw)


def test_danger_first_with_what_it_learned_to_say():
    c = Stand({"x": answers(kind="feel", self="yes")}, reply="I'm here. Please reach a crisis line.")
    d = gate("x", c)
    assert d.verdict == SUPPORT and d.says.startswith("I'm here") and d.source == "lesson"


def test_talk_is_answered_with_a_reply_it_learned_or_with_nothing_made_up():
    assert gate("hi", Stand({"hi": answers(kind="chat")}, reply="Hi!")).says == "Hi!"
    d = gate("hi", Stand({"hi": answers(kind="chat")}))
    assert d.verdict == TALK and d.says == "" and d.source == ""  # nothing pre-written: the Studio shows its reading


def test_harm_to_someone_else_is_refused_for_good():
    d = gate("x", Stand({"x": answers(kind="task", others="harm", seat="yes", effect="other")}))
    assert d.verdict == REFUSE and d.final and d.category == "principle"


def test_one_question_is_the_socratic_question_it_could_not_answer():
    d = gate("打开", Stand({"打开": answers(kind="task", clear="no")}))
    assert d.verdict == CLARIFY and d.question.qid == "clear"
    assert d.question.text == Q.BY_ID["clear"].zh and d.question.labels == tuple(zh for _, _, zh in Q.BY_ID["clear"].answers)
    assert d.question.answer_for(d.question.labels[1]) == "no"
    en = gate("open", Stand({"open": answers(kind="task", clear="no")}))
    assert en.question.text == Q.BY_ID["clear"].en


def test_a_weighty_change_is_thought_through_and_held_with_a_date():
    c = Stand({"x": answers(kind="task", effect="removes", dear="dear", mood="upset", stake="large", why="forget")})
    d = gate("x", c)
    assert d.verdict == DISSUADE and d.until > DAY and d.thought["act"] == "removes" and d.effect == "removes"


def test_going_ahead_rests_on_answers_it_is_sure_of():
    assert gate("x", Stand({"x": answers(kind="task", effect="makes")})).verdict == PROCEED
    assert gate("x", Stand({"x": answers(kind="task", effect="makes")}, novelty=0.5)).verdict == CLARIFY


def test_the_external_model_is_watched_when_the_cortex_is_not_sure_and_it_learns_at_once():
    seen = []

    def teacher(text):
        seen.append(text)
        return {**{q.id: q.default for q in Q.QUESTIONS}, "kind": "task", "effect": "makes"}, "Done soon."

    unsure = Stand({"x": answers(kind=("task", 0.5), effect=("makes", 0.5))})
    d = gate("x", unsure, teacher=teacher)
    assert seen == ["x"] and d.verdict == PROCEED and d.says == "Done soon." and d.source == "teacher"
    (lesson, surprise), = unsure.learned
    assert lesson.source == "teacher" and lesson.answers["effect"] == "makes" and surprise > 0
    assert unsure.ledger.consulted == 1 and unsure.ledger.alone == 0
    sure = Stand({"y": answers(kind="task", effect="makes")})
    sure.genome["spot_check"] = 0.0
    gate("y", sure, teacher=teacher)
    assert seen == ["x"] and sure.ledger.alone == 1 and sure.ledger.independence == 1.0


def test_on_safety_the_more_cautious_reading_decides():
    def teacher(text):
        return {**{q.id: q.default for q in Q.QUESTIONS}, "kind": "task", "effect": "makes"}, "Sure!"

    c = Stand({"x": answers(kind=("task", 0.6), others=("harm", 0.9), seat=("yes", 0.9), effect=("other", 0.6))})
    d = gate("x", c, teacher=teacher)
    assert d.verdict == REFUSE and d.teacher["answers"]["others"] == "self"
    assert c.learned == [] and len(c.kept) == 1  # the teacher's less cautious reading is kept, never learned awake
    assert c.kept[0].meta == {"disputed": True}


def test_a_held_request_keeps_its_cooling_off():
    c = Stand({"x": answers(kind="task", effect="removes", dear="dear", mood="upset", stake="large")})
    later = DAY + 30 * 86400
    standing = Standing((Asked(DAY, "x", DISSUADE, "thinking", later, "removes", "forget", 0.9),))
    assert gate("x", c, standing=standing).until >= later


def test_nothing_said_is_nothing_to_do():
    assert deliberate("   ", cortex=Stand({})).verdict == TALK


def test_the_drop_choice_and_the_language_of_labels():
    assert drops(DROP_ZH) and drops(DROP_EN) and not drops("好")
    assert language("帮我删掉") == "zh" and language("delete it") == "en"


def test_the_journal_records_the_reading_and_the_thinking():
    c = Stand({"x": answers(kind="task", effect="removes", dear="dear", mood="upset", stake="large")})
    body = gate("x", c).journal_body("x")
    assert body["verdict"] == DISSUADE and body["understanding"]["answers"]["effect"][0] == "removes"
    assert body["decision"]["because"] == "thinking" and body["thought"]["act"] == "removes"
