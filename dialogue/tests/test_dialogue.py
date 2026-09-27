"""The probe / clarification loop. A probe is resolved only by an explicit,
non-empty clarification that names it and arrives in time; otherwise it expires
and the step stays suspended. No implicit yes, no double answers, no revival."""
from __future__ import annotations

import itertools
import threading

import pytest
from secdogie_dialogue.dialogue import (
    Conversation,
    DialogueError,
    ProbeLedger,
    ProbeStatus,
    system_status,
)
from secdogie_dialogue.protocol import DialoguePacket, DialogueType

OP = "did:key:zOperatorDevice"


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _ledger(clock=None, ttl=60.0):
    ids = (f"p{i}" for i in itertools.count(1))
    return ProbeLedger(ttl=ttl, clock=clock or Clock(), id_factory=lambda: next(ids))


def _clarify(probe_id, text="the toolbar one"):
    return DialoguePacket("m1", DialogueType.USER_CLARIFICATION, text, in_reply_to=probe_id)


# ---- Agent side -------------------------------------------------------------


def test_open_then_answer():
    ledger = _ledger()
    q = ledger.open("Which Save?", options=("toolbar", "dialog"), gate_finding="ambiguous-target")
    assert q.dialogue_type is DialogueType.SOCRATIC_QUESTION and q.probe_id == "p1"
    assert q.suggested_options == ("toolbar", "dialog") and q.gate_finding == "ambiguous-target"
    assert ledger.status("p1") is ProbeStatus.OPEN and [p.probe_id for p in ledger.pending()] == ["p1"]
    res = ledger.resolve(_clarify("p1"), answered_by=OP)
    assert res.answer == "the toolbar one" and res.answered_by == OP
    assert ledger.status("p1") is ProbeStatus.ANSWERED and ledger.pending() == ()


def test_a_probe_is_answered_once():
    ledger = _ledger()
    ledger.open("Which Save?")
    assert ledger.resolve(_clarify("p1", "first"), answered_by=OP).answer == "first"
    assert ledger.resolve(_clarify("p1", "second"), answered_by=OP) is None
    assert ledger.wait("p1", 0).answer == "first"


def test_only_a_non_empty_clarification_of_an_open_probe_resolves():
    ledger = _ledger()
    ledger.open("Which Save?")
    assert ledger.resolve(_clarify("nope"), answered_by=OP) is None  # unknown probe
    assert ledger.resolve(_clarify("p1", "   "), answered_by=OP) is None  # empty
    status = DialoguePacket("s", DialogueType.SYSTEM_STATUS, "yes", in_reply_to="p1")
    assert ledger.resolve(status, answered_by=OP) is None  # not a clarification
    assert ledger.status("p1") is ProbeStatus.OPEN


def test_expiry_is_a_no_and_a_late_answer_revives_nothing():
    clock = Clock()
    ledger = _ledger(clock, ttl=60)
    ledger.open("Delete these files?")
    ledger.open("Which window?", ttl=600)
    clock.t += 60
    assert ledger.expire() == ("p1",)
    assert ledger.status("p1") is ProbeStatus.EXPIRED and ledger.status("p2") is ProbeStatus.OPEN
    assert ledger.resolve(_clarify("p1", "yes"), answered_by=OP) is None
    assert ledger.status("p1") is ProbeStatus.EXPIRED


def test_an_answer_arriving_at_the_deadline_is_too_late_even_before_expire_runs():
    clock = Clock()
    ledger = _ledger(clock, ttl=60)
    ledger.open("Delete these files?")
    clock.t += 60
    assert ledger.resolve(_clarify("p1", "yes"), answered_by=OP) is None
    assert ledger.status("p1") is ProbeStatus.EXPIRED


def test_wait_returns_the_answer_from_another_thread():
    ledger = _ledger()
    ledger.open("Which Save?")
    t = threading.Timer(0.05, lambda: ledger.resolve(_clarify("p1"), answered_by=OP))
    t.start()
    res = ledger.wait("p1", timeout=5)
    t.join()
    assert res is not None and res.answer == "the toolbar one"


def test_wait_times_out_closed():
    ledger = _ledger()
    ledger.open("Which Save?")
    assert ledger.wait("p1", timeout=0.05) is None
    assert ledger.status("p1") is ProbeStatus.EXPIRED
    assert ledger.resolve(_clarify("p1"), answered_by=OP) is None  # and it stays closed
    assert ledger.wait("unknown", timeout=5) is None  # an unknown probe does not block


# ---- App side ---------------------------------------------------------------


def _probe(pid="p1", options=("toolbar", "dialog")):
    return DialoguePacket(pid, DialogueType.SOCRATIC_QUESTION, "Which Save?", suggested_options=options)


def test_answer_by_option_or_text():
    conv = Conversation()
    assert conv.receive(_probe("p1")) and conv.receive(_probe("p2"))
    a = conv.answer("p1", option=2)
    assert a.dialogue_type is DialogueType.USER_CLARIFICATION and a.in_reply_to == "p1" and a.content == "dialog"
    b = conv.answer("p2", "neither: use Export")
    assert b.content == "neither: use Export" and conv.pending() == ()


def test_the_app_refuses_bad_answers():
    conv = Conversation()
    conv.receive(_probe("p1"))
    for kwargs in ({"option": 0}, {"option": 3}, {"text": "  "}, {}, {"text": "x", "option": 1}):
        with pytest.raises(DialogueError):
            conv.answer("p1", **kwargs)
    assert [p.probe_id for p in conv.pending()] == ["p1"]  # still pending after refusals
    conv.answer("p1", option=1)
    with pytest.raises(DialogueError, match="no pending probe"):
        conv.answer("p1", option=1)  # not twice
    with pytest.raises(DialogueError, match="no pending probe"):
        conv.answer("never-asked", "yes")


def test_the_agent_closes_a_probe_with_a_status():
    conv = Conversation()
    conv.receive(_probe("p1"))
    conv.receive(system_status("probe expired; the step stays suspended", about="p1"))
    assert conv.pending() == ()
    with pytest.raises(DialogueError):
        conv.answer("p1", option=1)


def test_the_app_ignores_what_the_agent_should_not_send():
    conv = Conversation()
    assert not conv.receive(_clarify("p1"))  # the Agent does not clarify on the operator's behalf
    assert conv.receive(_probe("p1"))
    assert not conv.receive(_probe("p1"))  # a repeated probe is not a second question
    conv.answer("p1", option=1)
    assert not conv.receive(_probe("p1"))  # nor does it reopen an answered one
    assert conv.pending() == ()


def test_transcript_lines_are_clean():
    conv = Conversation()
    conv.receive(DialoguePacket("p1", DialogueType.SOCRATIC_QUESTION, "Which\nSave?\x1b[2J",
                                suggested_options=("tool\tbar", "dialog")))
    conv.answer("p1", option=1)
    assert conv.lines() == [
        "Agent: Which Save? [2J",  # newline and ESC became spaces: no terminal control gets through
        "  [1] tool bar",
        "  [2] dialog",
        "You: tool bar",
    ]


def test_full_loop_over_both_ends():
    ledger, conv = _ledger(), Conversation()
    conv.receive(ledger.open("Which Save?", options=("toolbar", "dialog")))
    res = ledger.resolve(conv.answer("p1", option=1), answered_by=OP)
    assert res.answer == "toolbar"
    conv.receive(system_status("adopted; continuing", about="p1"))
    assert [e.who for e in conv.transcript()] == ["agent", "you", "agent"]
