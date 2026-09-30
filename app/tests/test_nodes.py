"""Pairing with another machine's node: what pasted text is accepted (only
records that verify, one node, an address to reach it), what the book keeps
(and drops when it no longer verifies), and the hub's switching -- one
session per node, opened once, closed when unpaired."""
from __future__ import annotations

import json
import os
import stat
import time

import pytest

pytest.importorskip("nacl")
pytest.importorskip("secdogie_transport")

from secdogie_app.local import OperatorKey  # noqa: E402
from secdogie_app.model import DialogError  # noqa: E402
from secdogie_app.nodes import LOCAL, NodeBook, NodeHub, PairingError, parse_pairing  # noqa: E402
from secdogie_dialogue.app import AppController  # noqa: E402
from secdogie_dialogue.connect import NodeNotFound  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_transport import Endpoint  # noqa: E402
from secdogie_transport.membership import ROLE_RELAY, ROLE_RENDEZVOUS, sign_record  # noqa: E402
from test_model import CHEAP, PASS, FakeSession, challenge, deliver  # noqa: E402

NODE, OTHER, RV, RELAY, APP = (Identity.generate() for _ in range(5))


def record(ident=NODE, endpoints=(("local", "127.0.0.1", 7950),), **kw):
    return sign_record(ident, [Endpoint(*e) for e in endpoints], last_seen=time.time(), **kw)


def ready(ident=NODE, listen="127.0.0.1:7950", rec=None):
    return json.dumps({"event": "ready", "did": ident.did, "listen": listen, "record": rec or record(ident)})


def rv_record():
    return json.dumps(record(RV, (("local", "127.0.0.1", 7000),), roles=[ROLE_RENDEZVOUS]))


def relay_record():
    return json.dumps(record(RELAY, (("local", "127.0.0.1", 7001),), roles=[ROLE_RELAY]))


# ---- parse_pairing --------------------------------------------------------------------------


def test_a_ready_line_pairs_with_the_address_in_its_record():
    node = parse_pairing(ready(), name="  desk  ")
    assert node.did == NODE.did and node.name == "desk" and node.address() == ("127.0.0.1", 7950)


def test_a_node_listening_on_every_interface_needs_its_address_on_a_line():
    text = ready(listen="0.0.0.0:7950", rec=record(endpoints=()))
    with pytest.raises(PairingError, match="地址"):
        parse_pairing(text)
    assert parse_pairing(text + "\n10.0.0.5").address() == ("10.0.0.5", 7950)  # the port from the ready line
    assert parse_pairing(text + "\nvm.local:8000").address() == ("vm.local", 8000)
    node = parse_pairing(text + "\n" + rv_record() + "\n" + relay_record())  # or found by DID at a rendezvous
    assert node.address() is None and len(node.rendezvous) == 1 and len(node.relays) == 1


def test_an_address_given_comes_before_the_records():
    assert parse_pairing(ready() + "\n10.0.0.9:1234").address() == ("10.0.0.9", 1234)


def _tampered():
    rec = record()
    rec["endpoints"][0]["host"] = "203.0.113.9"  # redirected after signing
    return rec


@pytest.mark.parametrize("text, why", [
    ("", "ready"),
    ("not json at all {", "ready"),  # the missing ready line is what to say first
    ("{not json", "JSON"),
    ("[1, 2]", "ready"),
    (ready() + "\nnot an address!", "看不懂"),
    (json.dumps({"hello": 1}), "成员记录"),
    (json.dumps({"event": "ready", "did": NODE.did, "listen": "x", "record": _tampered()}), "签名"),
    (json.dumps({"event": "ready", "did": OTHER.did, "listen": "x", "record": record(NODE)}), "对不上"),
    (ready() + "\n" + ready(OTHER), "不止一个"),
    (relay_record(), "ready"),  # a relay alone is not a node
    (ready(rec=record(roles=[ROLE_RELAY])), "中继"),
    (ready(rec=record(device_class="headless")), "headless"),
    (ready() + "\n" + json.dumps(record(RV, (("local", "127.0.0.1", 7000),))), "不止一个"),
    (ready() + "\n10.0.0.5:1\n10.0.0.6:1", "只要一行"),
])
def test_what_is_refused(text, why):
    with pytest.raises(PairingError, match=why):
        parse_pairing(text)


