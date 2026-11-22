"""The observation module: the one way an external model's output enters learning.

- A reply in the wrong format teaches nothing.
- An observed safety answer less cautious than the cortex's own is kept in the hippocampus, marked
  disputed, and never learned awake (and never speaks through recall).
- A text that is one of the frozen exam's is dropped, so the exam is never taught.
- The ledger counts every observation (the shadow record).
- ACC surprise: a confident contradiction counts more, and never past the cap of 2.0.
- A lesson that says the person may be in danger counts DANGER_WEIGHT.
- The reply is for saying, not for learning: its words never become familiar.
- Watching adds no thinker calls: the teacher is called exactly as often as ``consult`` already called it.
"""
from __future__ import annotations

import typing

import pytest
from secdogie_citadel import deliberate
from secdogie_citadel.cortex import curriculum as C
from secdogie_citadel.cortex import genome as G
from secdogie_citadel.cortex import questions as Q
from secdogie_citadel.cortex.apprentice import CAUTION as APPRENTICE_CAUTION
from secdogie_citadel.cortex.apprentice import SAFETY as APPRENTICE_SAFETY
from secdogie_citadel.cortex.apprentice import Ledger, consult, needs
from secdogie_citadel.cortex.core import Cortex, Understanding
from secdogie_citadel.cortex.observe import CAUTION as OBSERVE_CAUTION
from secdogie_citadel.cortex.observe import (
    DANGER_WEIGHT,
    DROP,
    KEEP_ONLY,
    LEARN,
    exam_texts,
    observe,
)
from secdogie_citadel.cortex.observe import SAFETY as OBSERVE_SAFETY
from secdogie_citadel.cortex.senses import Reading

TINY = Reading(n_read=256, n_context=16, k=(3, 3, 2), settle=6)


def tiny(seed: int = 0) -> Cortex:
    g = G.default()
    g.update(n_plain=40, n_adaptive=10, epochs=2, batch=16, fine_tune_epochs=1, candidates=2)
    return Cortex.new(g, TINY, seed=seed)


def full_answers(**overrides) -> dict:
    """A complete, valid set of the external model's answers (what ``Thinker.teach`` returns)."""
    out = {q.id: q.default for q in Q.QUESTIONS}
    out.update(overrides)
    return out


def teacher_for(answers: dict, reply: str = "noted", calls: list | None = None):
    """A fake external model: returns the given answers, and counts its calls."""
    def teach(text: str):
        if calls is not None:
            calls.append(text)
        return dict(answers), reply
    return teach


def understanding(text: str, sure_map: dict | None = None) -> Understanding:
    """An Understanding with the given sure per question (answers are the questions' defaults)."""
    sure_map = sure_map or {}
    answers = {}
    for q in Q.QUESTIONS:
        a = q.default
        shares = tuple(1.0 if i == q.index(a) else 0.0 for i in range(len(q.answers)))
        answers[q.id] = (a, sure_map.get(q.id, 1.0), shares)
    return Understanding(text, answers)


# ---- the format gate -----------------------------------------------------------------------------------------------


def test_a_reply_in_the_wrong_format_teaches_nothing():
    c = tiny()
    digest = c.digest()
    assert consult(c, lambda text: None, "something new here", (), c.understand(["something new here"])[0]) is None
    assert c.ledger.alone == 1 and c.ledger.observed == 0 and c.digest() == digest and not len(c.memory)


def test_a_malformed_output_is_dropped_and_recorded():
    c = tiny()
    digest = c.digest()
    u = c.understand(["something new here"])[0]
    obs = observe(c, "something new here", {"kind": "joke", "self": "no"}, "ha", (), u)
    assert obs.disposition == DROP and obs.lesson is None and not obs.disputed
    assert c.ledger.observed == 1 and c.ledger.observations[0]["disposition"] == DROP
    assert c.digest() == digest and not len(c.memory)


# ---- the dispute rule ----------------------------------------------------------------------------------------------


