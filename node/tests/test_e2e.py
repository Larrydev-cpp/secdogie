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

import struct
import zlib

import pytest

pytest.importorskip("nacl")
pytest.importorskip("secdogie_agent")

from secdogie_agent import cli_common, screen  # noqa: E402
from secdogie_agent import loop as agent_loop  # noqa: E402
from secdogie_agent.axtree import AxElement  # noqa: E402
from secdogie_agent.providers.base import Action, VisionProvider  # noqa: E402
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

DELETE_BUTTON = AxElement(role="Button", name="Delete", automation_id="ID_DELETE", bounds=(0, 0, 40, 20))
NAME_FIELD = AxElement(role="Edit", name="File name", automation_id="", bounds=(0, 30, 200, 50))


def _png() -> bytes:
    raw = b"\x00" + b"\x80\x80\x80" * 4
    ihdr = struct.pack(">IIBBBBB", 4, 1, 8, 2, 0, 0, 0)

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class FakeDesk:
    """A desktop with an accessibility tree; a click at (5, 5) always fails."""

    def __init__(self):
        self.done: list[str] = []

    def setup(self, logger):
        pass

    def capture(self, region=None):
        return _png(), (200, 100)

    def execute(self, action):
        if action.kind == "left_click":
            return "error: nothing to click there"
        self.done.append(action.kind)
        return "ok"

    def element_targets(self):
        return [DELETE_BUTTON, NAME_FIELD]

    def invoke_element(self, el):
        self.done.append(f"invoke:{el.name}")
        return "invoked"


class Scripted(VisionProvider):
    def __init__(self, script, seen):
        self.script, self.seen = list(script), seen

    def next_action(self, task, screenshot_png, screen_size, history):
        self.seen.append([getattr(h, "result", "") for h in history])
        return Action.from_dict(self.script.pop(0))


FILE_THE_REPORT = [
    {"action": "click_element", "element": "e2"},  # focus the file name field (not destructive)
    {"action": "key", "keys": ["delete"], "rollback": "restore it from the Trash"},  # high-risk: Gate 2
    {"action": "ask_user", "text": "Which folder should the report go to?"},
    {"action": "remember", "text": "reports go to ~/Reports", "key": "report-folder"},
    {"action": "done", "text": "filed"},
]
TIDY_UP = [{"action": "left_click", "x": 5, "y": 5}, {"action": "done", "text": "tidied"}]


@pytest.fixture
def wired(monkeypatch):
    """The production runner with a scripted model and the fake desk."""
    scripts = [FILE_THE_REPORT, TIDY_UP, TIDY_UP, TIDY_UP, TIDY_UP]  # one per goal, in goal-id order
    histories: list = []
    desk = FakeDesk()
    monkeypatch.setattr(cli_common, "resolve_provider", lambda args, prog: Scripted(scripts.pop(0), histories))
    real_kwargs = cli_common.loop_config_kwargs

    def kwargs(args, *, task, backend=None):
        kw = real_kwargs(args, task=task, backend=desk)
        kw.update(action_pause=0, verify_actions=False)
        return kw

    monkeypatch.setattr(cli_common, "loop_config_kwargs", kwargs)
    monkeypatch.setattr(screen, "prepare_for_model", lambda raw, size, **kw: (raw, size, 1.0))
    monkeypatch.setattr(agent_loop.time, "sleep", lambda s: None)
    return desk, histories


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
