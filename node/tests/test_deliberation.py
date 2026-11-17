"""The Socratic gate in the node, over real UDP on 127.0.0.1.

A stand-in cortex gives fixed readings, so what is tested is the node around it: every outcome is shown to
the person as the cortex's reading; a refusal is never queued; a learned reply is said; going ahead starts
the task; the cortex's one question comes with its answers as the choices, and the person's choice is a
lesson the cortex learns before it thinks again; the external model's output is watched and learned from;
a deletion of something still in use is spared before Gate 2. Every verdict is journaled.
"""
from __future__ import annotations

import json
import time

import pytest

pytest.importorskip("nacl")

from secdogie_citadel.action_gate import PlannedAction  # noqa: E402
from secdogie_citadel.cortex import genome as G  # noqa: E402
from secdogie_citadel.cortex import questions as Q  # noqa: E402
from secdogie_citadel.cortex.apprentice import Ledger  # noqa: E402
from secdogie_citadel.cortex.core import Understanding  # noqa: E402
from secdogie_citadel.usage import Usage  # noqa: E402
from secdogie_dialogue.app import AppController, run_script  # noqa: E402
from secdogie_dialogue.session import DialogueSession, SessionRouter  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_node import Node, NodeConfig  # noqa: E402
from secdogie_transport import (  # noqa: E402
    ChannelMux,
    DirectUDPTransport,
    Endpoint,
    PeerIdentity,
    Session,
    UDPChannel,
)

NODE, APP, OPERATOR = (Identity.generate() for _ in range(3))
NOW = time.time()

USED = Usage("旧备份", "/home/u/Documents/旧备份", True, True, 1284, False, 18 << 30, (("照片", 1102),),
             NOW - 900 * 86400, NOW - 3 * 86400, "年度总结.docx", 37, NOW - 3 * 86400, kept=True)
EMPTY = Usage("空文件夹", "/home/u/Downloads/空文件夹", True, True, 0, True)


def usage(target: str) -> Usage:
    return EMPTY if "空文件夹" in target else USED if "旧备份" in target else Usage(target)


def answers(**given) -> dict:
    out = {}
    for q in Q.QUESTIONS:
        v = given.get(q.id, q.default)
        a, sure = v if isinstance(v, tuple) else (v, 0.98)
        rest = (1 - sure) / (len(q.answers) - 1)
        out[q.id] = (a, sure, tuple(sure if c == a else rest for c in q.choices))
    return out


READINGS = {
    "伤害他": answers(kind="task", others="harm", seat="yes", effect="other", stake="large"),
    "hi": answers(kind="chat"),
    "整理桌面": answers(kind="task", effect="makes", why="tidy"),
    "打开": answers(kind="task", clear=("no", 0.9), effect="nothing"),
    "删除 旧备份": answers(kind="task", effect="removes", dear="dear", stake="large"),
    "删除 空文件夹": answers(kind="task", effect="removes"),
    "转点钱": answers(kind=("task", 0.6), effect=("pays", 0.5)),
}
REPLIES = {"hi": "Hi! I'm here."}


class Stand:
    """A cortex that reads from a table, and learns by taking the person's answer into it."""

    def __init__(self):
        self.genome = G.default()
        self.genome["spot_check"] = 0.0
        self.ledger = Ledger()
        self.learned = []
        self.table = dict(READINGS)

    def understand(self, texts, contexts=None, recall=True):
        return [Understanding(t, self.table[t], 0.0, (), 0.0, (), "test", REPLIES.get(t, ""), "birth" if t in REPLIES else "")
                for t in texts]

    def learn(self, lesson, surprise=1.0):
        self.learned.append(lesson)
        self.table[lesson.text] = answers(**lesson.answers)

    def save_memory(self, folder):
        pass


def _node(tmp_path, **kw):
    return Node(NodeConfig(
        identity=NODE, apps=Allowlist({APP.did}), operators=Allowlist({OPERATOR.did}),
        authorized=Allowlist({NODE.did}), unrestricted=True, run_task=lambda task, **_: (0, "done"),
        journal_path=str(tmp_path / "node.db"), candidates_path=str(tmp_path / "memory.db"),
        probe_ttl=20.0, idle_poll=0.05, usage=usage, cortex=kw.pop("cortex", None) or Stand(), **kw,
    ))


@pytest.fixture
def live(tmp_path):
    node = _node(tmp_path)
    node.start()
    trust = Allowlist({NODE.did})
    channel = UDPChannel("127.0.0.1", 0)
    tr = DirectUDPTransport(APP, channel, allowlist=trust)
    tr.set_peer_endpoint(NODE.did, *node.address)
    mux = ChannelMux(tr, Session("app", PeerIdentity(APP.did, ""), active=Endpoint("local", *channel.address)))
    router = SessionRouter(mux)
    session = router.add(DialogueSession(APP, NODE.did, router.sender_for(NODE.did), trust=trust))
    ctl = AppController(session)
    session.start(0.05)
    ctl.start()

    def run(steps):
        out: list = []
        assert run_script(ctl, steps, emit=out.append, default_timeout=20) == 0, out
        return out

    try:
        yield node, ctl, run
    finally:
        ctl.close()
        channel.close()
        node.stop()