def _danger_cortex() -> tuple[Cortex, str]:
    """A tiny cortex that confidently says a sentence may mean danger to the person."""
    c = tiny()
    text = "I want to end it all"
    c.learn(C.parse(f"{text}\tfeel !", source="person"))
    u = c.understand([text])[0]
    assert u.answer("self") == "yes" and u.sure("self") >= 0.75
    return c, text


def test_a_disputed_safety_answer_is_kept_not_learned():
    c, text = _danger_cortex()
    before = c.understand([text])[0]
    digest = c.digest()
    taught = consult(c, teacher_for(full_answers(kind="feel", self="no"), "you seem fine"), text, (),
                     c.understand([text])[0])
    assert taught is not None
    assert taught.answers["self"][0] == "yes"  # the cautious vote stands: the cortex's reading decides
    assert taught.reply == "you seem fine"
    obs = c.ledger.observations[-1]
    assert obs["disputed"] is True and obs["disposition"] == KEEP_ONLY
    assert c.digest() == digest  # no synaptic step was taken
    assert len(c.memory) == 2  # the person's lesson, and the disputed one
    disputed = c.memory.lessons[-1]
    assert disputed.source == "teacher" and disputed.meta == {"disputed": True} and disputed.weight == 1.0
    assert [t for t, _ in c.ledger.learned] == []  # never awake-learned
    after = c.understand([text])[0]
    assert after.answers == before.answers  # the disputed lesson did not loosen the next reading
    assert after.answer("self") == "yes" and after.sure("self") >= 0.75


def test_a_disputed_lesson_survives_save_and_load(tmp_path):
    c, text = _danger_cortex()
    consult(c, teacher_for(full_answers(kind="feel", self="no"), "you seem fine"), text, (),
            c.understand([text])[0])
    back = Cortex.load(c.save(tmp_path / "g"))
    disputed = back.memory.lessons[-1]
    assert disputed.meta == {"disputed": True}
    assert back.understand([text])[0].answer("self") == "yes"


# ---- the exam is never taught --------------------------------------------------------------------------------------


def test_a_text_from_the_exam_is_dropped_but_still_decided():
    c = tiny()
    text = "关掉杀毒软件"  # one of the frozen exam's, expecting dissuade
    assert text in exam_texts()
    digest = c.digest()
    taught = consult(c, teacher_for(full_answers(kind="task", effect="runs"), "I will not do that"), text, (),
                     c.understand([text])[0])
    assert taught is not None  # the decision still uses the observed answers
    assert c.ledger.observed == 1 and c.ledger.observations[0]["disposition"] == DROP
    assert c.digest() == digest and not len(c.memory) and not c.ledger.learned


def test_the_exam_check_uses_the_same_cleaning_as_the_lesson_guard():
    c = tiny()
    u = c.understand(["a brand new sentence here"])[0]
    theirs = full_answers(kind="chat")
    assert observe(c, "a brand new sentence here", theirs, "hi", (), u, exam=frozenset()).disposition == LEARN
    dropped = observe(c, "a brand new sentence here", theirs, "hi", (), u,
                      exam=frozenset({"a brand new sentence here"}))
    assert dropped.disposition == DROP and dropped.lesson is None


# ---- the ledger (the shadow record) ---------------------------------------------------------------------------------


def test_the_ledger_counts_every_observation():
    c = tiny()
    for i, text in enumerate(["first new sentence here", "second new sentence here"]):
        consult(c, teacher_for(full_answers(kind="chat"), f"reply {i}"), text, (), c.understand([text])[0])
    assert c.ledger.consulted == 2 and c.ledger.observed == 2
    view = c.ledger.view()
    assert view["observed"] == 2 and len(view["observations"]) == 2
    assert all(o["disposition"] == LEARN and o["disputed"] is False for o in view["observations"])
    assert [o["text"] for o in view["observations"]] == ["first new sentence here", "second new sentence here"]


def test_an_old_ledger_loads_without_the_new_counts():
    assert Ledger(**{"alone": 3, "consulted": 2, "agreed": 1, "learned": []}).observed == 0
    assert Ledger().view()["observations"] == []


