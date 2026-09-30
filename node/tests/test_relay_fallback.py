"""Relay fallback for the operator dialogue: the App cannot reach the node
directly (a dead address), yet a goal is submitted, runs, and is reported --
every frame carried by a relay both lease with, over real UDP on 127.0.0.1."""
from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from secdogie_dialogue.app import AppController, run_script  # noqa: E402
from secdogie_dialogue.session import DialogueSession, SessionRouter  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_node import Node, NodeConfig  # noqa: E402
from secdogie_transport import (  # noqa: E402
    ChannelMux,
    DirectUDPTransport,
    Endpoint,
    FailoverTransport,
    PeerIdentity,
    RelayService,
    Session,
    UDPChannel,
)
from secdogie_transport.membership import ROLE_RELAY, sign_record  # noqa: E402

NODE, APP, OPERATOR, RELAY = (Identity.generate() for _ in range(4))


def test_the_dialogue_survives_a_blocked_direct_path(tmp_path):
    relay_ch = UDPChannel("127.0.0.1", 0)
    relay = RelayService(DirectUDPTransport(RELAY, relay_ch, allowlist=Allowlist({NODE.did, APP.did})),
                         allowlist=Allowlist({NODE.did, APP.did}))
    record = sign_record(RELAY, [Endpoint("local", *relay_ch.address)], last_seen=1.0, roles=[ROLE_RELAY])

    ran = []

    def task(t, *, should_stop, on_status, confirm, record_step=None, **_):
        ran.append(t)
        return 0, "done"

    node = Node(NodeConfig(identity=NODE, apps=Allowlist({APP.did}), operators=Allowlist({OPERATOR.did}),
                           authorized=Allowlist({NODE.did}), mesh=Allowlist({NODE.did}), run_task=task, idle_poll=0.05,
                           journal_path=str(tmp_path / "node.db"), relay_records=[record]))
    node.start()

    dead = UDPChannel("127.0.0.1", 0)  # the address the App believes the node has; nothing answers there
    app_ch = UDPChannel("127.0.0.1", 0)
    trust = Allowlist({NODE.did})
    direct = DirectUDPTransport(APP, app_ch, allowlist=trust)
    direct.set_peer_endpoint(NODE.did, *dead.address)
    link = FailoverTransport.from_records(direct, [record])
    link.start()
    mux = ChannelMux(link, Session("app", PeerIdentity(APP.did, ""), active=Endpoint("local", *app_ch.address)))
    router = SessionRouter(mux)
    session = router.add(DialogueSession(APP, NODE.did, router.sender_for(NODE.did), trust=trust))
    ctl = AppController(session)
    session.start(0.05)
    ctl.start()
    out: list = []
    try:
        rc = run_script(ctl, [
            {"op": "add_goal", "title": "file the report", "goal_id": "g1"},
            {"op": "expect_status", "match": "goal g1 finished: exit 0"},
        ], emit=out.append, default_timeout=20)
        assert rc == 0, out
        assert ran == ["file the report"]
        assert relay.stats["forwarded"] >= 4  # both directions went through the relay
        assert not link.direct_is_fresh(NODE.did)  # the App never heard the node directly
    finally:
        ctl.close()
        link.close()
        node.stop()
        for ch in (app_ch, dead, relay_ch):
            ch.close()


def test_a_bad_relay_record_is_refused():
    with pytest.raises(ValueError, match="relay record"):
        Node(NodeConfig(identity=NODE, apps=Allowlist({APP.did}), operators=Allowlist({OPERATOR.did}),
                        authorized=Allowlist({NODE.did}), mesh=Allowlist({NODE.did}),
                        relay_records=[sign_record(RELAY, [Endpoint("local", "127.0.0.1", 9)], last_seen=1.0)]))
