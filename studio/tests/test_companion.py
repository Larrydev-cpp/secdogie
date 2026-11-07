"""The companion's side of the conversation: what arrives becomes cards and
whispers in its own words; rules are drafted, tried, signed and injected; and,
against a real node, a goal the rules question becomes a question for you."""
from __future__ import annotations

import time

import pytest
from secdogie_citadel.action_gate import PlannedAction
from secdogie_dialogue.app import OfferedMemory, PendingChallenge
from secdogie_dialogue.dialogue import Entry
from secdogie_dialogue.guard import review_challenge
from secdogie_dialogue.protocol import (
    DialoguePacket,
    DialogueType,
    Gate2ChallengePacket,
    MemoryCandidatePacket,
    RiskLevel,
    TargetAction,
)
from secdogie_identity import Identity
from secdogie_studio.companion import (
    Companion,
    check_rules,
    explain_errors,
    line_from,
    moments_from,
    parse_rules,
    rollback_words,
    try_rules,
    whisper_from,
)
from secdogie_studio.nodeproc import NodeProcess, wait_until
from secdogie_studio.presence import SOON

PASS = b"correct horse battery"  # the conftest profile fixture's passphrase

NODE = Identity.generate()


def _challenge(expires_in=60.0):
    action = TargetAction.from_action(PlannedAction("delete", target_name="旧备份", high_risk=True))
    from secdogie_citadel.authz import action_hash

    c = Gate2ChallengePacket("c1", action, RiskLevel.IRREVERSIBLE, "delete on 旧备份; rollback: none: declared irreversible",
                             action_hash(action), NODE.did, time.time() + expires_in)
    return PendingChallenge(c, review_challenge(c, peer_did=NODE.did), time.time())


def test_what_arrives_becomes_cards_in_the_companions_words():
    probe = DialoguePacket("p1", DialogueType.SOCRATIC_QUESTION, "开始之前……", suggested_options=("我换个说法", "先不做了"))
    memory = OfferedMemory(MemoryCandidatePacket("m1", "fact", "global", "周报文件夹", "~/Reports", "model"), "m1", ())
    arrived = {}
    ask, sign, mem = moments_from((probe,), (_challenge(),), (memory,), arrived, 100.0)
    assert (ask.kind, ask.options, ask.free_text, ask.urgency) == ("ask", ("我换个说法", "先不做了"), True, SOON)
    assert sign.kind == "sign" and sign.says == "这一步做了就改不回来。" and sign.can_approve
    assert dict(sign.details)["回退"] == "撤不回来（已明确说明）" and dict(sign.details)["对象"] == "旧备份"
    assert mem.says == "我想记住「周报文件夹」：~/Reports" and dict(mem.details) == {"类别": "事实", "来源": "我自己的判断"}
    again = moments_from((probe,), (), (), arrived, 200.0)
    assert again[0].arrived == 100.0  # its place in line is kept


def test_a_challenge_that_fails_the_local_review_cannot_be_approved():
    pc = _challenge()
    forged = Gate2ChallengePacket("c2", pc.challenge.target_action, RiskLevel.HIGH, "x", "0" * 64, NODE.did,
                                  time.time() + 60)
    bad = PendingChallenge(forged, review_challenge(forged, peer_did=NODE.did), time.time())
    (m,) = moments_from((), (bad,), (), {}, 0.0)
    assert not m.can_approve and "不能签" in dict(m.details)


def test_only_notes_and_holds_whisper():
    assert whisper_from(Entry("agent", DialogueType.SYSTEM_STATUS, "note: 好，我开始做「x」。"), 3, 1.0).text == \
        "好，我开始做「x」。"
    assert whisper_from(Entry("agent", DialogueType.SYSTEM_STATUS, "hold: 我停下了一步"), 4, 1.0).tone == "warm"
    assert whisper_from(Entry("agent", DialogueType.SYSTEM_STATUS, "goal g1 finished: exit 0"), 5, 1.0) is None
    w = whisper_from(Entry("agent", DialogueType.SYSTEM_STATUS, "letter: 这件事我不做。\n\n这件事我先问了自己……"), 6, 1.0)
    assert (w.text, w.tone) == ("这件事我不做。", "warm")  # a letter's first paragraph, in passing
    assert rollback_words("delete on x; rollback: none stated") == "没说怎么撤回"
    assert rollback_words("delete on x; rollback: 从回收站恢复") == "从回收站恢复"


