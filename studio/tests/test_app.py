"""The presence wired to a real controller and node, without Qt: a first
meeting card, keys made, the node wakes and says so (and tells the home window
how it is), rules tried and injected, and closing takes the node with it. Then
the real windows, offscreen: the home window at launch, the light once it is
closed."""
from __future__ import annotations

import os
import time
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
        assert s["onboarding"] == "form" and s["keys"] == {} and s["greeting"]
        presence.welcome("short", "short")
        assert presence.tick()["feedback"] == "口令至少要 8 位，再长一点。"
        presence.welcome(PASS, PASS + "x")
        assert presence.tick()["feedback"] == "两次输入的不一样，再输一次。"
        presence.welcome(PASS, PASS, "  小王\n")
        assert presence.tick()["onboarding"] in ("making", "ready")  # the keys are being made
        presence.controller.wait_idle()
        s = _until(presence, lambda s: s["onboarding"] == "ready")
        assert s["you"] == "小王" and presence.controller.profile.name() == "小王"
        assert s["keys"]["operator"].startswith("did:key:") and s["keys"]["node"] == s["node"]["did"]
        presence.finish_onboarding()
        assert presence.tick()["onboarding"] == ""  # for good
        s = _until(presence, lambda s: s["whisper"] == "我在。")
        assert s["card"] is None and s["statusRows"][0] == ["状态", "醒着"]
        assert s["homeOpen"] and s["homeCard"] is None and s["conversation"] == []
        s = _until(presence, lambda s: s["node"]["answered"])  # the node answered on admin/v1
        node = s["node"]
        assert (node["stateWords"], node["mesh"], node["roles"]) == ("醒着", "home", "转发")
        assert node["did"] == presence.controller.snapshot().node_did and node["appConnected"]
        assert node["work"] == "还没有交给我的事。" and node["since"].startswith("醒了 ")

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

    def conversation(self, n=200):
        return [{"who": "you", "text": "整理周报", "tone": "", "key": "s0"}]

    def node_status(self, timeout=3.0):
        return {"mesh": "home", "roles": ["forward"], "goals": {"active": 1}, "recent": [], "since": 0,
                "app": {"connected": True}}

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


def _windows(qt, presence, names=("Presence.qml", "Home.qml"), *, still=False):
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine
    from secdogie_studio.app import bridge_class

    bridge = bridge_class()(presence, still)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("P", bridge)
    for name in names:
        engine.load(QUrl.fromLocalFile(str(QML_DIR / name)))
    roots = engine.rootObjects()
    assert len(roots) == len(names)
    return engine, bridge, roots


def _window(qt, presence):
    engine, bridge, (root,) = _windows(qt, presence, ("Presence.qml",))
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
    presence.close_home()  # the light is what is left once the home window is closed
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


def test_studio_opens_on_the_home_window_and_closing_it_leaves_the_light(qt):
    _, messages = qt
    messages.clear()
    comp = FakeCompanion()
    clock = [100.0]
    presence = Presence(FakeController(), idle=lambda: 10.0, clock=lambda: clock[0],
                        companion_factory=lambda profile, ready: comp)
    engine, bridge, (light, home) = _windows(qt, presence)
    assert _pump(qt, bridge, lambda: home.isVisible() and not light.isVisible())
    assert _pump(qt, bridge, lambda: presence.tick()["node"]["work"] == "正在做 1 件")
    # what waits on the person shows in the home window while it is open
    comp.m = [Moment("probe:1", "ask", "开始之前，我想先弄清楚一点", ("我换个说法", "先不做了"), free_text=True,
                     arrived=clock[0])]
    assert _pump(qt, bridge, lambda: presence.tick()["homeCardKey"] == "probe:1")
    bridge.closeHome()                                   # the window's close button
    assert _pump(qt, bridge, lambda: light.isVisible() and not home.isVisible())
    bridge.openHome()                                    # a click on the light
    assert _pump(qt, bridge, lambda: home.isVisible() and not light.isVisible())
    home.close()                                         # closing it never quits: it leaves the light
    assert _pump(qt, bridge, lambda: light.isVisible() and not home.isVisible())
    assert _qml_problems(messages) == []
    del engine


def test_a_cold_start_opens_the_home_window_on_the_first_meeting(qt):
    _, messages = qt
    messages.clear()
    presence = Presence(FakeController(initialized=False), idle=lambda: 10.0)
    engine, bridge, (light, home) = _windows(qt, presence)
    assert _pump(qt, bridge, lambda: home.isVisible() and home.property("first"))
    assert presence.tick()["homeCard"]["kind"] == "welcome"
    bridge.closeHome()                                   # closed before the keys exist: the light says hello
    assert _pump(qt, bridge, lambda: light.isVisible() and light.property("shape") == "open")
    assert _qml_problems(messages) == []
    del engine


