"""The window's logic, headless: the real ``AppController`` over a fake session
(what it sends is recorded; what the node says is delivered by hand), and a
real operator keystore on disk with a cheap KDF."""
from __future__ import annotations

import os
import stat

import pytest

pytest.importorskip("nacl")

from nacl import pwhash  # noqa: E402
from secdogie_app.local import OperatorKey  # noqa: E402
from secdogie_app.model import (  # noqa: E402
    APPROVAL,
    MEMORY,
    NODE,
    NOTICE,
    OPEN,
    PROBE,
    STATE_LABELS,
    YOU,
    DialogError,
    DialogModel,
    Message,
    diff_messages,
    new_goal_id,
)
from secdogie_citadel.action_gate import PlannedAction  # noqa: E402
from secdogie_citadel.authz import action_hash, verify_authorization  # noqa: E402
from secdogie_citadel.lessons import MemoryClass, candidate_id  # noqa: E402
from secdogie_dialogue.app import AppController  # noqa: E402
from secdogie_dialogue.protocol import (  # noqa: E402
    PROTOCOL_VERSION,
    ControlOp,
    ControlPacket,
    DialoguePacket,
    DialogueType,
    Envelope,
    Gate2ChallengePacket,
    Gate2ResponsePacket,
    Header,
    MemoryCandidatePacket,
    RiskLevel,
    TargetAction,
    Verdict,
    kind_of,
)
from secdogie_identity import Allowlist, Identity  # noqa: E402

APP, NODE_ID = Identity.generate(), Identity.generate()
CHEAP = {"opslimit": pwhash.argon2id.OPSLIMIT_MIN, "memlimit": pwhash.argon2id.MEMLIMIT_MIN}
DELETE = TargetAction("delete", "f-1", "file", "report.txt", "", True)
PASS = "correct horse"


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeSession:
    def __init__(self):
        self.identity, self.peer_did = APP, NODE_ID.did
        self.sent: list = []
        self.on_envelope = self.on_undeliverable = self.on_peer_down = self.on_peer_up = None

    def send(self, packet, *, reliable=None):
        self.sent.append(packet)
        return len(self.sent)

    def close(self):
        pass

    def of(self, cls):
        return [p for p in self.sent if isinstance(p, cls)]


_seq = iter(range(1, 10**9))


def deliver(ctl, packet):
    hdr = Header(PROTOCOL_VERSION, NODE_ID.did, APP.did, "s", next(_seq), 0)
    ctl.on_envelope(Envelope(hdr, kind_of(packet), packet, NODE_ID.did))


def status(text, about=""):
    return DialoguePacket(f"st-{next(_seq)}", DialogueType.SYSTEM_STATUS, text, in_reply_to=about)


def question(pid, text, options=()):
    return DialoguePacket(pid, DialogueType.SOCRATIC_QUESTION, text, suggested_options=tuple(options))


def challenge(action=DELETE, *, cid="ch1", expires=1100.0, claimed=None, why="the file goes to the bin"):
    return Gate2ChallengePacket(cid, action, RiskLevel.HIGH, why, claimed or action_hash(action), NODE_ID.did,
                                expires)


def memory(key="report-folder", value="reports go to ~/Reports", mid=None):
    return MemoryCandidatePacket(mid or candidate_id(MemoryClass.FACT, "global", key, value), "fact", "global",
                                 key, value, "model")


class FakeKeys:
    def __init__(self, configured=False):
        self.saved, self._configured = [], configured

    def configured(self):
        return self._configured

    def problem(self, key):
        return None if len(key.strip()) >= 8 else "That looks too short for an API key."

    def save(self, key, *, provider=None, model=None):
        self.saved.append((key, provider, model))
        self._configured = True
        return "/somewhere/secdogie.env"


@pytest.fixture
def made(tmp_path):
    clock = Clock()
    session = FakeSession()
    ctl = AppController(session, clock=clock)
    operators = Allowlist()
    key = OperatorKey(tmp_path / "operator.keystore", operators, kdf=CHEAP)
    keys = FakeKeys()
    model = DialogModel(ctl, key, keys, clock=clock)
    return model, ctl, session, clock, operators, keys


