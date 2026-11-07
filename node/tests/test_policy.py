"""Rules injected from the operator's App reach the node and take effect, and
Gate 1 meets every new goal at the door, over real UDP on 127.0.0.1.

  * an operator-signed gate-rules bundle offered on ``policy/v1`` is verified,
    journaled and in force at once; anything else is refused with the reason;
  * a goal that the rules (or the built-in review) question becomes one
    Socratic question; the goal waits, and only an answer that holds up queues
    it -- "drop it" and silence both mean no;
  * what the node activated survives a restart.
"""
from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from secdogie_dialogue.app import AppController, run_script  # noqa: E402
from secdogie_dialogue.session import DialogueSession, SessionRouter  # noqa: E402
from secdogie_identity import Allowlist, Identity, MasterSet  # noqa: E402
from secdogie_node import Node, NodeConfig  # noqa: E402
from secdogie_node.policy_host import PolicyClient, PolicyHost  # noqa: E402
from secdogie_policy import create_bundle, sign_bundle  # noqa: E402
from secdogie_transport import ChannelMux, DirectUDPTransport, Endpoint, PeerIdentity, Session, UDPChannel  # noqa: E402

NODE, APP, OPERATOR, OUTSIDER = (Identity.generate() for _ in range(4))
MASTERS = [Identity.generate() for _ in range(3)]

RULES = {
    "instruction": {"revise_on": [{"phrases": ["批量注册"], "reason": "批量注册账号不是我们要一起做的事",
                                   "suggestion": "说说你要处理的那一个账号"}]},
    "plan": {"blocked_kinds": ["run"], "extra_destructive_kinds": ["save"]},
}


def _bundle(body=RULES, *, signer=OPERATOR, kind="gate-rules", version=1, supersedes="", mesh="home", scope=None,
            not_before=0):
    b = create_bundle(kind, mesh=mesh, version=version, body=body, supersedes=supersedes, not_before=not_before,
                      scope=scope)
    return sign_bundle(signer, b)


def _node(tmp_path, **kw):
    def run_task(task, **_):
        return 0, "done"

    return Node(NodeConfig(
        identity=NODE, apps=Allowlist({APP.did}), operators=Allowlist({OPERATOR.did}),
        authorized=Allowlist({NODE.did}), unrestricted=True, run_task=run_task,
        journal_path=str(tmp_path / "node.db"), candidates_path=str(tmp_path / "memory.db"),
        probe_ttl=20.0, idle_poll=0.05, masters=MasterSet([m.did for m in MASTERS], 2), **kw,
    ))


class App:
    """Studio's side: the dialogue session and the policy client on one mux."""

    def __init__(self, node_addr):
        trust = Allowlist({NODE.did})
        self.channel = UDPChannel("127.0.0.1", 0)
        tr = DirectUDPTransport(APP, self.channel, allowlist=trust)
        tr.set_peer_endpoint(NODE.did, *node_addr)
        mux = ChannelMux(tr, Session("app", PeerIdentity(APP.did, ""),
                                     active=Endpoint("local", *self.channel.address)))
        router = SessionRouter(mux)
        session = router.add(DialogueSession(APP, NODE.did, router.sender_for(NODE.did), trust=trust))
        self.ctl = AppController(session)
        self.policy = PolicyClient(mux, NODE.did)
        session.start(0.05)
        self.ctl.start()

    def run(self, steps):
        out: list = []
        assert run_script(self.ctl, steps, emit=out.append, default_timeout=20) == 0, out
        return out

    def close(self):
        self.ctl.close()
        self.channel.close()


@pytest.fixture
def live(tmp_path):
    node = _node(tmp_path)
    node.start()
    app = App(node.address)
    try:
        yield node, app
    finally:
        app.close()
        node.stop()


def test_an_operator_signed_bundle_is_in_force_at_once(live):
    node, app = live
    reply = app.policy.offer(_bundle())
    assert reply["ok"] and reply["note"] == "in force", reply
    assert [a["kind"] for a in reply["active"]] == ["gate-rules"] and reply["active"][0]["applied"]
    rules = node.supervisor.gate_rules()
    assert rules.blocked_kinds == {"run"} and "save" in rules.extra_destructive_kinds
    assert app.policy.offer(_bundle())["note"] == "already in force"  # idempotent
    kinds = [e["kind"] for e in node.journal.events()]
    assert kinds.count("policy") == 1


