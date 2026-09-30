"""The whole loop, end to end, over real UDP on 127.0.0.1 (stage 2, D2).

A headless operator App and a resident node, each on its own DID-authenticated
transport. The node runs goals through the production runner
(``agent_run_task``) and the real agent loop; only the model (a script) and the
desktop (a fake with an accessibility tree) are stand-ins. The scenario:

  1. the App submits a goal; the loop proposes a destructive step;
  2. Gate 2: the node challenges, the App signs with the operator key, the node
     verifies, the signature is the step's confirmation, the step runs;
  3. the model's ask_user becomes a Socratic probe; the operator's answer goes
     back into the model's history;
  4. the model's ``remember`` lands in the S2 quarantine, is offered to the App,
     confirmed, and only then reaches S3 and the prompt;
  5. the App's inspector shows the structural view the loop saw;
  6. an action that fails in three runs is refused by Gate 1 on the fourth.
"""
from __future__ import annotations

import pytest

pytest.importorskip("nacl")
pytest.importorskip("secdogie_agent")

import fakes  # noqa: E402
from secdogie_citadel.episodes import episodes_from_events  # noqa: E402
from secdogie_dialogue.app import AppController, run_script  # noqa: E402
from secdogie_dialogue.session import DialogueSession, SessionRouter  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_identity.capability import create_capability  # noqa: E402
from secdogie_node import Node, NodeConfig  # noqa: E402
from secdogie_transport import (  # noqa: E402
    ChannelMux,
    DirectUDPTransport,
    Endpoint,
    PeerIdentity,
    Session,
    UDPChannel,
)

NODE, APP, OPERATOR, ISSUER = (Identity.generate() for _ in range(4))


@pytest.fixture
def wired(monkeypatch):
    """The production runner with a scripted model and the fake desk."""
    return fakes.install(monkeypatch.setattr)


def _app(node_addr):
    """The operator's App, as `secdogie-dialogue connect` builds it."""
    trust = Allowlist({NODE.did})
    ch = UDPChannel("127.0.0.1", 0)
    tr = DirectUDPTransport(APP, ch, allowlist=trust)
    tr.set_peer_endpoint(NODE.did, *node_addr)
    mux = ChannelMux(tr, Session("app", PeerIdentity(APP.did, ""), active=Endpoint("local", *ch.address)))
    router = SessionRouter(mux)
    session = router.add(DialogueSession(APP, NODE.did, router.sender_for(NODE.did), trust=trust))
    ctl = AppController(session)
    session.start(0.05)
    ctl.start()
    return ctl, ch


def test_the_stage_two_loop_end_to_end(wired, tmp_path):
    desk, histories = wired
    node = Node(NodeConfig(
        identity=NODE, apps=Allowlist({APP.did}), operators=Allowlist({OPERATOR.did}),
        authorized=Allowlist({NODE.did}), issuers=Allowlist({ISSUER.did}),
        journal_path=str(tmp_path / "node.db"), candidates_path=str(tmp_path / "memory.db"),
        challenge_ttl=20.0, probe_ttl=20.0, idle_poll=0.1,
    ))
    node.supervisor.add_grant(create_capability(ISSUER, NODE.did, ["physical.click", "physical.key"], ttl=3600))
    node.start()
    ctl, app_channel = _app(node.address)
    out: list = []
    try:
        rc = run_script(ctl, [
            {"op": "add_goal", "title": "file the report", "goal_id": "g1"},
            {"op": "approve", "action": {"kind": "key", "text": "delete"}},
            {"op": "answer", "match": "folder", "text": "Desktop"},
            {"op": "expect_status", "match": "goal g1 finished: exit 0"},
        ], unlock=lambda: OPERATOR, emit=out.append, default_timeout=30)
        assert rc == 0, out
        # 4a. the model's note is quarantined (S2), not yet in memory or the prompt
        (held,) = node.supervisor._candidates.items()
        assert held.key == "report-folder" and held.source == "model"
        assert "reports go to ~/Reports" not in node.supervisor._recall()
        rc = run_script(ctl, [
            {"op": "confirm_memory", "key": "report-folder"},
            {"op": "expect_view", "match": 'Button "Delete"'},
            {"op": "add_goal", "title": "tidy up", "goal_id": "g2"},
            {"op": "add_goal", "title": "tidy up", "goal_id": "g3"},
            {"op": "add_goal", "title": "tidy up", "goal_id": "g4"},
            {"op": "expect_status", "match": "goal g4 finished"},
            {"op": "add_goal", "title": "tidy up", "goal_id": "g5"},
            {"op": "expect_status", "match": "goal g5 finished"},
        ], unlock=lambda: OPERATOR, emit=out.append, default_timeout=30)
        assert rc == 0, out
    finally:
        ctl.close()
        node.stop()
        app_channel.close()

    events = node.journal.events()
    episodes = {e.goal_id: e for e in episodes_from_events(events).values()}

    # 2. Gate 2: the destructive step passed with the operator's signature, and ran
    assert desk.done[:2] == ["invoke:File name", "key"]
    delete = episodes["g1"].steps[1]
    assert delete.outcome == "ok" and delete.action_key

    # 3. the operator's answer reached the model's history
    asks = [e["body"] for e in events if e["kind"] == "ask_result"]
    assert asks == [{"goal_id": "g1", "answered": True, "answer": "Desktop"}]
    assert any("Desktop" in str(r) for h in histories for r in h)

    # 4b. the note is in S3 only because the App confirmed it, and now in the prompt
    fact = node.supervisor.memory_view().facts()[("global", "report-folder")]
    assert fact.value == "reports go to ~/Reports" and fact.basis == "operator" and fact.confirmed_by == APP.did
    assert "reports go to ~/Reports" in node.supervisor._recall()
    assert node.supervisor._candidates.items() == []  # it left the quarantine

    # 6. three failed runs of the same click -> Gate 1 refuses it on the fourth
    for gid in ("g2", "g3", "g4"):
        assert episodes[gid].steps[0].outcome == "failed"
    refused = episodes["g5"].steps[0]
    assert refused.outcome == "rejected" and any("known-failure" in f for f in refused.findings)