def by_kind(model, kind):
    model.refresh()
    return [m for m in model.messages() if m.kind == kind]


# ---- the stream ---------------------------------------------------------------------


def test_a_goal_and_what_the_node_says_about_it_in_order(made):
    model, ctl, s, _, _, _ = made
    gid = model.add_goal("  file the report ")
    (pkt,) = s.of(ControlPacket)
    assert pkt.op is ControlOp.ADD_GOAL and pkt.goal_id == gid and pkt.title == "file the report"
    deliver(ctl, status(f"accepted: goal {gid} queued", about=pkt.request_id))
    model.refresh()
    assert [(m.kind, m.text) for m in model.messages()] == [(YOU, "file the report"),
                                                            (NODE, f"accepted: goal {gid} queued")]


def test_goal_ids_sort_in_the_order_they_were_sent(made):
    model, _, _, clock, _, _ = made
    ids = [model.add_goal(str(i)) for i in range(5)]  # all in the same millisecond
    clock.t += 10.0
    ids.append(model.add_goal("later"))
    assert ids == sorted(ids) and len(set(ids)) == 6
    assert new_goal_id(9) < new_goal_id(10)  # fixed width: no "9" > "10"


def test_node_text_is_cleaned_for_display(made):
    model, ctl, _, _, _, _ = made
    deliver(ctl, status("done\x1b[31m‮evil\x00"))
    (m,) = by_kind(model, NODE)
    assert "\x1b" not in m.text and "‮" not in m.text and "\x00" not in m.text


def test_refresh_says_whether_anything_changed(made):
    model, ctl, _, _, _, _ = made
    assert model.refresh() is True  # the first look
    assert model.refresh() is False
    deliver(ctl, status("working"))
    assert model.refresh() is True
    assert model.refresh() is False
    model.add_goal("tidy up")
    assert model.refresh() is True


def test_diff_messages_gives_new_and_changed():
    a, b = Message("a", NODE, "x"), Message("b", PROBE, "q", state=OPEN, actions=("answer",))
    assert diff_messages((), (a, b)) == ([a, b], [])
    closed = Message("b", PROBE, "q", state="answered")
    c = Message("c", YOU, "y")
    assert diff_messages((a, b), (a, closed, c)) == ([c], [closed])
    assert diff_messages((a, b), (a, b)) == ([], [])


# ---- questions ------------------------------------------------------------------------


def test_the_composer_answers_the_oldest_open_question_else_sends_a_goal(made):
    model, ctl, s, _, _, _ = made
    deliver(ctl, question("p1", "Which folder?"))
    deliver(ctl, question("p2", "Which name?"))
    model.refresh()
    assert model.composer_hint() == "回答：Which folder?"
    assert model.send("Desktop") == "answer"
    (ans,) = s.of(DialoguePacket)
    assert ans.in_reply_to == "p1" and ans.content == "Desktop"
    assert model.send("report.pdf") == "answer"
    assert model.send("tidy up") == "goal"
    assert [p.op for p in s.of(ControlPacket)] == [ControlOp.ADD_GOAL]
    with pytest.raises(DialogError):
        model.send("   ")


def test_a_question_card_with_options(made):
    model, ctl, s, _, _, _ = made
    deliver(ctl, question("p1", "Which folder?", ("Downloads", "Desk\x07top")))
    (card,) = by_kind(model, PROBE)
    assert card.state == OPEN and card.actions == ("answer",) and card.options == ("Downloads", "Desk top")
    model.answer("p1", option=2)
    assert s.of(DialoguePacket)[0].content == "Desk\x07top"  # the option as offered; only the display is cleaned
    model.refresh()
    msgs = model.messages()
    assert [m.state for m in msgs if m.kind == PROBE] == ["answered"]
    assert msgs[-1].kind == YOU and msgs[-1].ref == "p1"
    with pytest.raises(DialogError):
        model.answer("p1", "again")


def test_a_question_the_node_closed_is_closed(made):
    model, ctl, _, _, _, _ = made
    deliver(ctl, question("p1", "Which folder?"))
    model.refresh()
    deliver(ctl, status("probe expired", about="p1"))
    (card,) = by_kind(model, PROBE)
    assert card.state == "closed" and card.actions == ()


