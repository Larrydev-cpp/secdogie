"""The window itself, on a real Tk (run under Xvfb in CI; skipped where there
is no tkinter or no display): the key card on the first run, a goal typed and
sent, every card's buttons reaching the model, the passphrase fields emptied,
stop, and close. The model underneath is the real one over a fake session, as
in ``test_model.py``."""
from __future__ import annotations

import gc
import json
import os
import threading
import time

import pytest

tk = pytest.importorskip("tkinter")
pytest.importorskip("nacl")
pytest.importorskip("secdogie_agent")
if os.name == "posix" and not os.environ.get("DISPLAY"):
    pytest.skip("no display (CI runs this under xvfb-run)", allow_module_level=True)

from secdogie_app.local import OperatorKey  # noqa: E402
from secdogie_app.model import APPROVAL, MEMORY, PROBE  # noqa: E402
from secdogie_app.nodes import NodeBook, NodeHub  # noqa: E402
from secdogie_app.window import Window  # noqa: E402
from secdogie_dialogue.app import AppController  # noqa: E402
from secdogie_dialogue.protocol import (  # noqa: E402
    ControlOp,
    ControlPacket,
    DialoguePacket,
    Gate2ResponsePacket,
    Verdict,
)
from secdogie_identity import Allowlist  # noqa: E402
from test_model import (  # noqa: E402
    APP,
    CHEAP,
    PASS,
    Clock,
    FakeKeys,
    FakeSession,
    challenge,
    deliver,
    memory,
    question,
)


@pytest.fixture(autouse=True)
def tk_garbage_on_this_thread():
    """Closed windows' Tk objects are freed here, on the main thread: freed by
    the cyclic collector on another thread (a node's), Tcl aborts."""
    gc.collect()
    yield
    gc.collect()


@pytest.fixture
def win(tmp_path):
    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"no display: {e}")
    clock = Clock()
    session = FakeSession()
    ctl = AppController(session, clock=clock)
    keys = FakeKeys()
    key = OperatorKey(tmp_path / "operator.keystore", Allowlist(), kdf=CHEAP)
    hub = NodeHub(ctl, key, APP, NodeBook(tmp_path / "nodes.json"), api_keys=keys, clock=clock)
    closed = []
    w = Window(root, hub, on_close=lambda: closed.append(True), clock=clock)
    w.test = dict(ctl=ctl, session=session, keys=keys, clock=clock, closed=closed)
    yield w
    if not w._closed:
        w.close()


def pump(w, until=lambda: True, timeout=10.0):
    deadline = time.time() + timeout
    while True:
        w.pump()
        w.root.update()
        if until() or time.time() > deadline:
            return until()
        threading.Event().wait(0.02)


def card(w, kind):
    return next(c for c in w.cards.values() if c.msg.kind == kind)


def test_the_first_run_asks_for_the_api_key_before_anything_else(win):
    assert win.need_key and win.key_panel.winfo_manager() == "pack"
    assert str(win.composer.cget("state")) == "disabled" and not win.send_button.enabled
    win.key_entry.insert(0, "sk-1")
    win.save_key()
    assert "too short" in win.key_status.cget("text") and win.test["keys"].saved == []
    assert win.key_entry.get() == "sk-1"  # kept, to be fixed
    win.key_entry.delete(0, "end")
    win.key_entry.insert(0, "sk-ant-0123456789")
    win.model_entry.insert(0, "claude-x")
    win.provider.set("anthropic")
    win.save_key()
    assert win.test["keys"].saved == [("sk-ant-0123456789", "anthropic", "claude-x")]
    assert win.key_entry.get() == "" and not win.need_key
    assert str(win.composer.cget("state")) == "normal" and win.send_button.enabled


def _ready(win):
    win.key_entry.insert(0, "sk-ant-0123456789")
    win.save_key()


def test_a_goal_typed_and_sent(win):
    _ready(win)
    win.composer.insert(0, "file the report")
    win.send()
    (pkt,) = win.test["session"].of(ControlPacket)
    assert pkt.op is ControlOp.ADD_GOAL and pkt.title == "file the report"
    assert win.composer.get() == ""
    assert [c.body.cget("text") for c in win.cards.values()] == ["file the report"]
    assert win.stop_button.enabled
    win.stop()
    assert win.test["session"].of(ControlPacket)[-1].op is ControlOp.STOP
    pump(win)
    assert not win.stop_button.enabled


def test_an_empty_send_says_so(win):
    _ready(win)
    win.send()
    assert win.error.cget("text") and win.test["session"].sent == []


def test_a_question_card_answers_by_option(win):
    _ready(win)
    deliver(win.test["ctl"], question("p1", "Which folder?", ("Downloads", "Desktop")))
    pump(win)
    c = card(win, PROBE)
    assert "Which folder?" in c.body.cget("text") and "Which folder?" in win.hint.cget("text")
    c.buttons[1].invoke()
    (ans,) = win.test["session"].of(DialoguePacket)
    assert ans.content == "Desktop"
    assert c.controls is None and c.state.cget("text") == "已回答"