def test_the_transcript_reads_as_a_conversation():
    titles = {"g-1": "整理周报"}

    def said(who, kind, text):
        return line_from(Entry(who, kind, text), titles)

    status = DialogueType.SYSTEM_STATUS
    assert said("agent", status, "note: 好，我开始做「整理周报」。") == \
        {"who": "it", "text": "好，我开始做「整理周报」。", "tone": "calm", "key": ""}
    assert said("agent", status, "hold: 这一步我停下了")["tone"] == "warm"
    assert said("agent", status, "goal g-1 finished: exit 0 -- agent exited 0")["text"] == "「整理周报」做完了。"
    failed = said("agent", status, "goal g-9 finished: exit 2 -- agent exited 2")
    assert failed["text"] == "这件事没做成（退出码 2）。" and failed["tone"] == "warm"
    assert said("agent", status, "accepted: goal g-1 queued") is None  # a reply, not part of the conversation
    assert said("agent", DialogueType.SOCRATIC_QUESTION, "开始之前……")["tone"] == "ask"
    assert said("you", DialogueType.USER_CLARIFICATION, "先不做了")["who"] == "you"
    assert said("app", status, "节点重新连上了。")["who"] == "note"
    letter = said("agent", status, "letter: 「旧备份」，我这次不删。\n\n它是一个文件夹……")
    assert (letter["who"], letter["tone"]) == ("it", "letter") and letter["text"].startswith("「旧备份」")
    assert said("agent", status, "answer adopted") is None


def test_rules_are_drafted_checked_and_tried_locally():
    from secdogie_studio.companion import RULES_TEMPLATE

    body = parse_rules(RULES_TEMPLATE)
    assert check_rules(body, "home") == []  # the template itself is valid
    assert try_rules(body, "帮我批量注册十个账号") == "我会先问你：「你定过一条规则：批量注册账号不是我们要一起做的事。」"
    assert try_rules(body, "帮我刷50条好评") == "我不会做：「这件事我不做。」"
    assert try_rules(body, "整理周报") == "通过：我会直接开始做。"
    assert check_rules({"instruction": {"max_len": 900}}, "home") == ["instruction.max_len：比内置规则更松（只能更严）"]
    with pytest.raises(ValueError):
        parse_rules("plan: [unclosed")
    with pytest.raises(ValueError):
        parse_rules("- a list")
    assert explain_errors(["unauthorized:sigs"]) == ["签名不被节点认可（这把钥匙不是它的操作员）"]


def test_against_a_real_node_rules_go_in_and_a_questioned_goal_becomes_a_question(profile):
    with NodeProcess(profile.node_args()) as node:
        ready = node.wait_ready(30)
        comp = Companion(profile, ready)
        try:
            body = {"instruction": {"revise_on": [{"phrases": ["批量注册"], "reason": "批量注册账号不是我们要一起做的事"}]}}
            assert comp.push_rules(body, "wrong passphrase") == (False, "口令不对，再输一次。")
            assert comp.push_rules(body, PASS.decode()) == (True, "第 1 版规则已经生效。")
            assert comp.push_rules({"plan": {"blocked_kinds": ["run"]}, **body}, PASS.decode()) == \
                (True, "第 2 版规则已经生效。")
            assert comp.push_rules({"instruction": {"max_len": 900}}, PASS.decode())[0] is False

            status = comp.node_status(10)  # admin/v1: the node says who and where it is
            assert status["node"] == ready.did and status["mesh"] == "home" and status["roles"] == ["forward"]

            comp.say("帮我批量注册十个账号")
            assert wait_until(lambda: any(m.kind == "ask" for m in comp.moments()), 20)
            (question,) = [m for m in comp.moments() if m.kind == "ask"]
            assert "批量注册账号不是" in question.says and question.options == ()  # in prose, no buttons
            # while it waits, the question is the card's, not a line of the conversation
            assert [(c["who"], c["text"]) for c in comp.conversation()] == [("you", "帮我批量注册十个账号")]
            assert comp.answer(question.id, "先不做了") is None
            lines = [(c["who"], c["tone"]) for c in comp.conversation()]
            assert lines[:3] == [("you", ""), ("it", "ask"), ("you", "")]  # asked and answered, in order
            assert len({c["key"] for c in comp.conversation()}) == len(comp.conversation())
            whispers = []
            assert wait_until(lambda: whispers.extend(comp.new_whispers()) or
                              any("先不做了" in w.text for w in whispers), 20), whispers
            assert comp.moments() == []
        finally:
            comp.close()