def test_a_rendezvous_record_must_verify():
    bad = record(RV, (("local", "127.0.0.1", 7000),), roles=[ROLE_RENDEZVOUS])
    bad["endpoints"][0]["port"] = 1
    with pytest.raises(PairingError, match="rendezvous"):
        parse_pairing(ready() + "\n" + json.dumps(bad))


def test_the_window_cannot_pair_with_itself():
    with pytest.raises(PairingError, match="自己"):
        parse_pairing(ready(), refuse={NODE.did})


def test_a_long_or_odd_name_is_cleaned():
    assert parse_pairing(ready(), name="a\x1bb" + "x" * 100).name.startswith("a b")
    assert len(parse_pairing(ready(), name="x" * 100).name) <= 40
    assert parse_pairing(ready()).name  # no name: the DID, shortened


# ---- NodeBook ---------------------------------------------------------------------------------


def test_the_book_keeps_public_records_only_in_a_private_file(tmp_path):
    path = tmp_path / "nodes.json"
    book = NodeBook(path)
    book.add(parse_pairing(ready(), name="desk"))
    book.add(parse_pairing(ready(OTHER, rec=record(OTHER)) + "\n10.0.0.2:9", name="vm"))
    if os.name == "posix":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    again = NodeBook(path)
    assert [(n.name, n.address()) for n in again.nodes()] == [("desk", ("127.0.0.1", 7950)), ("vm", ("10.0.0.2", 9))]
    assert "seed" not in path.read_text(encoding="utf-8")
    again.remove(NODE.did)
    assert [n.did for n in NodeBook(path).nodes()] == [OTHER.did]


def test_an_entry_that_no_longer_verifies_is_dropped(tmp_path):
    path = tmp_path / "nodes.json"
    NodeBook(path).add(parse_pairing(ready(), name="desk"))
    data = json.loads(path.read_text(encoding="utf-8"))
    data["nodes"][0]["record"]["endpoints"][0]["host"] = "203.0.113.9"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert NodeBook(path).nodes() == []


def test_a_missing_or_broken_file_is_an_empty_book(tmp_path):
    assert NodeBook(tmp_path / "none.json").nodes() == []
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    assert NodeBook(tmp_path / "bad.json").nodes() == []


# ---- NodeHub ----------------------------------------------------------------------------------


class FakeLink:
    def __init__(self, did):
        session = FakeSession()
        session.peer_did = did
        self.controller = AppController(session)
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture
def hub(tmp_path):
    opened = []

    def opener(identity, did, **kw):
        if kw.get("node_addr") is None and not kw.get("rendezvous") and not kw.get("relays"):
            raise NodeNotFound("nowhere")
        link = FakeLink(did)
        opened.append((did, kw, link))
        return link

    local = FakeSession()
    local.peer_did = OTHER.did  # this machine's node
    key = OperatorKey(tmp_path / "op.keystore", Allowlist(), kdf=CHEAP)
    h = NodeHub(AppController(local), key, APP, NodeBook(tmp_path / "nodes.json"), opener=opener)
    h.opened = opened
    return h


def test_pairing_opens_one_session_and_switches_to_it(hub):
    hub.pair(ready(), "desk")
    assert hub.current == NODE.did and [d for d, _, _ in hub.opened] == [NODE.did]
    (_, kw, _), = hub.opened
    assert kw["node_addr"] == ("127.0.0.1", 7950) and kw["listen"] == ("0.0.0.0", 0)
    assert hub.choices() == [(LOCAL, "本机"), (NODE.did, "desk")]
    assert hub.model.status().startswith("节点「desk」已连接")
    hub.switch(LOCAL)
    hub.switch(NODE.did)
    assert len(hub.opened) == 1  # opened once; the conversation is kept


