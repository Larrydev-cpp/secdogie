"""The presence wired to a real controller and node, without Qt: a first
meeting card, keys made, the node wakes and says so, rules tried and injected,
and closing takes the node with it. Then the real window, offscreen."""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from secdogie_studio.app import QML_DIR, Presence
from secdogie_studio.controller import EXITED, StudioController
from secdogie_studio.nodeproc import wait_until
from secdogie_studio.presence import Moment

PASS = "correct horse battery"


def _until(presence, pred, timeout=30.0):
    seen = {}

    def check():
        seen.update(presence.tick())
        return pred(seen)

    assert wait_until(check, timeout), seen
    return seen


def test_first_meeting_waking_and_rules_without_qt(tmp_path):
    presence = Presence(StudioController(tmp_path / "p", kdf="min"), idle=lambda: 10.0)
    try:
        s = presence.tick()
        assert s["card"]["kind"] == "welcome" and s["mood"] == "speaking"
        presence.welcome("short", "short")
        assert presence.tick()["feedback"] == "口令至少 8 位。"
        presence.welcome(PASS, PASS + "x")
        assert presence.tick()["feedback"] == "两次输入的不一样。"
        presence.welcome(PASS, PASS)
        presence.controller.wait_idle()
        s = _until(presence, lambda s: s["whisper"] == "我在。")
        assert s["card"] is None and s["statusRows"][0] == ["状态", "醒着"]

        presence.toggle_inner()
        assert presence.rules_text.startswith("# 门的规则")
        presence.try_rules(presence.rules_text, "帮我批量注册十个账号")
        assert presence.tick()["rulesNote"].startswith("我会先问你")
        presence.push_rules(presence.rules_text, PASS)
        _until(presence, lambda s: s["rulesNote"] == "第 1 版规则已经生效。")
        _until(presence, lambda s: "gate-rules 第 1 版" in s["inForce"])
    finally:
        presence.shutdown()
    snap = presence.controller.snapshot()
    assert snap.node_state == EXITED and snap.returncode == 0


# ---- the window, offscreen ---------------------------------------------------------------

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")


class FakeController:
    def __init__(self, initialized=True):
        self.initialized = initialized
        self.profile = None

    def snapshot(self):
        return SimpleNamespace(initialized=self.initialized, busy="", node_state="running", node_did="did:key:z6Mk",
                               app_did="", operator_did="", listen="127.0.0.1:1", roles=("forward",), log=(),
                               returncode=None)

    def ready(self):
        return SimpleNamespace(did="did:key:z6Mk")

    def start_node(self):
        pass

    def shutdown(self):
        pass


class FakeCompanion:
    mesh = "home"

    def __init__(self):
        self.m, self.answers = [], []

    def moments(self):
        return list(self.m)

    def new_whispers(self):
        return []

    def busy(self):
        return False

    def log_lines(self, n=14):
        return []

    def answer(self, mid, text):
        self.answers.append((mid, text))
        self.m = []

    def rules_in_force(self, timeout=3.0):
        return None


@pytest.fixture(scope="module")
def qt():
    QtGui = pytest.importorskip("PySide6.QtGui", exc_type=ImportError)
    pytest.importorskip("PySide6.QtQuick", exc_type=ImportError)
    from PySide6.QtCore import qInstallMessageHandler

    app = QtGui.QGuiApplication.instance() or QtGui.QGuiApplication([])
    messages: list[str] = []
    qInstallMessageHandler(lambda mode, ctx, msg: messages.append(msg))
    return app, messages


def _window(qt, presence):
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine
    from secdogie_studio.app import bridge_class

    bridge = bridge_class()(presence)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("P", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_DIR / "Presence.qml")))
    (root,) = engine.rootObjects()
    return engine, bridge, root


def _pump(qt, bridge, pred, timeout=5.0):
    app, _ = qt

    def step():
        bridge.refresh()
        app.processEvents()
        return pred()

    return wait_until(step, timeout, interval=0.02)


def _qml_problems(messages):
    """What the QML itself got wrong: errors located in a .qml file, script
    errors, binding loops. The platform's own notes (fonts the offscreen
    platform cannot find, alias lookups) are about the test machine, not us."""
    markers = (".qml:", "TypeError", "ReferenceError", "Binding loop", "Unable to assign", "is not defined")
    return [m for m in messages if any(k in m for k in markers)]


def test_the_window_is_a_quiet_light_that_opens_for_a_question(qt):
    from PySide6.QtCore import Qt

    _, messages = qt
    messages.clear()
    comp = FakeCompanion()
    clock = [100.0]
    presence = Presence(FakeController(), idle=lambda: 10.0, clock=lambda: clock[0],
                        companion_factory=lambda profile, ready: comp)
    engine, bridge, root = _window(qt, presence)
    flags = root.flags()
    assert flags & Qt.FramelessWindowHint and flags & Qt.WindowStaysOnTopHint
    assert _pump(qt, bridge, lambda: root.property("shape") in ("compact", "whisper"))
    comp.m = [Moment("probe:1", "ask", "开始之前，我想先弄清楚一点", ("我换个说法", "先不做了"), free_text=True,
                     arrived=clock[0])]
    assert _pump(qt, bridge, lambda: root.property("shape") == "open" and root.width() == 468)
    bridge.answer("probe:1", "先不做了")
    assert comp.answers == [("probe:1", "先不做了")]
    clock[0] += 10
    assert _pump(qt, bridge, lambda: root.property("shape") != "open", timeout=5)
    assert _qml_problems(messages) == []
    del engine


def test_the_problem_filter_catches_qml_errors_and_ignores_platform_notes():
    assert _qml_problems(["file:///x/Presence.qml:12: TypeError: Cannot read property 'c' of undefined",
                          "QFontDatabase: Cannot find font directory C:/x/fonts.",
                          "Populating font family aliases took 66 ms."]) == \
        ["file:///x/Presence.qml:12: TypeError: Cannot read property 'c' of undefined"]


def test_a_cold_start_opens_straight_onto_the_first_meeting(qt):
    _, messages = qt
    messages.clear()
    presence = Presence(FakeController(initialized=False), idle=lambda: 10.0)
    engine, bridge, root = _window(qt, presence)
    assert _pump(qt, bridge, lambda: root.property("shape") == "open")
    assert presence.tick()["card"]["kind"] == "welcome"
    assert _qml_problems(messages) == []
    del engine
