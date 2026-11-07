"""``admin/v1``: the operator's App asks the node how it is, over real UDP on
127.0.0.1, and nobody else gets an answer."""
from __future__ import annotations

import time

import pytest

pytest.importorskip("nacl")

from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_node import Node, NodeConfig  # noqa: E402
from secdogie_node.admin import ADMIN_CHANNEL, AdminClient, goal_counts, recent_goals  # noqa: E402
from secdogie_transport import ChannelMux, DirectUDPTransport, Endpoint, PeerIdentity, Session, UDPChannel  # noqa: E402

NODE, APP, OPERATOR = (Identity.generate() for _ in range(3))


def _node(tmp_path, apps):
    return Node(NodeConfig(
        identity=NODE, apps=Allowlist(apps), operators=Allowlist({OPERATOR.did}),
        authorized=Allowlist({NODE.did}), unrestricted=True, run_task=lambda task, **_: (0, "done"),
        journal_path=str(tmp_path / "node.db"), candidates_path=str(tmp_path / "memory.db"),
        idle_poll=0.05, mesh="home", roles=("forward", "serve"),
    ))


def _client(who, node_addr):
    channel = UDPChannel("127.0.0.1", 0)
    tr = DirectUDPTransport(who, channel, allowlist=Allowlist({NODE.did}))
    tr.set_peer_endpoint(NODE.did, *node_addr)
    mux = ChannelMux(tr, Session("app", PeerIdentity(who.did, ""), active=Endpoint("local", *channel.address)))
    return AdminClient(mux, NODE.did), channel


def test_the_app_learns_who_the_node_is_and_what_it_is_doing(tmp_path):
    node = _node(tmp_path, {APP.did})
    before = int(time.time())
    node.start()
    client, channel = _client(APP, node.address)
    try:
        node.supervisor.add_goal("g-1", title="整理周报")
        node.supervisor.add_goal("g-2", title="备份照片")
        status = client.status(timeout=10)
        assert status is not None
        assert (status["node"], status["mesh"], status["roles"]) == (NODE.did, "home", ["forward", "serve"])
        assert status["listen"] == "{}:{}".format(*node.address) and status["carriers"] == ["udp"]
        assert (status["relays"], status["rendezvous"], status["halted"]) == (0, 0, False)
        assert status["since"] >= before
        assert sum(status["goals"].values()) == 2
        assert [g["title"] for g in status["recent"]] == ["备份照片", "整理周报"]  # newest first
        assert client.request("reboot", timeout=10)["errors"] == ["op:op"]
    finally:
        channel.close()
        node.stop()


def test_a_stranger_gets_no_answer(tmp_path):
    stranger = Identity.generate()
    node = _node(tmp_path, {APP.did})
    node.start()
    client, channel = _client(stranger, node.address)
    try:
        assert client.status(timeout=1.0) is None
    finally:
        channel.close()
        node.stop()


def test_counts_and_recent_goals():
    class G:
        def __init__(self, i, status):
            self.id, self.title, self.status = f"g-{i}", f"t{i}", status

    goals = [G(i, s) for i, s in enumerate(["done", "done", "failed", "active", "pending", "pending", "pending"])]
    assert goal_counts(goals) == {"pending": 3, "active": 1, "done": 2, "failed": 1}
    assert [g["id"] for g in recent_goals(goals, 3)] == ["g-6", "g-5", "g-4"]
    assert ADMIN_CHANNEL == "admin/v1"