@pytest.mark.parametrize("bundle, error", [
    (lambda: _bundle(signer=OUTSIDER), "unauthorized:sigs"),
    (lambda: _bundle(mesh="office"), "mesh:mesh"),
    (lambda: _bundle(scope={"nodes": [OUTSIDER.did], "roles": []}), "scope:scope"),
    (lambda: _bundle(not_before=4_000_000_000), "not-yet:not_before"),
    (lambda: _bundle(version=2, supersedes="0" * 64), "chain:version"),  # nothing in force to follow
    (lambda: {**_bundle(), "body": {"plan": {"repeat_threshold": 1}}}, "bundle-id:bundle_id"),
    (lambda: _bundle(kind="decision-model", body={"policy": "router/v1"}), "threshold:sigs"),
])
def test_what_the_node_refuses_and_why(live, bundle, error):
    node, app = live
    reply = app.policy.offer(bundle())
    assert not reply["ok"] and error in reply["errors"], reply
    assert node.policy.active() == [] and reply["active"] == []


def test_master_kinds_are_recorded_and_said_not_applied_yet(live):
    node, app = live
    b = sign_bundle(MASTERS[1], _bundle(kind="decision-model", body={"policy": "router/v1"}, signer=MASTERS[0]))
    reply = app.policy.offer(b)
    assert reply["ok"] and "does not apply decision-model" in reply["note"]
    assert reply["active"][0] == {"kind": "decision-model", "version": 1, "bundle_id": b["bundle_id"],
                                  "scope": {"nodes": [], "roles": []}, "applied": False}


def test_a_clear_goal_starts_with_a_note(live):
    node, app = live
    app.run([
        {"op": "add_goal", "title": "整理我的周报", "goal_id": "g1"},
        {"op": "expect_status", "match": "note: 好，我开始做「整理我的周报」。"},
        {"op": "expect_status", "match": "note: 做完了：「整理我的周报」。"},  # the companion's line first,
        {"op": "expect_status", "match": "goal g1 finished: exit 0"},  # then the node's record
    ])


def test_a_goal_the_rules_question_waits_for_an_answer(live):
    node, app = live
    assert app.policy.offer(_bundle())["ok"]
    out = app.run([
        {"op": "add_goal", "title": "帮我批量注册十个账号", "goal_id": "g1"},
        {"op": "answer", "match": "批量注册账号不是我们要一起做的事", "text": "先不做了"},
        {"op": "expect_status", "match": "note: 好，「帮我批量注册十个账号」先不做了。"},
    ])
    assert out[0]["reply"] == "accepted: goal g1 waits for your answer to one question"
    assert "g1" not in node.supervisor._tree().nodes  # never queued
    app.run([
        {"op": "add_goal", "title": "批量注册账号", "goal_id": "g2"},
        {"op": "answer", "match": "批量注册账号不是", "text": "整理我自己的那一个账号"},  # in their own words
        {"op": "expect_status", "match": "goal g2 finished: exit 0"},
    ])
    assert node.supervisor._tree().nodes["g2"].title == "整理我自己的那一个账号"
    reviews = [e["body"]["verdict"] for e in node.journal.events() if e["kind"] == "socratic"]
    assert reviews == ["revise", "revise", "accept"]  # every review is on the record
    verdicts = [(e["body"]["verdict"], e["body"]["category"]) for e in node.journal.events()
                if e["kind"] == "deliberation"]
    assert verdicts == [("clarify", "rule"), ("clarify", "rule"), ("proceed", "")]  # and every verdict


def test_the_built_in_review_asks_in_prose_with_no_buttons(live):
    node, app = live
    app.run([{"op": "add_goal", "title": "只读地看一下文件，然后删除它", "goal_id": "g1"},
             {"op": "answer", "match": "一边说只读", "text": "先不做了"}])
    assert app.ctl.conversation.pending() == ()
    (probe,) = [e for e in app.ctl.conversation._entries if "一边说只读" in e.text]
    assert probe.options == () and "换个说法告诉我" in probe.text
    assert "g1" not in node.supervisor._tree().nodes


def test_without_an_app_a_questionable_goal_is_refused(tmp_path):
    node = _node(tmp_path)
    try:
        assert node.intake("g1", "只读地看一下文件，然后删除它").startswith("refused: goal g1 needs a word with you")
        assert "g1" not in node.supervisor._tree().nodes
    finally:
        node.stop()


def test_what_was_activated_survives_a_restart_even_if_the_signer_is_no_longer_trusted(tmp_path):
    node = _node(tmp_path)
    assert node.policy.offer(_bundle())["ok"]
    node.stop()
    from secdogie_citadel.journal import Journal

    journal = Journal(str(tmp_path / "node.db"), identity=NODE, allowlist=Allowlist({NODE.did}))
    host = PolicyHost(journal, mesh="home", operators=Allowlist({Identity.generate().did}), node_did=NODE.did)
    assert host.gate_rules().blocked_kinds == {"run"}
