"""The one window driving a node on another machine, end to end: the window's
own local backend, and a second real ``Node`` (the "other machine"), both on
127.0.0.1 over real UDP. The production runner and the real agent loop run the
goal; only the model and the desktop are stand-ins (``node/tests/fakes.py``).

  1. Pairing: the passphrase is set first (the other node needs the operator
     DID), the ready line is pasted, and the window switches to that node.
  2. A goal there: its high-risk step is approved in the window with the same
     passphrase, signed for that node, and runs there.
  3. Switching back to this machine keeps this machine's conversation.
  4. Through a rendezvous: the ready line has no address; the window finds the
     node by its DID.
  5. A node that does not trust this operator refuses the signed approval: the
     step does not run.
"""
from __future__ import annotations

import json
import threading
import time

import pytest

pytest.importorskip("nacl")
pytest.importorskip("secdogie_agent")

import fakes  # noqa: E402
from nacl import pwhash  # noqa: E402
from secdogie_agent import cli_common  # noqa: E402
from secdogie_app.local import LocalBackend  # noqa: E402
from secdogie_app.model import APPROVAL, OPEN, PROBE  # noqa: E402
from secdogie_app.nodes import LOCAL, NodeBook, NodeHub  # noqa: E402
from secdogie_citadel.episodes import episodes_from_events  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_identity.capability import create_capability  # noqa: E402
from secdogie_node import Node, NodeConfig  # noqa: E402
from secdogie_transport import DirectUDPTransport, Endpoint, RendezvousService, UDPChannel  # noqa: E402
from secdogie_transport.membership import ROLE_RENDEZVOUS, sign_record  # noqa: E402

CHEAP = {"opslimit": pwhash.argon2id.OPSLIMIT_MIN, "memlimit": pwhash.argon2id.MEMLIMIT_MIN}
PASS = "correct horse"
DELETE = [{"action": "key", "keys": ["delete"], "rollback": "restore it from the Trash"},
          {"action": "done", "text": "deleted"}]


def until(model, pick, timeout=30.0):
    tick = threading.Event()  # not time.sleep: the stand-ins make it a no-op
    deadline = time.time() + timeout
    while time.time() < deadline:
        model.refresh()
        got = pick(model.messages())
        if got:
            return got
        tick.wait(0.05)
    raise AssertionError(f"timed out; the window shows: {[(m.kind, m.text, m.state) for m in model.messages()]}")


def open_card(kind):
    return lambda msgs: next((m for m in msgs if m.kind == kind and m.state == OPEN), None)


def said(text):
    return lambda msgs: next((m for m in msgs if text in m.text), None)


@pytest.fixture
def world(monkeypatch, tmp_path):
    desk, histories = fakes.install(monkeypatch.setattr)
    plans = [list(fakes.FILE_THE_REPORT), list(DELETE), list(DELETE)]
    monkeypatch.setattr(cli_common, "resolve_provider", lambda args, prog: fakes.Scripted(plans.pop(0), histories))
    backend = LocalBackend(tmp_path / "home", kdf=CHEAP, run_task=lambda *a, **k: (0, "done here"))
    hub = NodeHub(backend.start(), backend.operator_key, backend.app_identity,
                  NodeBook(tmp_path / "home" / "nodes.json"), listen=("127.0.0.1", 0))
    nodes, channels = [], []

    def other_machine(me=None, *, trust_operator=True, **kw):
        """A node on "another machine": it trusts this window's App DID and, if
        told to, its operator DID -- what that machine's admin configures."""
        me, issuer = me or Identity.generate(), Identity.generate()
        operators = Allowlist({hub.pairing_info()[1]} if trust_operator else set())
        node = Node(NodeConfig(
            identity=me, apps=Allowlist({backend.app_identity.did}), operators=operators,
            authorized=Allowlist({me.did}), mesh=Allowlist({me.did}), issuers=Allowlist({issuer.did}),
            journal_path=str(tmp_path / f"{me.did[-8:]}.db"), candidates_path=str(tmp_path / f"{me.did[-8:]}.mem"),
            challenge_ttl=20.0, probe_ttl=20.0, idle_poll=0.1, **kw))
        node.supervisor.add_grant(create_capability(issuer, me.did, ["physical.click", "physical.key"], ttl=3600))
        node.start()
        nodes.append(node)
        return node

    def ready(node, record=None):
        host, port = node.address
        return json.dumps({"event": "ready", "did": node.identity.did, "listen": f"{host}:{port}",
                           "record": record or node.record()})

    def rendezvous(allow):
        rv = Identity.generate()
        ch = UDPChannel("127.0.0.1", 0)
        channels.append(ch)
        RendezvousService(DirectUDPTransport(rv, ch, allowlist=allow), allowlist=allow)
        return sign_record(rv, [Endpoint("local", *ch.address)], last_seen=time.time(), roles=[ROLE_RENDEZVOUS])

    hub.set_passphrase(PASS, PASS)  # 1. first: the other machine needs the operator DID
    yield dict(desk=desk, backend=backend, hub=hub, other_machine=other_machine, ready=ready,
               rendezvous=rendezvous, plans=plans)
    hub.close()
    for node in nodes:
        node.stop()
    backend.stop()
    for ch in channels:
        ch.close()