def test_an_approval_card_sets_the_passphrase_then_signs(win, tmp_path):
    _ready(win)
    deliver(win.test["ctl"], challenge())
    pump(win)
    c = card(win, APPROVAL)
    assert c.pass1 is not None and c.pass2 is not None  # first time: set it, twice
    c.pass1.insert(0, PASS)
    c.pass2.insert(0, PASS + "!")
    win.approve(c)
    assert c.pass1.get() == "" and c.pass2.get() == ""  # emptied as soon as read
    assert pump(win, lambda: "不一致" in c.error.cget("text"))
    assert not (tmp_path / "operator.keystore").exists()
    c.pass1.insert(0, PASS)
    c.pass2.insert(0, PASS)
    win.approve(c)
    assert pump(win, lambda: c.msg.state == "approved")
    (resp,) = win.test["session"].of(Gate2ResponsePacket)
    assert resp.user_verdict is Verdict.APPROVE
    assert "口令已设置" in win.status.cget("text")
    # the next card asks for the passphrase once
    deliver(win.test["ctl"], challenge(cid="ch2"))
    pump(win)
    c2 = [x for x in win.cards.values() if x.msg.kind == APPROVAL][-1]
    assert c2.pass1 is not None and c2.pass2 is None
    c2.buttons[-1].invoke()  # deny
    assert win.test["session"].of(Gate2ResponsePacket)[-1].user_verdict is Verdict.DENY
    assert c2.state.cget("text") == "已拒绝"


def test_an_unsignable_approval_card_offers_only_deny(win):
    _ready(win)
    deliver(win.test["ctl"], challenge(claimed="00" * 32))
    pump(win)
    c = card(win, APPROVAL)
    assert c.pass1 is None and c.pass2 is None and len(c.buttons) == 1
    assert any("不一致" in d.cget("text") for d in c.details)
    c.buttons[0].invoke()
    assert win.test["session"].of(Gate2ResponsePacket)[0].user_verdict is Verdict.DENY


def test_an_open_card_asks_once_for_the_passphrase_after_it_is_set(win):
    _ready(win)
    deliver(win.test["ctl"], challenge(cid="ch1"))
    deliver(win.test["ctl"], challenge(cid="ch2"))
    pump(win)
    first, second = [c for c in win.cards.values() if c.msg.kind == APPROVAL]
    assert second.pass2 is not None
    first.pass1.insert(0, PASS)
    first.pass2.insert(0, PASS)
    win.approve(first)
    assert pump(win, lambda: first.msg.state == "approved")
    assert second.pass1 is not None and second.pass2 is None


def test_an_open_approval_counts_down(win):
    _ready(win)
    deliver(win.test["ctl"], challenge(expires=1100.0))
    pump(win)
    c = card(win, APPROVAL)
    assert c.countdown.cget("text").startswith("100 ")
    win.test["clock"].t = 1101.0
    pump(win)
    assert c.controls is None and "没有批准" in c.state.cget("text")


def test_memory_cards(win):
    _ready(win)
    deliver(win.test["ctl"], memory())
    pump(win)
    c = card(win, MEMORY)
    c.buttons[0].invoke()
    (pkt,) = win.test["session"].of(ControlPacket)
    assert pkt.op is ControlOp.CONFIRM_MEMORY and c.state.cget("text") == "已记住"


def test_close_stops_the_backend(win):
    win.close()
    assert win.test["closed"] == [True]
    win.close()  # once
    assert win.test["closed"] == [True]


def test_main_opens_the_window_on_a_real_local_node_and_closes_cleanly(monkeypatch, tmp_path):
    """``secdogie`` itself: the local node starts, the window opens (asking for
    the key: none is configured here), and closing it stops the node."""
    from secdogie_app import window as window_mod

    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SECDOGIE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    seen = {}
    real_run = window_mod.Window.run

    def run(self):
        seen["need_key"] = self.need_key
        seen["status"] = self.status.cget("text")
        self.root.after(300, self.close)
        real_run(self)

    monkeypatch.setattr(window_mod.Window, "run", run)
    assert window_mod.main([]) == 0
    assert seen == {"need_key": True, "status": "本机节点已连接 · 口令未设置"}
    assert (tmp_path / "home" / "node.key").exists()
    assert window_mod.main([]) == 0  # the lock was released: it opens again


# ---- the structural view, and other machines' nodes ------------------------------------------


def test_the_view_fold_shows_the_structure_the_node_sent(win):
    from secdogie_dialogue.protocol import NodeDelta, NodeOp, StateSnapshotPacket

    _ready(win)
    assert win.view_panel.winfo_manager() == ""  # folded by default
    deliver(win.test["ctl"], StateSnapshotPacket(1, 7, 1, (
        NodeDelta(NodeOp.ADD, 0, role="AXWindow", name="Drawing"),
        NodeDelta(NodeOp.ADD, 1, parent_index=0, role="AXButton", name="Save\x1b"),
    ), focused_node_index=1, full=True))
    win.toggle_view()
    pump(win)
    assert win.view_panel.winfo_manager() == "pack" and "▾" in win.view_button.cget("text")
    text = win.view_text.get("1.0", "end")
    assert 'AXWindow "Drawing"' in text and "\x1b" not in text
    focused = win.view_text.tag_ranges("focus")
    assert focused and "★" in win.view_text.get(focused[0], focused[1])
    assert str(win.view_text.cget("state")) == "disabled"  # read-only
    win.toggle_view()
    assert win.view_panel.winfo_manager() == ""