# ---- Gate 2 -----------------------------------------------------------------------------


def _verify(resp, operator_did, clock):
    planned = PlannedAction("delete", "f-1", "file", "report.txt", "", True)
    return verify_authorization(resp.authorization, planned, operators=Allowlist({operator_did}),
                                subject=NODE_ID.did, now=clock.t)


def test_the_first_approval_sets_the_passphrase_and_signs(made, tmp_path):
    model, ctl, s, clock, operators, _ = made
    deliver(ctl, challenge())
    (card,) = by_kind(model, APPROVAL)
    assert card.state == OPEN and card.actions == ("approve", "deny") and card.expires_at == 1100.0
    assert "delete" in card.text and any("一致" in d for d in card.detail)
    assert not model.passphrase_set and "口令未设置" in model.status()
    model.approve("ch1", PASS, PASS)
    path = tmp_path / "operator.keystore"
    assert path.exists() and stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert PASS not in path.read_text(encoding="utf-8")  # the passphrase never touches the disk
    assert model.passphrase_set and operators.dids() == {model.key.did}
    (resp,) = s.of(Gate2ResponsePacket)
    assert resp.user_verdict is Verdict.APPROVE
    assert _verify(resp, model.key.did, clock).ok
    (card,) = by_kind(model, APPROVAL)
    assert card.state == "approved" and card.actions == () and card.state in STATE_LABELS


@pytest.mark.parametrize("passphrase, again, why", [
    (PASS, None, "输入两次"),
    (PASS, PASS + "x", "不一致"),
    ("short", "short", "至少"),
    ("", "", "设置"),
])
def test_a_first_approval_without_a_good_pair_sets_nothing_and_signs_nothing(made, tmp_path, passphrase, again,
                                                                              why):
    model, ctl, s, _, operators, _ = made
    deliver(ctl, challenge())
    with pytest.raises(DialogError, match=why):
        model.approve("ch1", passphrase, again)
    assert not (tmp_path / "operator.keystore").exists() and len(operators) == 0
    assert s.sent == [] and len(ctl.challenges()) == 1


def test_later_approvals_unlock_with_the_passphrase_and_a_wrong_one_signs_nothing(made):
    model, ctl, s, clock, _, _ = made
    deliver(ctl, challenge(cid="ch1"))
    model.refresh()
    model.approve("ch1", PASS, PASS)
    first_did = model.key.did
    deliver(ctl, challenge(cid="ch2"))
    model.refresh()
    with pytest.raises(DialogError, match="口令不对"):
        model.approve("ch2", "wrong passphrase")
    with pytest.raises(DialogError, match="请输入口令"):
        model.approve("ch2", "")
    assert len(s.of(Gate2ResponsePacket)) == 1
    assert [m.state for m in by_kind(model, APPROVAL)] == ["approved", OPEN]
    model.approve("ch2", PASS)  # "again" is ignored once the passphrase is set
    resp = s.of(Gate2ResponsePacket)[-1]
    assert model.key.did == first_did and _verify(resp, first_did, clock).ok


def test_an_unsignable_challenge_offers_only_deny_and_never_touches_the_key(made, tmp_path):
    model, ctl, s, _, _, _ = made
    deliver(ctl, challenge(claimed="00" * 32, why="looks\x1b[0m fine"))
    (card,) = by_kind(model, APPROVAL)
    assert card.actions == ("deny",) and any("不一致" in d for d in card.detail)
    assert all("\x1b" not in d for d in card.detail)
    with pytest.raises(DialogError, match="不能签"):
        model.approve("ch1", PASS, PASS)
    assert not (tmp_path / "operator.keystore").exists()  # nothing unsignable ever sets or asks for the key
    model.deny("ch1")
    (resp,) = s.of(Gate2ResponsePacket)
    assert resp.user_verdict is Verdict.DENY and not resp.authorization
    assert [m.state for m in by_kind(model, APPROVAL)] == ["denied"]