def test_a_still_light_for_someone_who_asked_for_less_motion(qt):
    _, messages = qt
    messages.clear()
    presence = Presence(FakeController(initialized=False), idle=lambda: 10.0)
    engine, bridge, (light, home) = _windows(qt, presence, still=True)
    assert bridge.property("still") is True
    assert _pump(qt, bridge, lambda: home.isVisible() and home.property("first"))
    assert _qml_problems(messages) == []
    del engine


def test_the_ink_switch_changes_the_world_and_back(qt):
    """墨 / 宣纸: the switch floods the window and the theme flips under it
    (for someone who asked for less motion, a short fade)."""
    from PySide6.QtCore import QMetaObject, QObject

    _, messages = qt
    messages.clear()
    presence = Presence(FakeController(), idle=lambda: 10.0, companion_factory=lambda profile, ready: FakeCompanion())
    engine, bridge, (light, home) = _windows(qt, presence, still=True)
    assert _pump(qt, bridge, lambda: home.isVisible())
    switch = home.findChild(QObject, "themeSwitch")
    assert presence.tick()["theme"] == "dark"
    flood = home.findChild(QObject, "inkFlood")
    QMetaObject.invokeMethod(switch, "click")
    assert _pump(qt, bridge, lambda: presence.tick()["theme"] == "light")
    QMetaObject.invokeMethod(switch, "click")            # while the ink still lifts: ignored
    assert _pump(qt, bridge, lambda: not flood.property("running"))
    assert presence.tick()["theme"] == "light"
    QMetaObject.invokeMethod(switch, "click")
    assert _pump(qt, bridge, lambda: presence.tick()["theme"] == "dark")
    for page in range(5):                                # every page draws in both worlds
        home.setProperty("page", page)
        _pump(qt, bridge, lambda: True, timeout=0.2)
    assert _qml_problems(messages) == []
    del engine


def test_presence_is_told_from_the_idle_time_only():
    from secdogie_studio.app import BUCKET, shichen_of

    assert shichen_of(time.mktime((2026, 11, 12, 23, 30, 0, 0, 0, -1))) == 0   # 子
    assert shichen_of(time.mktime((2026, 11, 12, 0, 30, 0, 0, 0, -1))) == 0
    assert shichen_of(time.mktime((2026, 11, 12, 14, 37, 0, 0, 0, -1))) == 7   # 未
    idle = [10.0]
    clock = [time.mktime((2026, 11, 12, 14, 0, 0, 0, 0, -1))]
    presence = Presence(FakeController(), idle=lambda: idle[0], clock=lambda: clock[0],
                        companion_factory=lambda profile, ready: FakeCompanion())
    for _ in range(60):                                   # a minute here...
        presence.tick()
        clock[0] += 1
    idle[0] = 900.0                                       # ...then away
    for _ in range(60):
        presence.tick()
        clock[0] += 1
    s = presence.tick()
    assert 55 <= s["hereToday"] <= 61 and s["present"] == [7]
    assert len(s["hereBars"]) == 24 and 0 < sum(s["hereBars"]) * BUCKET <= 61
    # the node card's dots: today by the quarter-hour, lit only where you were here
    assert len(s["dayQuarters"]) == 96 and s["quarterNow"] == 14 * 4 + 0
    assert s["dayQuarters"][56] > 0 and sum(1 for v in s["dayQuarters"] if v > 0) == 1


def test_the_overview_is_a_wallet(qt):
    """The node, what waits on you and each device are cards; the chosen card
    comes to the front, and Studio's own face (Google Sans) is loaded."""
    from PySide6.QtCore import QMetaObject
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtQuick import QQuickItem
    from secdogie_studio.app import load_fonts

    app, messages = qt
    messages.clear()
    load_fonts(app)
    assert "Google Sans" in QFontDatabase.families()
    presence = Presence(FakeController(), idle=lambda: 10.0, companion_factory=lambda profile, ready: FakeCompanion())
    engine, bridge, (light, home) = _windows(qt, presence, still=True)
    assert _pump(qt, bridge, lambda: home.isVisible())
    wallet = home.findChild(QQuickItem, "homeWallet")
    assert _pump(qt, bridge, lambda: wallet.property("count") >= 3)   # this node, waiting, an empty slot
    cards = [c for c in wallet.childItems() if c.property("kind") in ("dots", "glaze", "add")]
    kinds = {c.property("kind") for c in cards}
    assert {"dots", "glaze", "add"} <= kinds
    assert wallet.property("front") == 0
    add = next(c for c in cards if c.property("kind") == "add")
    QMetaObject.invokeMethod(add, "click")
    assert _pump(qt, bridge, lambda: wallet.property("front") == wallet.property("count") - 1)
    assert home.property("picked").toVariant()["what"] == "add"
    assert _qml_problems(messages) == []
    del engine