def test_pairing_again_replaces_the_session(hub):
    hub.pair(ready(), "desk")
    hub.pair(ready() + "\n10.0.0.7:7950", "desk 2")
    first, second = hub.opened
    assert first[2].closed and not second[2].closed and second[1]["node_addr"] == ("10.0.0.7", 7950)
    assert hub.choices() == [(LOCAL, "本机"), (NODE.did, "desk 2")]


def test_forget_closes_the_session_and_shows_this_machine(hub):
    hub.pair(ready(), "desk")
    hub.forget(NODE.did)
    assert hub.opened[0][2].closed and hub.current == LOCAL and hub.choices() == [(LOCAL, "本机")]


def test_the_hub_refuses_this_machines_node_and_the_window_itself(hub):
    with pytest.raises(DialogError, match="自己"):
        hub.pair(ready(OTHER, rec=record(OTHER)))
    with pytest.raises(DialogError, match="自己"):
        hub.pair(ready(APP, rec=record(APP)))
    assert hub.opened == []


def test_a_node_that_cannot_be_reached_is_said_so(hub):
    node = parse_pairing(ready(listen="0.0.0.0:1", rec=record(endpoints=())) + "\n" + rv_record())
    hub.book.add(node)
    hub._opener = lambda *a, **k: (_ for _ in ()).throw(NodeNotFound("not registered"))
    with pytest.raises(DialogError, match="找不到"):
        hub.switch(NODE.did)
    assert hub.current == LOCAL
    with pytest.raises(DialogError):
        hub.switch("did:key:nobody")


def test_waiting_elsewhere_counts_the_nodes_not_shown(hub):
    hub.pair(ready(), "desk")
    deliver(hub.model.controller, challenge())
    assert hub.waiting_elsewhere() == 0  # it is the one shown
    hub.switch(LOCAL)
    assert hub.waiting_elsewhere() == 1


def test_pairing_info_and_setting_the_passphrase_early(hub):
    app_did, operator = hub.pairing_info()
    assert app_did == APP.did and operator is None
    with pytest.raises(DialogError, match="不一致"):
        hub.set_passphrase(PASS, PASS + "x")
    did = hub.set_passphrase(PASS, PASS)
    assert hub.pairing_info() == (APP.did, did)


def test_close_closes_every_paired_session(hub):
    hub.pair(ready(), "desk")
    hub.close()
    assert hub.opened[0][2].closed


def test_a_relay_record_must_verify():
    bad = record(RELAY, (("local", "127.0.0.1", 7001),), roles=[ROLE_RELAY])
    bad["endpoints"][0]["port"] = 1
    with pytest.raises(PairingError, match="中继"):
        parse_pairing(ready() + "\n" + json.dumps(bad))


@pytest.mark.parametrize("line, why", [
    ("h:0", "1–65535"),
    ("h:70000", "1–65535"),
    ("::1:7950", "看不懂"),  # a bare IPv6 address: which part is the port?
    ("[::1]x", "看不懂"),
    ("h:x", "看不懂"),
])
def test_bad_addresses(line, why):
    with pytest.raises(PairingError, match=why):
        parse_pairing(ready() + "\n" + line)


def test_ipv6_in_brackets():
    assert parse_pairing(ready() + "\n[::1]:7950").address() == ("::1", 7950)
    assert parse_pairing(ready() + "\n[fe80::1]").address() == ("fe80::1", 7950)


def test_waiting_counts_questions_and_notes_too(hub):
    from test_model import memory, question

    hub.pair(ready(), "desk")
    deliver(hub.model.controller, question("p1", "Which folder?"))
    deliver(hub.model.controller, memory())
    hub.switch(LOCAL)
    assert hub.waiting_elsewhere() == 2