def test_a_goal_on_another_machine_approved_from_this_window(world):
    hub, desk = world["hub"], world["desk"]
    local = hub.model
    local.send("hello")
    until(local, said("finished: exit 0"))  # this machine's own conversation
    vm = world["other_machine"]()
    hub.pair(world["ready"](vm), "vm")
    assert hub.current == vm.identity.did and hub.model is not local
    model = hub.model
    until(model, lambda msgs: True)
    model.send("file the report")
    card = until(model, open_card(APPROVAL))
    model.approve(card.ref, PASS)  # the same passphrase; signed for this node only
    probe = until(model, open_card(PROBE))
    assert "folder" in probe.text
    model.send("Desktop")
    until(model, said("finished: exit 0"))
    episodes = list(episodes_from_events(vm.journal.events()).values())
    assert episodes[0].steps[1].outcome == "ok" and "key" in desk.done  # it ran on the other machine
    hub.switch(LOCAL)
    assert any("hello" == m.text for m in hub.model.messages())  # this machine's conversation is still there
    assert hub.model.status().startswith("本机节点")


def test_found_through_a_rendezvous_by_did(world):
    hub = world["hub"]
    vm_id = Identity.generate()
    rv = world["rendezvous"](Allowlist({world["backend"].app_identity.did, vm_id.did}))
    vm = world["other_machine"](vm_id, rendezvous_records=[rv])
    assert _wait(lambda: vm.rendezvous.reflexive)  # registered
    bare = sign_record(vm.identity, [], last_seen=time.time())  # a ready line with no address in it
    hub.pair(world["ready"](vm, record=bare) + "\n" + json.dumps(rv), "behind NAT")
    world["plans"][:] = [list(DELETE)]
    model = hub.model
    model.send("delete it")
    card = until(model, open_card(APPROVAL))  # reached: through the address the rendezvous gave
    model.deny(card.ref)
    until(model, said("finished"))


def test_a_node_that_does_not_trust_this_operator_runs_nothing(world):
    hub, desk = world["hub"], world["desk"]
    world["plans"][:] = [list(DELETE)]
    vm = world["other_machine"](trust_operator=False)
    hub.pair(world["ready"](vm), "stranger")
    model = hub.model
    model.send("delete it")
    card = until(model, open_card(APPROVAL))
    model.approve(card.ref, PASS)  # signed, sent -- and refused by that node
    until(model, said("finished"))
    (episode,) = episodes_from_events(vm.journal.events()).values()
    refused = episode.steps[0]
    assert refused.outcome == "rejected" and "operator not trusted" in refused.result
    assert "key" not in desk.done


def _wait(pred, timeout=10.0):
    tick = threading.Event()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        tick.wait(0.05)
    return pred()
