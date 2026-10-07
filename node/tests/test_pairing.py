"""``secdogie-node pair``: two confirmations (the owner on the terminal, the tap
on the page) before anything is enrolled; single use; burned by a "no" or by
repeated bad MACs; the receipt names the resident room."""
from __future__ import annotations

import time

import pytest

pytest.importorskip("nacl")

from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_identity import linkauth as la  # noqa: E402
from secdogie_node import cli  # noqa: E402
from secdogie_node.pairing import PairingOffer, PairingPolicy, Terminal, file_enroller  # noqa: E402

PAGE = ("sha-256 " + ":".join(["AA"] * 32),)
NODE_FPS = ("sha-256 " + ":".join(["BB"] * 32), "sha-512 " + ":".join(["CC"] * 64))
NODE_AS_SEEN = NODE_FPS[:1]


class FakeLink:
    def __init__(self, room):
        self.room, self.link_id = room, 1
        self.local_fingerprints, self.remote_fingerprints = NODE_FPS, PAGE  # the node's view
        self.sent, self.closed, self.extended = [], None, None

    def send_text(self, obj):
        self.sent.append(obj)

    def close(self, reason="", *, delay=0.0):
        self.closed = reason

    def extend(self, seconds):
        self.extended = seconds


def _until(pred, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not pred():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


@pytest.fixture
def world():
    node, app, op = Identity.generate(), Identity.generate(), Identity.generate()
    offer = PairingOffer(ttl=600)
    enrolled = []
    asked = []
    answers = {"code": True, "operator": True}

    def ask(q):
        asked.append(q)
        return answers["operator" if "撤不回" in q else "code"]

    policy = PairingPolicy(node, offer, standing_room=la.derive_room(node), ask=ask,
                           enroll=lambda a, o: enrolled.append((a, o)))
    link = FakeLink(offer.room)
    return dict(node=node, app=app, op=op, offer=offer, policy=policy, link=link, enrolled=enrolled, asked=asked,
                answers=answers)


def _hello(w, *, operator=True, secret=None):
    return la.create_pair_hello(w["app"], secret=secret or w["offer"].secret, node_did=w["node"].did, local=PAGE,
                                remote=NODE_AS_SEEN, operator_did=w["op"].did if operator else "")


def _confirm(w, hello, *, proof=True):
    pid = w["offer"].pairing_id
    op_proof = la.create_operator_proof(w["op"], app_did=w["app"].did, node_did=w["node"].did, pairing=pid) \
        if proof else None
    return la.create_pair_confirm(w["app"], secret=w["offer"].secret, node_did=w["node"].did, hello=hello,
                                  operator_proof=op_proof)


def test_both_confirmations_enroll_and_the_receipt_names_the_resident_room(world):
    w = world
    policy, link = w["policy"], w["link"]
    policy.on_open(link)
    binding = link.sent[0]
    assert la.verify_link_binding(binding, trust=Allowlist({w["node"].did}), room=w["offer"].room,
                                  observed_local=PAGE, observed_remote=NODE_AS_SEEN).ok
    assert link.extended and link.extended > 500
    hello = _hello(w)
    policy.on_text(link, hello)
    _until(lambda: len(w["asked"]) == 2)
    assert la.check_code(hello) in w["asked"][0]  # the terminal shows the very code the page shows
    assert w["enrolled"] == []  # the owner said yes, but the page has not been tapped
    policy.on_text(link, _confirm(w, hello))
    _until(lambda: policy.done.is_set())
    assert w["enrolled"] == [(w["app"].did, w["op"].did)]
    receipt = link.sent[-1]
    r = la.verify_paired(receipt, node_did=w["node"].did, app_did=w["app"].did, pairing=w["offer"].pairing_id,
                         observed_local=PAGE, observed_remote=NODE_AS_SEEN)
    assert r.ok and r.room == la.derive_room(w["node"]) and r.operator_did == w["op"].did
    assert w["offer"].used and not w["offer"].usable()


def test_the_operator_key_is_only_enrolled_when_the_owner_allows_it(world):
    w = world
    w["answers"]["operator"] = False
    w["policy"].on_open(w["link"])
    hello = _hello(w)
    w["policy"].on_text(w["link"], hello)
    w["policy"].on_text(w["link"], _confirm(w, hello))
    _until(lambda: w["policy"].done.is_set())
    assert w["enrolled"] == [(w["app"].did, None)]
    assert w["link"].sent[-1]["operator"] == ""


def test_the_owner_saying_no_refuses_and_burns_the_offer(world):
    w = world
    w["answers"]["code"] = False
    w["policy"].on_open(w["link"])
    hello = _hello(w)
    w["policy"].on_text(w["link"], hello)
    _until(lambda: w["policy"].done.is_set())
    assert w["enrolled"] == [] and w["offer"].burned
    refusal = w["link"].sent[-1]
    assert refusal["type"] == la.REFUSED_TYPE and refusal["reason"] == "pairing-rejected"
    w["policy"].on_text(w["link"], _confirm(w, hello))  # a tap afterwards changes nothing
    assert w["enrolled"] == []


def test_a_tap_alone_is_not_enough(world):
    w = world
    gate = []
    w["policy"].ask = lambda q: gate.append(q) or time.sleep(0.5) or False  # the owner hesitates, then says no
    w["policy"].on_open(w["link"])
    hello = _hello(w)
    w["policy"].on_text(w["link"], hello)
    w["policy"].on_text(w["link"], _confirm(w, hello))
    _until(lambda: w["policy"].done.is_set())
    assert w["enrolled"] == [] and w["policy"].refused == "the owner said no"


def test_bad_macs_burn_the_offer_and_a_burned_offer_refuses(world):
    w = world
    for _ in range(5):
        w["offer"]._last_attempt = 0.0  # skip the rate limit
        w["policy"].on_text(w["link"], _hello(w, secret=b"\x01" * 32))
    assert w["offer"].burned and w["asked"] == []
    w["offer"]._last_attempt = 0.0
    w["policy"].on_text(w["link"], _hello(w))
    assert w["link"].sent[-1]["reason"] == "pairing-unavailable"


def test_an_expired_or_used_offer_refuses():
    node = Identity.generate()
    clock = [1000.0]
    offer = PairingOffer(ttl=60, clock=lambda: clock[0])
    assert offer.usable()
    clock[0] += 61
    assert not offer.usable()
    with pytest.raises(ValueError):
        PairingOffer(ttl=0)
    with pytest.raises(ValueError):
        PairingOffer(ttl=7200)
    link = FakeLink(offer.room)
    policy = PairingPolicy(node, offer, standing_room=la.derive_room(node), ask=lambda q: True,
                           enroll=lambda a, o: None, clock=lambda: clock[0])
    app = Identity.generate()
    hello = la.create_pair_hello(app, secret=offer.secret, node_did=node.did, local=PAGE, remote=NODE_AS_SEEN,
                                 clock=lambda: clock[0])
    policy.on_text(link, hello)
    assert link.sent[-1]["reason"] == "pairing-unavailable"


def test_statements_out_of_turn_close_the_link(world):
    w = world
    hello = _hello(w)
    w["policy"].on_text(w["link"], _confirm(w, hello))
    assert w["link"].closed == "a confirmation out of turn"
    w["link"].closed = None
    w["policy"].on_text(w["link"], {"type": "something else"})
    assert w["link"].closed == "not a pairing statement"


def test_the_link_holds_a_key_and_no_address(world):
    w = world
    url = w["offer"].link("https://ui.example/", w["node"].did)
    inv = la.parse_pairing_fragment(url.split("#", 1)[1])
    assert inv.secret == w["offer"].secret and inv.node_did == w["node"].did


def test_file_enroller(tmp_path):
    apps, ops = tmp_path / "apps.allow", tmp_path / "ops.allow"
    app, op = Identity.generate(), Identity.generate()
    enroll = file_enroller(str(apps), str(ops), pairing_id="ab" * 16)
    enroll(app.did, op.did)
    assert Allowlist.load(apps).dids() == {app.did} and Allowlist.load(ops).dids() == {op.did}
    assert "pairing=abababab" in apps.read_text()
    with pytest.raises(ValueError, match="cannot also be"):
        enroll(op.did, None)  # an operator key offered as an App key
    with pytest.raises(ValueError, match="two files"):
        file_enroller(str(apps), str(apps), pairing_id="x")


def test_the_terminal_talks_on_a_real_pty():
    import os

    master, slave = os.openpty()
    try:
        term = Terminal(os.ttyname(slave))
        term.say("hello")
        os.write(master, b"y\n")
        assert term.ask("ok? [y/N] ") is True
        os.write(master, b"\n")
        assert term.ask("again? [y/N] ") is False  # the default is no
        out = os.read(master, 4096).decode()
        assert "hello" in out and "ok? [y/N]" in out
        term.close()
    finally:
        os.close(master)
        os.close(slave)


def test_no_terminal_no_pairing(tmp_path, monkeypatch, capsys):
    with pytest.raises(OSError):
        Terminal(str(tmp_path / "no-such-tty"))

    def no_tty(*a, **k):
        raise OSError("no tty")

    monkeypatch.setattr("secdogie_node.pairing.Terminal", no_tty)
    key = tmp_path / "node.key"
    Identity.generate().save(key)
    with pytest.raises(SystemExit) as e:
        cli.main(["pair", "--identity", str(key), "--apps", str(tmp_path / "a"), "--operators", str(tmp_path / "o"),
                  "--webrtc-signal", "ws://127.0.0.1:1/ws", "--ui", "http://127.0.0.1:8770/"])
    assert e.value.code == 2
    err = capsys.readouterr()
    assert "needs a terminal" in err.err and "pair=" not in err.out + err.err


def test_webrtc_flags_need_the_signal(tmp_path, capsys):
    with pytest.raises(SystemExit):
        cli.main(["run", "--identity", "x", "--apps", "a", "--operators", "o", "--authorized", "n", "--journal",
                  ":memory:", "--webrtc-origin", "https://ui.example"])