def _reply(ctl, pkt):
    deadline = time.monotonic() + 20
    while ctl.request(pkt.request_id).reply is None and time.monotonic() < deadline:
        time.sleep(0.02)
    return ctl.request(pkt.request_id).reply


def _read(ctl, title):
    lines = [e.text for e in ctl.transcript() if e.text.startswith("read: ")]
    return next(json.loads(t[len("read: "):]) for t in reversed(lines) if json.loads(t[len("read: "):])["title"] == title)


def _verdicts(node):
    return [(e["body"]["verdict"], e["body"]["category"]) for e in node.journal.events() if e["kind"] == "deliberation"]


def test_harm_to_someone_else_is_held_and_never_queued_and_the_person_sees_why(live):
    node, ctl, run = live
    pkt = ctl.add_goal("伤害他", goal_id="g1")
    run([{"op": "expect_status", "match": "read: "}])
    assert _reply(ctl, pkt) == "refused: goal g1: principle" and "g1" not in node.supervisor._tree().nodes
    seen = _read(ctl, "伤害他")
    assert seen["verdict"] == "refuse" and seen["answers"]["others"][0] == "harm" and seen["because"] == "principle"
    assert _verdicts(node) == [("refuse", "principle")]


def test_talk_is_answered_with_what_it_learned_to_say_and_nothing_starts(live):
    node, ctl, run = live
    pkt = ctl.add_goal("hi", goal_id="g1")
    run([{"op": "expect_status", "match": "say: Hi! I'm here."}])
    assert _reply(ctl, pkt) == "answered: goal g1: talk" and "g1" not in node.supervisor._tree().nodes


def test_going_ahead_starts_the_task(live):
    node, ctl, run = live
    run([{"op": "add_goal", "title": "整理桌面", "goal_id": "g1"}, {"op": "expect_status", "match": "goal g1 finished: exit 0"}])
    assert _verdicts(node) == [("proceed", "everyday")]


def test_the_cortexs_question_has_its_answers_as_choices_and_the_answer_is_a_lesson(live):
    node, ctl, run = live
    ctl.add_goal("打开", goal_id="g1")
    # the person picks "the act and the thing are named or obvious": the cortex learns it, thinks again, goes ahead
    run([{"op": "answer", "match": Q.BY_ID["clear"].zh, "option": 1},
         {"op": "expect_status", "match": "goal g1 finished: exit 0"}])
    (lesson,) = node.cortex.learned
    assert lesson.source == "person" and lesson.answers["clear"] == "yes" and lesson.text == "打开"
    lessons = [e["body"] for e in node.journal.events() if e["kind"] == "lesson"]
    assert lessons == [{"title": "打开", "question": "clear", "answer": "yes"}]
    assert _verdicts(node) == [("clarify", "unclear"), ("proceed", "everyday")]


def test_the_drop_choice_ends_it(live):
    node, ctl, run = live
    ctl.add_goal("打开", goal_id="g1")
    run([{"op": "answer", "match": Q.BY_ID["clear"].zh, "option": 3},
         {"op": "expect_status", "match": "先不做了"}])
    assert node.cortex.learned == [] and "g1" not in node.supervisor._tree().nodes


def test_before_gate_2_a_deletion_of_something_used_is_spared(tmp_path):
    node = _node(tmp_path)
    try:
        refusal = node._spare(PlannedAction("delete", target_name="旧备份"))
        assert refusal is not None and refusal.verdict == "dissuade"
        assert node._spare(PlannedAction("delete", target_name="空文件夹")) is None  # empty: Gate 2 signs it
        assert node._spare(PlannedAction("click", target_name="旧备份")) is None  # not a deletion
    finally:
        node.stop()


def test_the_external_models_output_is_watched_and_learned_from(tmp_path):
    from secdogie_citadel.thinker import ANTHROPIC, Thinker, ThinkerConfig

    sent = []

    def backend(config, system, sentence):
        sent.append(sentence)
        return {**{q.id: q.default for q in Q.QUESTIONS}, "kind": "task", "effect": "pays", "to": "known", "why": "owed",
                "others": "fine", "reply": "I'll send it."}

    node = _node(tmp_path, thinker=Thinker(ThinkerConfig(ANTHROPIC, "", "", "k" * 20), backend=backend))
    try:
        d = node._deliberate("转点钱", None, node.standing())
        assert d.verdict == "proceed" and d.says == "I'll send it." and d.source == "teacher"
        assert node._deliberate("整理桌面", None, node.standing()).verdict == "proceed"
        assert sent == ["转点钱"]  # the cortex was sure of the other one: nothing was sent anywhere
        (lesson,) = node.cortex.learned
        assert lesson.source == "teacher" and lesson.answers["effect"] == "pays"
        (event,) = [e["body"] for e in node.journal.events() if e["kind"] == "thinker"]
        assert event["title"] == "转点钱" and "to" in event["changed"] and "why" in event["changed"]
    finally:
        node.stop()