def test_an_expired_approval_closes_as_not_approved(made):
    model, ctl, s, clock, _, _ = made
    deliver(ctl, challenge(expires=1010.0))
    model.refresh()
    clock.t = 1011.0
    (card,) = by_kind(model, APPROVAL)
    assert card.state == "expired" and card.actions == ()
    with pytest.raises(DialogError):
        model.approve("ch1", PASS, PASS)
    assert s.sent == []


def test_when_the_node_goes_away_open_cards_close(made):
    model, ctl, _, _, _, _ = made
    deliver(ctl, challenge())
    deliver(ctl, question("p1", "Which folder?"))
    model.refresh()
    ctl.on_peer_down()
    model.refresh()
    states = {m.kind: m.state for m in model.messages() if m.kind in (APPROVAL, PROBE)}
    assert states == {APPROVAL: "expired", PROBE: "closed"}
    assert any(m.kind == NOTICE for m in model.messages())
    assert model.status().startswith("本机节点没有响应")


# ---- memory ----------------------------------------------------------------------------------


def test_remember_and_skip(made):
    model, ctl, s, _, _, _ = made
    deliver(ctl, memory())
    deliver(ctl, memory(key="editor", value="vim"))
    cards = by_kind(model, MEMORY)
    assert [c.actions for c in cards] == [("remember", "skip")] * 2 and "reports go to ~/Reports" in cards[0].text
    model.remember(cards[0].ref)
    model.skip(cards[1].ref)
    (pkt,) = s.of(ControlPacket)
    assert pkt.op is ControlOp.CONFIRM_MEMORY and pkt.memory_id == cards[0].ref
    assert [c.state for c in by_kind(model, MEMORY)] == ["remembered", "skipped"]
    with pytest.raises(DialogError):
        model.remember(cards[1].ref)


def test_a_memory_that_does_not_match_its_id_can_only_be_skipped(made):
    model, ctl, s, _, _, _ = made
    deliver(ctl, memory(mid="ab" * 32))
    (card,) = by_kind(model, MEMORY)
    assert card.actions == ("skip",) and card.detail
    with pytest.raises(DialogError):
        model.remember(card.ref)
    assert s.sent == []


# ---- stop -------------------------------------------------------------------------------------


def test_stop_stops_every_goal_not_yet_finished(made):
    model, ctl, s, clock, _, _ = made
    g1 = model.add_goal("one")
    clock.t += 1
    g2 = model.add_goal("two")
    clock.t += 1
    g3 = model.add_goal("three")
    refused = s.of(ControlPacket)[2]
    deliver(ctl, status("refused: headless node", about=refused.request_id))
    deliver(ctl, status(f"goal {g1} finished: exit 0 -- done"))
    model.refresh()
    assert model.active_goals() == [g2]
    assert model.stop() == [g2]
    stop = s.of(ControlPacket)[-1]
    assert stop.op is ControlOp.STOP and stop.goal_id == g2
    assert model.active_goals() == [] and g3
    with pytest.raises(DialogError):
        model.stop()


def test_stop_with_a_queue_stops_them_all(made):
    model, _, s, clock, _, _ = made
    ids = []
    for t in ("a", "b"):
        ids.append(model.add_goal(t))
        clock.t += 1
    assert model.stop() == ids
    assert [p.goal_id for p in s.of(ControlPacket) if p.op is ControlOp.STOP] == ids


# ---- the API key --------------------------------------------------------------------------


def test_the_api_key(made):
    model, _, _, _, _, keys = made
    assert model.api_key_needed()
    with pytest.raises(DialogError, match="too short"):
        model.save_api_key("sk-1")
    assert keys.saved == []
    model.save_api_key("sk-ant-0123456789", provider="anthropic", model="claude-x")
    assert keys.saved == [("sk-ant-0123456789", "anthropic", "claude-x")]
    assert not model.api_key_needed()


def test_without_an_api_key_manager_none_is_asked_for(made):
    _, ctl, _, _, _, _ = made
    model = DialogModel(ctl, made[0].key, None)
    assert not model.api_key_needed()
    with pytest.raises(DialogError):
        model.save_api_key("sk-ant-0123456789")