def _other_node_ready():
    from secdogie_identity import Identity
    from secdogie_transport import Endpoint
    from secdogie_transport.membership import sign_record

    node = Identity.generate()
    record = sign_record(node, [Endpoint("local", "127.0.0.1", 7950)], last_seen=time.time())
    return node, json.dumps({"event": "ready", "did": node.did, "listen": "127.0.0.1:7950", "record": record})


class _Link:
    def __init__(self, did, clock):
        s = FakeSession()
        s.peer_did = did
        self.session, self.controller, self.closed = s, AppController(s, clock=clock), False

    def close(self):
        self.closed = True


def test_pairing_from_the_window_and_switching_back_and_forth(win):
    _ready(win)
    links = []
    win.hub._opener = lambda identity, did, **kw: links.append(_Link(did, win.test["clock"])) or links[-1]
    win.composer.insert(0, "here")
    win.send()
    win.show_pair_card()
    assert win.pair_panel.winfo_manager() == "pack" and win.pair_pass1 is not None  # no operator key yet
    win.pair_pass1.insert(0, PASS)
    win.pair_pass2.insert(0, PASS)
    win.set_passphrase_now()
    assert win.hub.key.is_set and win.pair_pass1 is None  # now it shows the operator DID to copy
    node, line = _other_node_ready()
    win.pair_text.insert("1.0", "not a node")
    win.pair_now()
    assert "ready" in win.pair_status.cget("text") and links == []
    win.pair_text.delete("1.0", "end")
    win.pair_text.insert("1.0", line)
    win.pair_name.insert(0, "vm")
    win.pair_now()
    assert win.pair_panel.winfo_manager() == "" and win.hub.current == node.did
    assert win.cards == {} and "vm" in win.switcher.cget("text")  # the other node's (empty) conversation
    assert "节点「vm」" in win.status.cget("text")
    deliver(links[0].controller, challenge())
    pump(win)
    win.switch("local")
    assert [c.body.cget("text") for c in win.cards.values()] == ["here"]  # this machine's, kept
    assert "其他节点 1 件待处理" in win.switcher.cget("text")
    win.switch(node.did)
    assert len(links) == 1 and card(win, APPROVAL)
    win.forget(node.did)
    assert links[0].closed and win.hub.current == "local" and win.hub.choices() == [("local", "本机")]


def test_the_switcher_menu_lists_the_nodes(win, monkeypatch):
    _ready(win)
    shown = []
    monkeypatch.setattr(tk.Menu, "tk_popup", lambda self, x, y: shown.append(
        [self.entrycget(i, "label") for i in range(self.index("end") + 1) if self.type(i) == "command"]))
    win.show_switcher()
    assert shown == [["• 本机", "添加远程节点…"]]


def test_a_node_that_cannot_be_reached_leaves_the_window_where_it_was(win):
    _ready(win)
    node, line = _other_node_ready()
    from secdogie_app.nodes import parse_pairing
    from secdogie_dialogue.connect import NodeNotFound

    win.hub.book.add(parse_pairing(line, name="gone"))

    def unreachable(*a, **k):
        raise NodeNotFound("not registered")

    win.hub._opener = unreachable
    assert not win.switch(node.did)
    assert "找不到" in win.error.cget("text") and win.hub.current == "local"


def test_the_view_is_redrawn_only_when_it_changed(win, monkeypatch):
    from secdogie_dialogue.protocol import NodeDelta, NodeOp, StateSnapshotPacket

    _ready(win)
    deliver(win.test["ctl"], StateSnapshotPacket(1, 7, 1, (NodeDelta(NodeOp.ADD, 0, role="AXWindow", name="A"),),
                                                 full=True))
    win.toggle_view()
    pump(win)
    redraws = []
    real = win.view_text.delete
    monkeypatch.setattr(win.view_text, "delete", lambda *a: redraws.append(a) or real(*a))
    from test_model import status

    for i in range(3):
        deliver(win.test["ctl"], status(f"working {i}"))  # something else changed; the view did not
        pump(win)
    assert redraws == []


def test_an_approval_that_expired_on_a_node_not_shown_still_shows_how_it_ended(win):
    _ready(win)
    links = []
    win.hub._opener = lambda identity, did, **kw: links.append(_Link(did, win.test["clock"])) or links[-1]
    node, line = _other_node_ready()
    win.hub.pair(line, "vm")
    win.switch("local")
    deliver(links[0].controller, challenge(expires=1010.0))
    pump(win)  # the node not shown is caught up too
    win.test["clock"].t = 1011.0
    pump(win)
    win.switch(node.did)
    assert "没有批准" in card(win, APPROVAL).state.cget("text")
