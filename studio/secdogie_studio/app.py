"""The companion's window: a faint, breathing light in the corner of the screen.

Qt Quick draws it (``qml/Presence.qml``); this module is the thin bridge. On a
timer it reads the controller (profile, node), the companion (the dialogue
with the node), the OS idle counter, and lets ``presence.Stage`` decide what
to show: the light alone, a whisper, or one card. Slow work (unlocking the
operator key, creating keys) runs on a worker thread, and its result arrives
on the next tick, so Qt is never touched from another thread.

The window never takes the keyboard focus by itself. It appears when the
person opens Studio, and after that only changes inside its own frame. Typing
into a card is the person's click away.

Qt is imported lazily, so the rest of the package (and its tests) runs without
PySide6.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from .companion import RULES_TEMPLATE, Companion, parse_rules, try_rules
from .controller import EXITED, RUNNING, StudioController
from .idle import idle_seconds
from .motion import reduce_motion
from .presence import Attention, Moment, Stage, Whisper

QML_DIR = Path(__file__).resolve().parent / "qml"

WELCOME = Moment(
    "welcome", "welcome", "你好。我们先认识一下。",
    details=(("", "我会在这台电脑上安个家，生成三把钥匙：我自己的一把；你和我说话用的一把；"
                  "还有只属于你的签名钥匙。签名钥匙用你的口令锁着，只有你签名，"
                  "我才会去做改不回来的事。"),),
    label="初次见面",
)


def card_dict(m: Moment | None) -> dict | None:
    if m is None:
        return None
    return {"id": m.id, "kind": m.kind, "label": m.label, "says": m.says, "options": list(m.options),
            "details": [[k, v] for k, v in m.details], "freeText": m.free_text, "canApprove": m.can_approve}


class Presence:
    """Everything the window shows, computed without Qt (tested headless)."""

    def __init__(self, controller: StudioController, *, idle=idle_seconds, clock=time.time,
                 companion_factory=Companion):
        self.controller = controller
        self.idle = idle
        self.clock = clock
        self.companion_factory = companion_factory
        self.attention = Attention()
        self.stage = Stage()
        self.companion: Companion | None = None
        self._woke = False
        self._started = False
        self._lock = threading.Lock()
        self._feedback = ""  # the answer to the person's last action on a card
        self._rules_note = ""
        self._in_force = ""
        self._busy_with = ""
        self.inner_open = False
        self.say_open = False
        self.rules_text = ""

    # -- the tick ----------------------------------------------------------------------

    def tick(self) -> dict:
        now = self.clock()
        snap = self.controller.snapshot()
        moments: list[Moment] = []
        if not snap.initialized:
            moments = [WELCOME]
        else:
            if snap.node_state != RUNNING and not self._started and not snap.busy:
                self._started = True
                self.controller.start_node()
            ready = self.controller.ready()
            if ready is not None and self.companion is None:
                try:
                    self.companion = self.companion_factory(self.controller.profile, ready)
                except (OSError, ValueError) as e:
                    self._set_feedback(f"我连不上自己的节点（{e}）。关掉 Studio 再打开试试。")
            if self.companion is not None:
                if not self._woke:
                    self._woke = True
                    self.stage.note(Whisper("woke", "我在。", "calm", now))
                for w in self.companion.new_whispers():
                    self.stage.note(w)
                moments = self.companion.moments()
            if snap.node_state == EXITED and self.companion is None:
                self.stage.note(Whisper(f"exit{snap.returncode}", f"我没能醒来（代码 {snap.returncode}）。右键点我，看看里面的记录。", "warm", now))
        self.attention.sample(self.idle(), now)
        busy = self.companion.busy() if self.companion is not None else bool(snap.busy)
        view = self.stage.update(moments, self.attention.mode(now), now, busy=busy)
        with self._lock:
            feedback, rules_note, in_force, working = self._feedback, self._rules_note, self._in_force, self._busy_with
        return {
            "mood": view.mood,
            "card": card_dict(view.card),
            "cardKey": view.card.id if view.card is not None else "",
            "whisper": view.whisper.text if view.whisper is not None else "",
            "whisperTone": view.whisper.tone if view.whisper is not None else "calm",
            "held": view.held,
            "feedback": feedback,
            "working": working or snap.busy,
            "innerOpen": self.inner_open,
            "sayOpen": self.say_open,
            "statusRows": self._status_rows(snap),
            "logText": "\n".join(self.companion.log_lines(14) if self.companion is not None else snap.log[-14:]),
            "rulesText": self.rules_text,
            "rulesNote": rules_note,
            "inForce": in_force,
        }

    def _status_rows(self, snap) -> list:
        if not snap.initialized:
            return []
        state = {"running": "醒着", "starting": "正在醒来", "stopped": "在休息", "exited": "停下了"}
        rows = [["状态", state.get(snap.node_state, snap.node_state)], ["节点", snap.node_did],
                ["你的签名钥匙", snap.operator_did], ["这台电脑的 App", snap.app_did], ["监听", snap.listen],
                ["角色", "、".join(snap.roles)]]
        return [[k, v] for k, v in rows if v]

    # -- worker plumbing --------------------------------------------------------------------

    def _set_feedback(self, text: str) -> None:
        with self._lock:
            self._feedback = text

    def _work(self, what: str, fn) -> None:
        with self._lock:
            if self._busy_with:
                return
            self._busy_with = what

        def run():
            try:
                fn()
            finally:
                with self._lock:
                    self._busy_with = ""

        threading.Thread(target=run, daemon=True, name=f"studio-{what}").start()

    # -- what the person does -----------------------------------------------------------------

    def hover(self) -> None:
        self.attention.touch(self.clock())

    def welcome(self, passphrase: str, confirm: str) -> None:
        if len(passphrase) < 8:
            self._set_feedback("口令至少要 8 位，再长一点。")
        elif passphrase != confirm:
            self._set_feedback("两次输入的不一样，再输一次。")
        else:
            self._set_feedback("")
            self.controller.create_profile(passphrase)

    def answer(self, moment_id: str, text: str) -> None:
        if self.companion is not None:
            self._set_feedback(self.companion.answer(moment_id, text) or "")

    def approve(self, moment_id: str, passphrase: str) -> None:
        companion = self.companion
        if companion is None:
            return
        self._set_feedback("")
        self._work("正在签名", lambda: self._set_feedback(companion.approve(moment_id, passphrase) or ""))

    def deny(self, moment_id: str) -> None:
        if self.companion is not None:
            self.companion.deny(moment_id)
            self._set_feedback("")

    def remember(self, moment_id: str, keep: bool) -> None:
        if self.companion is not None:
            self._set_feedback(self.companion.remember(moment_id, keep) or "")

    def say(self, text: str) -> None:
        if self.companion is not None and text.strip():
            self.companion.say(text)
            self.say_open = False

    def toggle_say(self) -> None:
        self.say_open = not self.say_open and self.companion is not None
        if self.say_open:
            self.inner_open = False

    def toggle_inner(self) -> None:
        self.inner_open = not self.inner_open
        if self.inner_open:
            self.say_open = False
            if not self.rules_text:
                self.rules_text = RULES_TEMPLATE
            self.refresh_in_force()

    def refresh_in_force(self) -> None:
        companion = self.companion
        if companion is None:
            return

        def fetch():
            listing = companion.rules_in_force(timeout=3.0)
            if listing is None:
                text = "暂时问不到节点。"
            else:
                active = listing.get("active", [])
                text = "；".join(f"{a['kind']} 第 {a['version']} 版" + ("" if a.get("applied") else "（已记录，未启用）")
                                for a in active) or "还没有注入过规则，用的是内置的门。"
            with self._lock:
                self._in_force = "生效中：" + text

        threading.Thread(target=fetch, daemon=True, name="studio-rules-list").start()

    def try_rules(self, text: str, sample: str) -> None:
        self.rules_text = text
        try:
            body = parse_rules(text)
        except ValueError as e:
            note = str(e)
        else:
            from .companion import check_rules

            problems = check_rules(body, self.companion.mesh if self.companion is not None else "home")
            if problems:
                note = "规则还有问题：" + "；".join(problems)
            elif sample.strip():
                note = try_rules(body, sample)
            else:
                note = "规则本身没问题。写一句话试试它会怎么回应。"
        with self._lock:
            self._rules_note = note

    def push_rules(self, text: str, passphrase: str) -> None:
        self.rules_text = text
        companion = self.companion
        if companion is None:
            return
        try:
            body = parse_rules(text)
        except ValueError as e:
            with self._lock:
                self._rules_note = str(e)
            return

        def push():
            ok, note = companion.push_rules(body, passphrase)
            with self._lock:
                self._rules_note = note
            if ok:
                self.refresh_in_force()

        self._work("正在注入规则", push)

    def shutdown(self) -> None:
        if self.companion is not None:
            self.companion.close()
            self.companion = None
        self.controller.shutdown()


def bridge_class():
    """The QObject the QML reads as ``P``: the presence's tick as properties,
    its actions as slots. Built on demand so this module imports without Qt."""
    from PySide6.QtCore import Property, QObject, Signal, Slot
    from PySide6.QtGui import QGuiApplication

    names = ("mood", "card", "cardKey", "whisper", "whisperTone", "held", "feedback", "working", "innerOpen",
             "sayOpen", "statusRows", "logText", "rulesText", "rulesNote", "inForce")
    changed = Signal()

    def init(self, presence: Presence, still: bool = False):
        QObject.__init__(self)
        self.presence = presence
        self._still = still
        self._s = presence.tick()

    def refresh(self):
        s = self.presence.tick()
        if s != self._s:
            self._s = s
            self.changed.emit()

    def act(fn):
        def slot(self, *args):
            fn(self.presence, *args)
            self.refresh()
        return slot

    ns = {"changed": changed, "__init__": init, "refresh": refresh}
    for name in names:  # declared at class creation, so the meta-object has them
        ns[name] = Property("QVariant", (lambda n: lambda self: self._s[n])(name), notify=changed)
    # The person asked their computer for less motion: a still light, plain fades.
    ns["still"] = Property(bool, lambda self: self._still, constant=True)
    slots = {
        "hover": ((), Presence.hover), "welcome": ((str, str), Presence.welcome),
        "answer": ((str, str), Presence.answer), "approve": ((str, str), Presence.approve),
        "deny": ((str,), Presence.deny), "remember": ((str, bool), Presence.remember),
        "say": ((str,), Presence.say), "toggleSay": ((), Presence.toggle_say),
        "toggleInner": ((), Presence.toggle_inner), "tryRules": ((str, str), Presence.try_rules),
        "pushRules": ((str, str), Presence.push_rules),
    }
    for name, (types, fn) in slots.items():
        ns[name] = Slot(*types, name=name)(act(fn))
    ns["rest"] = Slot(name="rest")(lambda self: QGuiApplication.quit())
    return type("Bridge", (QObject,), ns)


def run_gui(root: Path, *, kdf: str = "moderate") -> int:
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtGui import QFont, QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine

    Bridge = bridge_class()

    app = QGuiApplication.instance() or QGuiApplication([])
    app.setApplicationName("secdogie")
    font = QFont()
    font.setFamilies(["PingFang SC", "Microsoft YaHei UI", "Noto Sans CJK SC", "Source Han Sans SC",
                      "WenQuanYi Zen Hei", "Segoe UI", "Helvetica Neue", "sans-serif"])
    app.setFont(font)
    presence = Presence(StudioController(root, kdf=kdf))
    bridge = Bridge(presence, reduce_motion(app))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("P", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_DIR / "Presence.qml")))
    if not engine.rootObjects():
        presence.shutdown()
        return 1
    timer = QTimer()
    timer.timeout.connect(bridge.refresh)
    timer.start(250)
    app.aboutToQuit.connect(presence.shutdown)
    code = app.exec()
    timer.stop()
    del engine  # the engine goes before the bridge it reads, so nothing reads a dead object
    return code


__all__ = ["Presence", "QML_DIR", "WELCOME", "bridge_class", "card_dict", "run_gui"]