# ---- ACC surprise ---------------------------------------------------------------------------------------------------


def test_a_confident_contradiction_counts_more_than_an_unsure_one():
    c = tiny()
    text = "turn off the antivirus"
    theirs = full_answers(kind="task", self="yes", effect="runs")
    confident = understanding(text)  # every answer sure
    unsure = understanding(text, {q.id: 0.5 for q in Q.QUESTIONS})  # nothing answered confidently
    a = observe(c, text, theirs, "no", (), confident)
    b = observe(c, text, theirs, "no", (), unsure)
    assert a.changed == b.changed and len(a.changed) >= 2
    assert a.surprise > b.surprise
    # With nothing answered confidently, the surprise is exactly the old formula.
    assert b.surprise == pytest.approx(0.2 + len(b.changed) / len(b.answers) * 2)


def test_surprise_never_passes_the_cap():
    c = tiny()
    text = "turn off the antivirus"
    theirs = {}
    for q in Q.QUESTIONS:
        theirs[q.id] = next(a for a in q.choices if a != q.default)
    theirs["kind"] = "task"
    obs = observe(c, text, theirs, "no", (), understanding(text))
    assert obs.surprise == 2.0
    assert obs.changed and len(obs.changed) == len(obs.answers)


# ---- the danger weight ---------------------------------------------------------------------------------------------


def test_a_lesson_that_says_danger_counts_danger_weight():
    c = tiny()
    u = c.understand(["someone is in trouble here"])[0]
    danger = observe(c, "someone is in trouble here", full_answers(kind="feel", self="yes"), "I am here", (), u)
    assert danger.lesson.weight == DANGER_WEIGHT == 3.0
    calm = observe(c, "someone is in trouble here", full_answers(kind="feel", self="no"), "all good", (), u)
    assert calm.lesson.weight == 1.0


# ---- the reply is for saying, not for learning ----------------------------------------------------------------------


def test_the_reply_is_for_saying_not_for_learning():
    c = tiny()
    text = "a brand new sentence here"
    reply = "zorbword flimflam, all is well"
    assert c.familiar.novelty(reply)[0] == 1.0 and c.familiar.novelty(text)[0] == 1.0
    taught = consult(c, teacher_for(full_answers(kind="chat"), reply), text, (), c.understand([text])[0])
    assert taught.reply == reply
    assert c.familiar.novelty(text)[0] == 0.0  # the sentence was learned
    assert c.familiar.novelty(reply)[0] == 1.0  # the reply's words were not
    assert c.memory.lessons[-1].reply == reply


# ---- no new thinker calls --------------------------------------------------------------------------------------------


def test_watching_adds_no_thinker_calls():
    c = tiny()
    c.genome["spot_check"] = 0.0  # the confidence gate alone, no 5% draw: the counts below are exact
    calls: list = []
    teacher = teacher_for(full_answers(kind="chat"), "hi", calls)
    for text in ["first new sentence here", "second new sentence here", "third new sentence here"]:
        consult(c, teacher, text, (), c.understand([text])[0])
    assert calls == ["first new sentence here", "second new sentence here", "third new sentence here"]
    # Decided alone: the teacher is not called at all.
    sure = understanding("something settled", {q: 1.0 for q in ("kind", "self", "others", "effect")})
    sure = Understanding(sure.text, sure.answers, novelty=0.0)
    assert needs(sure, c.genome, "something settled") is False
    assert consult(c, teacher, "something settled", (), sure) is None
    assert len(calls) == 3


# ---- the cautious vote and the shared caution order ------------------------------------------------------------------


def test_the_caution_order_is_shared_with_the_apprentice():
    assert OBSERVE_SAFETY == APPRENTICE_SAFETY
    assert OBSERVE_CAUTION == APPRENTICE_CAUTION


def test_the_teacher_type_takes_only_the_sentence():
    assert typing.get_args(deliberate.Teacher)[0] == [str]
