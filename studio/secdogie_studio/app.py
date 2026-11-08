"""Studio's two windows: the home window, and the light it leaves behind.

Opening Studio opens the home window (``qml/Home.qml``): the conversation, and
this node and its mesh. Closing it leaves the companion as a faint, breathing
light in the corner of the screen (``qml/Presence.qml``); clicking the light
opens the home window again. While the home window is open, what waits on the
person (a question, a signature, a memory) shows inside it. While it is
closed, the light's own cards take over, and ``presence.Stage`` decides when.

This module is the thin bridge. On a timer it reads the controller (profile,
node), the companion (the dialogue with the node), the OS idle counter and,
while the home window is open, the node's own status (``admin/v1``). Slow work
(unlocking the operator key, creating keys, asking the node) runs on worker
threads, and its result arrives on the next tick, so Qt is never touched from
another thread.

Neither window takes the keyboard focus by itself: the home window opens
because the person opened Studio or clicked the light, and the light's cards
fade in without taking focus.

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
from .profile import clean_name

QML_DIR = Path(__file__).resolve().parent / "qml"
POLL_EVERY = 3.0  # seconds between asking the node how it is, while the home window is open

STATE_WORDS = {"running": "醒着", "starting": "正在醒来", "stopped": "在休息", "exited": "停下了"}
GOAL_WORDS = {"pending": "排队中", "active": "正在做", "done": "做完了", "failed": "没做成"}
ROLE_WORDS = {"forward": "转发", "serve": "服务", "model": "模型", "ingest": "采集", "relay": "中继"}
KIND_WORDS = {"gate-rules": "门的规则", "decision-model": "决策模型", "ingest": "采集规则", "allowlist": "允许名单"}

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


def duration_words(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return "不到一分钟"
    m, h, d = s // 60 % 60, s // 3600 % 24, s // 86400
    if not h and not d:
        return f"{m} 分钟"
    if not d:
        return f"{h} 小时 {m} 分钟" if m else f"{h} 小时"
    return f"{d} 天 {h} 小时" if h else f"{d} 天"


def work_words(goals: dict) -> str:
    parts = [f"{GOAL_WORDS[k]} {goals[k]} 件" for k in ("active", "pending", "done", "failed") if goals.get(k)]
    return " · ".join(parts) if parts else "还没有交给我的事。"


def greeting(now: float) -> str:
    hour = time.localtime(now).tm_hour
    if 5 <= hour < 11:
        return "早上好"
    if 11 <= hour < 13:
        return "中午好"
    if 13 <= hour < 18:
        return "下午好"
    if 18 <= hour < 23:
        return "晚上好"
    return "夜深了"


def first_moment(moments: list[Moment], now: float) -> Moment | None:
    """What the home window shows first: the same order the light uses."""
    live = [m for m in moments if m.expires is None or m.expires > now]
    order = sorted(live, key=lambda m: (m.kind != "welcome", m.urgency != "soon", m.arrived, m.id))
    return order[0] if order else None


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
        self.home_open = True  # opening Studio opens the home window
        self.rules_text = ""
        self._status: dict | None = None  # the node's last answer on admin/v1
        self._active: list[dict] = []  # the policy bundles in force, as the node listed them
        self._polled = float("-inf")
        self._polling = False
        # The first meeting, in the home window: "form" (hello, a name, a passphrase),
        # "making" (the keys), "ready" (here they are), then "" for good.
        self._met_here = False  # this session began before there was a profile
        self._onboarded = False
        self._name = ""  # the name given at the first meeting, until the profile holds it

    # -- the tick ----------------------------------------------------------------------

    def tick(self) -> dict:
        now = self.clock()
        snap = self.controller.snapshot()
        moments: list[Moment] = []
        if not snap.initialized:
            self._met_here = True
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
        self._maybe_poll(now)
        with self._lock:
            feedback, rules_note, in_force, working = self._feedback, self._rules_note, self._in_force, self._busy_with
        home_card = first_moment(moments, now)
        return {
            "onboarding": self._onboarding(snap),
            "you": self._you(snap),
            "greeting": greeting(now),
            "keys": {"node": snap.node_did, "app": snap.app_did, "operator": snap.operator_did}
                    if snap.initialized else {},
            "homeOpen": self.home_open,
            "homeCard": card_dict(home_card),
            "homeCardKey": home_card.id if home_card is not None else "",
            "waiting": max(0, len(moments) - 1),
            "conversation": self.companion.conversation() if self.companion is not None else [],
            "node": self._node_view(snap, now),
            "mood": view.mood,
            "card": card_dict(view.card),
            "cardKey": view.card.id if view.card is not None else "",
            "whisper": view.whisper.text if view.whisper is not None else "",
            "whisperTone": view.whisper.tone if view.whisper is not None else "calm",
            "held": view.held,
            "feedback": feedback,
            "working": working or snap.busy,
            "innerOpen": self.inner_open,
            "statusRows": self._status_rows(snap),
            "logText": "\n".join(self.companion.log_lines(14) if self.companion is not None else snap.log[-14:]),
            "rulesText": self.rules_text,
            "rulesNote": rules_note,
            "inForce": in_force,
        }

    def _onboarding(self, snap) -> str:
        if not snap.initialized:
            return "making" if snap.busy else "form"
        return "ready" if self._met_here and not self._onboarded else ""

    def _you(self, snap) -> str:
        if snap.initialized:
            profile = getattr(self.controller, "profile", None)
            name = profile.name() if profile is not None and hasattr(profile, "name") else ""
            return name or self._name
        return self._name

    def _node_view(self, snap, now: float) -> dict:
        """The home window's node and mesh page: the controller's view of this
        machine, the node's own answer on admin/v1, and the rules in force."""
        if not snap.initialized:
            return {}
        with self._lock:
            status, active = self._status, list(self._active)
        st = status or {}
        app = st.get("app") or {}
        relays, rendezvous = st.get("relays", 0), st.get("rendezvous", 0)
        carriers = " · ".join([*(c.upper() for c in st.get("carriers", ["udp"])),
                               *([f"中继 {relays} 个"] if relays else []),
                               *([f"会合点 {rendezvous} 个"] if rendezvous else [])])
        rules = [{"name": KIND_WORDS.get(a.get("kind"), a.get("kind", "")), "version": a.get("version", 0),
                  "applied": bool(a.get("applied")),
                  "words": f"第 {a.get('version', 0)} 版 · " + ("生效中" if a.get("applied") else "已记录，节点还没启用")}
                 for a in active]
        return {
            "state": snap.node_state,
            "stateWords": STATE_WORDS.get(snap.node_state, snap.node_state),
            "since": f"醒了 {duration_words(now - st['since'])}" if st.get("since") and snap.node_state == RUNNING
                     else "",
            "answered": status is not None,
            "did": snap.node_did,
            "app": snap.app_did,
            "appConnected": bool(app.get("connected")),
            "operator": snap.operator_did,
            "mesh": st.get("mesh") or (self.companion.mesh if self.companion is not None else "home"),
            "roles": "、".join(ROLE_WORDS.get(r, r) for r in (st.get("roles") or snap.roles)),
            "listen": st.get("listen") or snap.listen,
            "carriers": carriers,
            "work": work_words(st.get("goals") or {}),
            "recent": [{"title": g.get("title") or g.get("id", ""), "status": g.get("status", ""),
                        "words": GOAL_WORDS.get(g.get("status", ""), "")} for g in st.get("recent", [])],
            "rules": rules,
            "halted": bool(st.get("halted")),
        }

    def _maybe_poll(self, now: float) -> None:
        """While the home window is open, ask the node how it is every few seconds."""
        companion = self.companion
        if companion is None or not self.home_open or now - self._polled < POLL_EVERY:
            return
        with self._lock:
            if self._polling:
                return
            self._polling = True
        self._polled = now

        def fetch():
            try:
                status = companion.node_status(timeout=3.0)
                listing = companion.rules_in_force(timeout=3.0)
                with self._lock:
                    if status is not None:
                        self._status = status
                    if listing is not None:
                        self._active = list(listing.get("active", []))
            finally:
                with self._lock:
                    self._polling = False

        threading.Thread(target=fetch, daemon=True, name="studio-status").start()

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

    def welcome(self, passphrase: str, confirm: str, name: str = "") -> None:
        if len(passphrase) < 8:
            self._set_feedback("口令至少要 8 位，再长一点。")
        elif passphrase != confirm:
            self._set_feedback("两次输入的不一样，再输一次。")
        else:
            self._set_feedback("")
            self._name = clean_name(name)
            self.controller.create_profile(passphrase, name=self._name)

    def finish_onboarding(self) -> None:
        self._onboarded = True

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

    def open_home(self) -> None:
        self.home_open = True
        self.inner_open = False
        self._polled = float("-inf")  # ask the node at once
        self.attention.touch(self.clock())

    def close_home(self) -> None:
        self.home_open = False

    def open_rules(self) -> None:
        """The rules editor, wherever it is shown: load the draft and what is in force."""
        if not self.rules_text:
            self.rules_text = RULES_TEMPLATE
        self.refresh_in_force()

    def toggle_inner(self) -> None:
        self.inner_open = not self.inner_open
        if self.inner_open:
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
                if listing is not None:
                    self._active = list(listing.get("active", []))

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

    names = ("onboarding", "you", "greeting", "keys", "homeOpen", "homeCard", "homeCardKey", "waiting", "conversation", "node", "mood",
             "card", "cardKey",
             "whisper", "whisperTone", "held", "feedback", "working", "innerOpen", "statusRows", "logText",
             "rulesText", "rulesNote", "inForce")
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
        "hover": ((), Presence.hover), "welcome": ((str, str, str), Presence.welcome),
        "finishOnboarding": ((), Presence.finish_onboarding),
        "answer": ((str, str), Presence.answer), "approve": ((str, str), Presence.approve),
        "deny": ((str,), Presence.deny), "remember": ((str, bool), Presence.remember),
        "say": ((str,), Presence.say), "openHome": ((), Presence.open_home),
        "closeHome": ((), Presence.close_home), "openRules": ((), Presence.open_rules),
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
    app.setQuitOnLastWindowClosed(False)  # closing the home window leaves the light; "让我休息" quits
    font = QFont()
    font.setFamilies(["PingFang SC", "Microsoft YaHei UI", "Noto Sans CJK SC", "Source Han Sans SC",
                      "WenQuanYi Zen Hei", "Segoe UI", "Helvetica Neue", "sans-serif"])
    app.setFont(font)
    presence = Presence(StudioController(root, kdf=kdf))
    bridge = Bridge(presence, reduce_motion(app))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("P", bridge)
    for name in ("Presence.qml", "Home.qml"):
        engine.load(QUrl.fromLocalFile(str(QML_DIR / name)))
    if len(engine.rootObjects()) != 2:
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
