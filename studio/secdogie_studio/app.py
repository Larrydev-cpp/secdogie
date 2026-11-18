"""Studio's window and the icon it leaves in the task bar.

Opening Studio opens the window (``qml/Home.qml``): the conversation, and this
node and its mesh. Closing it puts the window away and leaves the companion in
the task bar, still running; clicking the icon there opens it again, and "let
it rest" in that icon's menu is what quits. On a desktop with no tray the
window minimises instead, so the person can always get back to it.

What waits on them (a question, a signature, a memory) shows in the window.

This module is the thin bridge. On a timer it reads the controller (profile,
node), the companion (the dialogue with the node), the OS idle counter and,
while the home window is open, the node's own status (``admin/v1``). Slow work
(unlocking the operator key, creating keys, asking the node) runs on worker
threads, and its result arrives on the next tick, so Qt is never touched from
another thread.

The window never takes the keyboard focus by itself: it opens because the
person opened Studio or clicked the icon in the task bar.

Qt is imported lazily, so the rest of the package (and its tests) runs without
PySide6.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from .companion import RULES_TEMPLATE, Companion, cortex_at, mind_view, parse_rules, think_through, try_rules
from .controller import EXITED, RUNNING, StudioController
from .idle import idle_seconds
from .motion import reduce_motion
from .presence import Attention, Moment, Stage, Whisper
from .profile import clean_name
from .words import LANGS, get_ui, pick, set_ui
from .words import load as load_lang
from .words import save as save_lang

QML_DIR = Path(__file__).resolve().parent / "qml"
# Google Sans (OFL 1.1, Latin; qml/fonts/OFL-GoogleSans.txt) first, the platform's CJK sans behind it
FONT_FAMILIES = ["Google Sans", "PingFang SC", "Microsoft YaHei UI", "Noto Sans CJK SC", "Source Han Sans SC",
                 "WenQuanYi Zen Hei", "Segoe UI", "Helvetica Neue", "sans-serif"]
POLL_EVERY = 3.0  # seconds between asking the node how it is, while the home window is open
THEMES = ("dark", "light")  # 墨 (ink) and 宣纸 (rice paper)
BUCKET, BUCKETS = 300, 24  # presence in 5-minute columns, over the last two hours


def quarter_of(t: float) -> int:
    """Which quarter-hour of the local day (0..95) a moment falls in."""
    lt = time.localtime(t)
    return lt.tm_hour * 4 + lt.tm_min // 15


def shichen_of(t: float) -> int:
    """The 时辰 (0 = 子, 23:00-01:00, ... 11 = 亥) that local time `t` falls in."""
    hour = time.localtime(t).tm_hour
    return ((hour + 1) % 24) // 2

STATE_WORDS = {"zh": {"running": "醒着", "starting": "正在醒来", "stopped": "在休息", "exited": "停下了"},
               "en": {"running": "awake", "starting": "waking up", "stopped": "resting", "exited": "stopped"}}
GOAL_WORDS = {"zh": {"pending": "排队中", "active": "正在做", "done": "做完了", "failed": "没做成"},
              "en": {"pending": "queued", "active": "in progress", "done": "done", "failed": "not done"}}
ROLE_WORDS = {"zh": {"forward": "转发", "serve": "服务", "model": "模型", "ingest": "采集", "relay": "中继"},
              "en": {"forward": "forwarding", "serve": "serving", "model": "model", "ingest": "ingestion",
                     "relay": "relay"}}
KIND_WORDS = {"zh": {"gate-rules": "门的规则", "decision-model": "决策模型", "ingest": "采集规则", "allowlist": "允许名单"},
              "en": {"gate-rules": "Gate rules", "decision-model": "Decision model", "ingest": "Ingestion rules",
                     "allowlist": "Allowlist"}}

def welcome_moment(lang: str = "zh") -> Moment:
    """The first hello, as the light's card (the home window has its own first meeting)."""
    return Moment(
        "welcome", "welcome", pick(lang, "你好。我们先认识一下。", "Hello. Let's get to know each other."),
        details=(("", pick(lang, "我会在这台电脑上安个家，生成三把钥匙：我自己的一把；你和我说话用的一把；"
                                 "还有只属于你的签名钥匙。签名钥匙用你的口令锁着，只有你签名，"
                                 "我才会去做改不回来的事。",
                           "I'll make a home on this computer with three keys: my own; one for the two of us to "
                           "talk; and your signing key, locked with your passphrase. Nothing that can't be undone "
                           "happens without your signature.")),),
        label=pick(lang, "初次见面", "First meeting"),
    )


WELCOME = welcome_moment()


def card_dict(m: Moment | None) -> dict | None:
    if m is None:
        return None
    return {"id": m.id, "kind": m.kind, "label": m.label, "says": m.says, "options": list(m.options),
            "details": [[k, v] for k, v in m.details], "freeText": m.free_text, "canApprove": m.can_approve}


def duration_words(seconds: float, lang: str = "zh") -> str:
    s = max(0, int(seconds))
    if s < 60:
        return pick(lang, "不到一分钟", "under a minute")
    m, h, d = s // 60 % 60, s // 3600 % 24, s // 86400
    if lang == "en":
        unit = lambda n, w: f"{n} {w}" + ("" if n == 1 else "s")  # noqa: E731
        if not h and not d:
            return unit(m, "minute")
        if not d:
            return f"{unit(h, 'hour')} {unit(m, 'minute')}" if m else unit(h, "hour")
        return f"{unit(d, 'day')} {unit(h, 'hour')}" if h else unit(d, "day")
    if not h and not d:
        return f"{m} 分钟"
    if not d:
        return f"{h} 小时 {m} 分钟" if m else f"{h} 小时"
    return f"{d} 天 {h} 小时" if h else f"{d} 天"


def work_words(goals: dict, lang: str = "zh") -> str:
    words = GOAL_WORDS["en" if lang == "en" else "zh"]
    if lang == "en":
        parts = [f"{goals[k]} {words[k]}" for k in ("active", "pending", "done", "failed") if goals.get(k)]
        return ", ".join(parts).capitalize() if parts else "Nothing handed to me yet."
    parts = [f"{words[k]} {goals[k]} 件" for k in ("active", "pending", "done", "failed") if goals.get(k)]
    return "，".join(parts) if parts else "还没有交给我的事。"


def greeting(now: float, lang: str = "zh") -> str:
    hour = time.localtime(now).tm_hour
    if 5 <= hour < 11:
        return pick(lang, "早上好", "Good morning")
    if 11 <= hour < 13:
        return pick(lang, "中午好", "Good day")
    if 13 <= hour < 18:
        return pick(lang, "下午好", "Good afternoon")
    if 18 <= hour < 23:
        return pick(lang, "晚上好", "Good evening")
    return pick(lang, "夜深了", "It's late")


def first_moment(moments: list[Moment], now: float) -> Moment | None:
    """What the home window shows first: the same order the light uses."""
    live = [m for m in moments if m.expires is None or m.expires > now]
    order = sorted(live, key=lambda m: (m.kind != "welcome", m.urgency != "soon", m.arrived, m.id))
    return order[0] if order else None


class Presence:
    """Everything the window shows, computed without Qt (tested headless)."""

    def __init__(self, controller: StudioController, *, idle=idle_seconds, clock=time.time,
                 companion_factory=Companion, lang: str | None = None):
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
        self._trial: dict = {}  # how the gate thinks about a sentence the person tried (the Gate page)
        self._thinker_note = ""
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
        root = getattr(getattr(controller, "profile", None), "root", None)
        self.lang = lang if lang in LANGS else (load_lang(root) if root is not None else load_lang(Path(".")))
        theme = get_ui(root).get("theme") if root is not None else None
        self.theme = theme if theme in THEMES else "dark"
        self.devices = None  # pairing and "My devices" (devices.py), once there is a profile
        self._present: tuple[str, set[int]] = ("", set())  # today's date, and the 时辰 you were here in
        self._here_today = 0.0  # seconds at this computer today
        self._buckets: dict[int, float] = {}  # 5-minute bucket -> seconds at this computer in it
        self._quarters: dict[int, float] = {}  # today's quarter-hour (0..95) -> seconds at this computer in it
        self._last_tick: float | None = None

    # -- the tick ----------------------------------------------------------------------

    def tick(self) -> dict:
        now = self.clock()
        snap = self.controller.snapshot()
        moments: list[Moment] = []
        w = lambda zh, en: pick(self.lang, zh, en)  # noqa: E731
        if not snap.initialized:
            self._met_here = True
            moments = [welcome_moment(self.lang)]
        else:
            if snap.node_state != RUNNING and not self._started and not snap.busy:
                self._started = True
                self.controller.start_node()
            ready = self.controller.ready()
            if ready is not None and self.companion is None:
                try:
                    self.companion = self.companion_factory(self.controller.profile, ready)
                except (OSError, ValueError) as e:
                    self._set_feedback(w(f"我连不上自己的节点（{e}）。关掉 Studio 再打开试试。",
                                         f"I can't reach my own node ({e}). Close Studio and open it again."))
            if self.companion is not None:
                self.companion.lang = self.lang
                if not self._woke:
                    self._woke = True
                    self.stage.note(Whisper("woke", w("我在。", "I'm here."), "calm", now))
                for w in self.companion.new_whispers():
                    self.stage.note(w)
                moments = self.companion.moments()
            if snap.node_state == EXITED and self.companion is None:
                self.stage.note(Whisper(f"exit{snap.returncode}",
                                        w(f"我没能醒来（代码 {snap.returncode}）。右键点我，看看里面的记录。",
                                          f"I couldn't wake up (code {snap.returncode}). Right-click me to see the log."),
                                        "warm", now))
        idle_s = self.idle()
        self.attention.sample(idle_s, now)
        self._note_presence(idle_s, now)
        if snap.initialized and self.devices is None and getattr(self.controller, "profile", None) is not None:
            from .devices import Devices

            self.devices = Devices(self.controller.profile, lang=lambda: self.lang, clock=self.clock)
            self.devices.on_rules = self._rules_arrived
        busy = self.companion.busy() if self.companion is not None else bool(snap.busy)
        view = self.stage.update(moments, self.attention.mode(now), now, busy=busy)
        self._maybe_poll(now)
        with self._lock:
            feedback, rules_note, in_force, working = self._feedback, self._rules_note, self._in_force, self._busy_with
        home_card = first_moment(moments, now)
        return {
            "lang": self.lang,
            "onboarding": self._onboarding(snap),
            "you": self._you(snap),
            "greeting": greeting(now, self.lang),
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
            "trial": dict(self._trial),
            "thinker": self._thinker_view(),
            "mind": self._mind_view(),
            "theme": self.theme,
            "devices": self.devices.snapshot() if self.devices is not None else {},
            "present": sorted(self._present[1]),
            "shichen": shichen_of(now),
            "clock": time.strftime("%H:%M", time.localtime(now)),
            "hereToday": int(self._here_today),
            "hereBars": self._bars(now),
            "dayQuarters": [round(min(1.0, self._quarters.get(q, 0.0) / 900), 3) for q in range(96)],
            "quarterNow": quarter_of(now),
        }

    def _note_presence(self, idle_s, now: float) -> None:
        """Which 时辰 (the twelve two-hour periods) of today you were at this
        computer in: only from the idle time the system reports, nothing else."""
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        if self._present[0] != day:
            self._present = (day, set())
            self._here_today = 0.0
            self._quarters = {}
        step = 0.0 if self._last_tick is None else min(5.0, max(0.0, now - self._last_tick))
        self._last_tick = now
        if idle_s is not None and idle_s < 120:
            self._present[1].add(shichen_of(now))
            self._here_today += step
            q = quarter_of(now)
            self._quarters[q] = self._quarters.get(q, 0.0) + step
            bucket = int(now // BUCKET)
            self._buckets[bucket] = self._buckets.get(bucket, 0.0) + step
            for old in [b for b in self._buckets if b < bucket - BUCKETS]:
                del self._buckets[old]

    def _bars(self, now: float) -> list[float]:
        """Your presence over the last two hours, in 5-minute columns (0..1)."""
        last = int(now // BUCKET)
        return [round(min(1.0, self._buckets.get(b, 0.0) / BUCKET), 3) for b in range(last - BUCKETS + 1, last + 1)]

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
        en = self.lang == "en"
        carriers = ("、" if not en else ", ").join(c.upper() for c in st.get("carriers", ["udp"]))
        extra = [pick(self.lang, f"{relays} 个中继", f"{relays} relay" + ("s" if relays != 1 else ""))] if relays else []
        extra += [pick(self.lang, f"{rendezvous} 个会合点", f"{rendezvous} rendezvous")] if rendezvous else []
        if extra:
            carriers += pick(self.lang, "，另有 " + "、".join(extra), ", plus " + " and ".join(extra))
        kinds = KIND_WORDS["en" if en else "zh"]

        def version_words(a) -> str:
            v = a.get("version", 0)
            if en:
                return f"version {v}" + (", in force" if a.get("applied") else " (recorded; the node does not apply it yet)")
            return f"第 {v} 版" + ("，生效中" if a.get("applied") else "（已记录，节点还没启用）")

        rules = [{"name": kinds.get(a.get("kind"), a.get("kind", "")), "version": a.get("version", 0),
                  "applied": bool(a.get("applied")), "words": version_words(a)} for a in active]
        states, roles, goals = (STATE_WORDS["en" if en else "zh"], ROLE_WORDS["en" if en else "zh"],
                                GOAL_WORDS["en" if en else "zh"])
        return {
            "state": snap.node_state,
            "stateWords": states.get(snap.node_state, snap.node_state),
            "since": pick(self.lang, f"醒了 {duration_words(now - st['since'], 'zh')}",
                          f"awake for {duration_words(now - st['since'], 'en')}")
                     if st.get("since") and snap.node_state == RUNNING else "",
            "answered": status is not None,
            "did": snap.node_did,
            "app": snap.app_did,
            "appConnected": bool(app.get("connected")),
            "operator": snap.operator_did,
            "mesh": st.get("mesh") or (self.companion.mesh if self.companion is not None else "home"),
            "roles": ("、" if not en else ", ").join(roles.get(r, r) for r in (st.get("roles") or snap.roles)),
            "listen": st.get("listen") or snap.listen,
            "carriers": carriers,
            "work": work_words(st.get("goals") or {}, self.lang),
            "recent": [{"title": g.get("title") or g.get("id", ""), "status": g.get("status", ""),
                        "words": goals.get(g.get("status", ""), "")} for g in st.get("recent", [])],
            "rules": rules,
            "halted": bool(st.get("halted")),
            "uptime": int(now - st["since"]) if st.get("since") and snap.node_state == RUNNING else 0,
            "goalsDone": sum(1 for g in st.get("recent", []) if g.get("status") == "done"),
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
        w = lambda zh, en: pick(self.lang, zh, en)  # noqa: E731
        state = STATE_WORDS["en" if self.lang == "en" else "zh"]
        rows = [[w("状态", "State"), state.get(snap.node_state, snap.node_state)], [w("节点", "Node"), snap.node_did],
                [w("你的签名钥匙", "Your signing key"), snap.operator_did],
                [w("这台电脑的 App", "This computer's App"), snap.app_did], [w("监听", "Listening"), snap.listen],
                [w("角色", "Roles"), ("、" if self.lang != "en" else ", ").join(snap.roles)]]
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
            self._set_feedback(pick(self.lang, "口令至少要 8 位，再长一点。",
                                    "The passphrase needs at least 8 characters. Make it longer."))
        elif passphrase != confirm:
            self._set_feedback(pick(self.lang, "两次输入的不一样，再输一次。",
                                    "The two entries differ. Type it again."))
        else:
            self._set_feedback("")
            self._name = clean_name(name)
            self.controller.create_profile(passphrase, name=self._name)

    def finish_onboarding(self) -> None:
        self._onboarded = True

    def set_language(self, lang: str) -> None:
        """The person's choice of language, kept beside the profile."""
        if lang not in LANGS:
            return
        self.lang = lang
        root = getattr(getattr(self.controller, "profile", None), "root", None)
        if root is not None:
            save_lang(root, lang)

    def answer(self, moment_id: str, text: str) -> None:
        if self.companion is not None:
            self._set_feedback(self.companion.answer(moment_id, text) or "")

    def approve(self, moment_id: str, passphrase: str) -> None:
        companion = self.companion
        if companion is None:
            return
        self._set_feedback("")
        self._work(pick(self.lang, "正在签名", "Signing"),
                   lambda: self._set_feedback(companion.approve(moment_id, passphrase) or ""))

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
            en = self.lang == "en"
            if listing is None:
                text = pick(self.lang, "暂时问不到节点。", "The node is not answering right now.")
            else:
                active = listing.get("active", [])
                if en:
                    text = "; ".join(f"{a['kind']} version {a['version']}" + ("" if a.get("applied") else " (recorded, not applied)")
                                     for a in active) or "No rules injected yet; the built-in gates apply."
                else:
                    text = "；".join(f"{a['kind']} 第 {a['version']} 版" + ("" if a.get("applied") else "（已记录，未启用）")
                                    for a in active) or "还没有注入过规则，用的是内置的门。"
            with self._lock:
                self._in_force = pick(self.lang, "生效中：", "In force: ") + text
                if listing is not None:
                    self._active = list(listing.get("active", []))

        threading.Thread(target=fetch, daemon=True, name="studio-rules-list").start()

    def try_rules(self, text: str, sample: str) -> None:
        self.rules_text = text
        try:
            body = parse_rules(text, self.lang)
        except ValueError as e:
            note = str(e)
        else:
            from .companion import check_rules

            problems = check_rules(body, self.companion.mesh if self.companion is not None else "home", self.lang)
            if problems:
                note = pick(self.lang, "规则还有问题：", "The rules still have problems: ") + pick(
                    self.lang, "；", "; ").join(problems)
            elif sample.strip():
                note = try_rules(body, sample, self.lang)
            else:
                note = pick(self.lang, "规则本身没问题。写一句话试试它会怎么回应。",
                            "The rules hold up. Try a sentence to see how the gate answers.")
        with self._lock:
            self._rules_note = note

    # -- the Gate page: how it thinks, and the optional thinker ------------------------------

    def think_through(self, rules_text: str, sample: str, hour: str) -> None:
        """Show how the gate thinks about ``sample`` (under the draft rules when they read; the built-in gate if
        not), at an afternoon hour or in the small hours."""
        body = None
        if rules_text.strip():
            try:
                body = parse_rules(rules_text, self.lang)
            except ValueError:
                body = None
        view = think_through(body, sample, "night" if hour == "night" else "day", self.lang, now=self.clock(),
                             cortex=cortex_at(self._cortex_path()))
        with self._lock:
            self._trial = view

    def _cortex_path(self):
        profile = getattr(self.controller, "profile", None)
        return getattr(profile, "cortex", None)

    def _mind_view(self) -> dict:
        """The cortex itself, for its page: re-read at most every few seconds (it changes when it learns or grows)."""
        now = time.monotonic()
        cached = getattr(self, "_mind_cache", None)
        if cached is not None and now - cached[0] < 5.0 and cached[2] == self.lang:
            return cached[1]
        try:
            view = mind_view(self._cortex_path(), self.lang)
        except Exception:  # noqa: BLE001 - the page shows nothing rather than failing the whole window
            view = {}
        self._mind_cache = (now, view, self.lang)
        return view

    def _thinker_path(self):
        profile = getattr(self.controller, "profile", None)
        return getattr(profile, "thinker_config", None)

    def _thinker_view(self) -> dict:
        from secdogie_citadel.thinker import load_config

        path = self._thinker_path()
        view = load_config(path).view() if path is not None else {"provider": "anthropic", "model": "claude-opus-5-5",
                                                                  "base": "", "hint": "", "on": False}
        return {**view, "note": self._thinker_note}

    def set_thinker(self, provider: str, model: str, base: str, key: str) -> None:
        """Save the thinker's settings (owner-only file). An empty key keeps the one already saved."""
        from secdogie_citadel.thinker import PROVIDERS, ThinkerConfig, load_config, save_config

        path = self._thinker_path()
        if path is None:
            return
        old = load_config(path)
        provider = provider if provider in PROVIDERS else "anthropic"
        cfg = ThinkerConfig(provider, model.strip(), base.strip(), key.strip() or old.api_key)
        save_config(path, cfg)
        self._thinker_note = (pick(self.lang, "接上了。它没把握的事，会先看你的 AI 模型怎么做，再学会自己做。",
                                   "Connected. Whatever it is unsure of, it watches your AI model handle first, then learns to handle "
                                   "itself.") if cfg.on else
                              pick(self.lang, "存好了；还差一把钥匙才接得上。", "Saved; it needs a key before it is connected."))

    def clear_thinker(self) -> None:
        from secdogie_citadel.thinker import remove_config

        path = self._thinker_path()
        if path is not None:
            remove_config(path)
        self._thinker_note = pick(self.lang, "断开了，钥匙也删了。它学会的东西还在。", "Disconnected, and the key is gone. What it learned stays.")

    def test_thinker(self) -> None:
        """Ask your AI model once, in the cortex's fixed format, and say what came back."""
        from secdogie_citadel.cortex import questions as Q
        from secdogie_citadel.thinker import Thinker, load_config

        path = self._thinker_path()
        if path is None:
            return
        thinker = Thinker(load_config(path))
        if not thinker.on:
            self._thinker_note = pick(self.lang, "还没有钥匙，没接上。", "There is no key yet, so it is not connected.")
            return
        lang = self.lang

        def ask():
            sample = "凌晨两点，气死我了，给前任发消息说我恨你"
            got = thinker.teach(sample)
            if got is None:
                note = pick(lang, f"没有得到能用的回答（{thinker.last}）。它照旧自己想。", f"No usable answer ({thinker.last}). It thinks on its own as before.")
            else:
                answers, reply = got
                read = Q.BY_ID["effect"].meaning(answers["effect"], lang)
                note = pick(lang, f"你的模型按同一套问题回答了（比如「会改变什么」：{read}）。", f"Your model answered in the same format (for "
                            f"instance, what it would change: {read}).") + ((" " + reply) if reply else "")
            self._thinker_note = note

        self._work(pick(self.lang, "正在问你的 AI 模型", "Asking your AI model"), ask)

    def push_rules(self, text: str, passphrase: str) -> None:
        self.rules_text = text
        companion = self.companion
        if companion is None:
            return
        if self.devices is not None and self.devices.snapshot().get("role") == "member":
            with self._lock:
                self._rules_note = pick(self.lang, "这台设备在别人的设备网里：规则由设备网的主人签名，再传到这里。",
                                        "This device belongs to someone else's mesh: its owner signs the rules, "
                                        "and they travel here.")
            return
        try:
            body = parse_rules(text, self.lang)
        except ValueError as e:
            with self._lock:
                self._rules_note = str(e)
            return

        def push():
            ok, note = companion.push_rules(body, passphrase)
            if ok and self.devices is not None:
                chain = companion.policy.chain(timeout=5.0) or []
                if chain and chain[0].get("version") == 1:
                    self.devices.spread(chain)  # every device of yours gets what it lacks of it
                    if self.devices.snapshot().get("devices"):
                        note += pick(self.lang, " 它会传到你的每一台设备上。", " It travels to each of your devices.")
            with self._lock:
                self._rules_note = note
            if ok:
                self.refresh_in_force()

        self._work(pick(self.lang, "正在注入规则", "Injecting the rules"), push)

    def _rules_arrived(self, chain: list[dict]) -> None:
        """The owner's rules came from another of your devices: offer what this node lacks, in order, then tell
        the other devices what it holds. Off the devices loop (the node may take a moment to answer)."""
        companion, devices = self.companion, self.devices
        if companion is None or devices is None:
            return

        def offer():
            from .companion import explain_errors

            listing = companion.rules_in_force(timeout=5.0) or {}
            held = next((a for a in listing.get("active", []) if a.get("kind") == "gate-rules"
                         and a.get("scope") == {"nodes": [], "roles": []}), None)
            have = held["version"] if held else 0
            active, errors = listing.get("active", []), []
            for b in chain:
                if b.get("version", 0) <= have:
                    continue
                reply = companion.policy.offer(b, timeout=8.0)
                if reply is None:
                    errors = [pick(self.lang, "节点没有回应。", "The node did not answer.")]
                    break
                active = reply.get("active", active)
                if not reply.get("ok"):
                    words = explain_errors(reply.get("errors", []), self.lang)
                    if any(e.startswith("chain:") for e in reply.get("errors", [])):
                        words = [pick(self.lang, "这台设备有自己的规则，和主人的对不上。", "This device has rules of its own "
                                      "that do not follow the owner's.")]
                    errors = words
                    break
            devices.report(active, errors)
            with self._lock:
                self._rules_note = pick(self.lang, f"你另一台设备上签的第 {len(chain)} 版规则到了。",
                                        f"Version {len(chain)} of the rules came from another of your devices.") \
                    if not errors else pick(self.lang, "另一台设备传来的规则没有生效：", "The rules from another device "
                                            "did not take effect: ") + pick(self.lang, "；", "; ").join(errors)
            self.refresh_in_force()

        threading.Thread(target=offer, daemon=True, name="studio-rules-arrived").start()

    # -- look, and your own devices -------------------------------------------------------

    def set_theme(self, theme: str) -> None:
        if theme not in THEMES:
            return
        self.theme = theme
        root = getattr(getattr(self.controller, "profile", None), "root", None)
        if root is not None:  # before there is a profile, the choice holds for this session
            set_ui(root, theme=theme)

    def _devices(self, method: str, *args) -> None:
        devices = self.devices
        if devices is None:
            return
        if method in ("write_invite", "agree", "go_online"):  # these touch keys or the network: off the UI thread
            threading.Thread(target=getattr(devices, method), args=args, daemon=True,
                             name=f"studio-{method}").start()
        else:
            getattr(devices, method)(*args)

    def write_invite(self, passphrase: str, signal: str, page: str) -> None:
        self._devices("write_invite", passphrase, signal, page)

    def decide_pairing(self, admit: bool) -> None:
        self._devices("decide", admit)

    def withdraw_invite(self) -> None:
        self._devices("withdraw")

    def read_invite(self, text: str) -> None:
        self._devices("read_invite", text)

    def agree_invite(self) -> None:
        self._devices("agree")

    def reset_join(self) -> None:
        self._devices("reset_join")

    def go_online(self, signal: str) -> None:
        self._devices("go_online", signal)

    def go_offline(self) -> None:
        self._devices("go_offline")

    def say_to_devices(self, text: str) -> None:
        self._devices("say", text)

    def set_device_name(self, name: str) -> None:
        self._devices("set_device_name", name)

    def shutdown(self) -> None:
        if self.devices is not None:
            self.devices.close()
        if self.companion is not None:
            self.companion.close()
            self.companion = None
        self.controller.shutdown()


def bridge_class():
    """The QObject the QML reads as ``P``: the presence's tick as properties,
    its actions as slots. Built on demand so this module imports without Qt."""
    from PySide6.QtCore import Property, QObject, Signal, Slot
    from PySide6.QtGui import QGuiApplication

    names = ("lang", "onboarding", "you", "greeting", "keys", "homeOpen", "homeCard", "homeCardKey", "waiting", "conversation", "node", "mood",
             "card", "cardKey",
             "whisper", "whisperTone", "held", "feedback", "working", "innerOpen", "statusRows", "logText",
             "rulesText", "rulesNote", "inForce", "theme", "devices", "present", "shichen", "clock", "hereToday", "hereBars",
             "dayQuarters", "quarterNow", "trial", "thinker", "mind")
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
        "finishOnboarding": ((), Presence.finish_onboarding), "setLanguage": ((str,), Presence.set_language),
        "answer": ((str, str), Presence.answer), "approve": ((str, str), Presence.approve),
        "deny": ((str,), Presence.deny), "remember": ((str, bool), Presence.remember),
        "say": ((str,), Presence.say), "openHome": ((), Presence.open_home),
        "closeHome": ((), Presence.close_home), "openRules": ((), Presence.open_rules),
        "toggleInner": ((), Presence.toggle_inner), "tryRules": ((str, str), Presence.try_rules),
        "pushRules": ((str, str), Presence.push_rules),
        "thinkThrough": ((str, str, str), Presence.think_through),
        "setThinker": ((str, str, str, str), Presence.set_thinker), "clearThinker": ((), Presence.clear_thinker),
        "testThinker": ((), Presence.test_thinker),
        "setTheme": ((str,), Presence.set_theme), "writeInvite": ((str, str, str), Presence.write_invite),
        "decidePairing": ((bool,), Presence.decide_pairing), "withdrawInvite": ((), Presence.withdraw_invite),
        "readInvite": ((str,), Presence.read_invite), "agreeInvite": ((), Presence.agree_invite),
        "resetJoin": ((), Presence.reset_join), "goOnline": ((str,), Presence.go_online),
        "goOffline": ((), Presence.go_offline), "sayToDevices": ((str,), Presence.say_to_devices),
        "setDeviceName": ((str,), Presence.set_device_name),
    }
    for name, (types, fn) in slots.items():
        ns[name] = Slot(*types, name=name)(act(fn))
    ns["rest"] = Slot(name="rest")(lambda self: QGuiApplication.quit())
    ns["copy"] = Slot(str, name="copy")(lambda self, text: QGuiApplication.clipboard().setText(text))
    ns["hasTray"] = Property(bool, lambda self: bool(getattr(self.presence, "has_tray", False)), notify=changed)
    return type("Bridge", (QObject,), ns)


def load_fonts(app) -> None:
    """Studio's own face for every window: Google Sans, with the platform's CJK sans behind it."""
    from PySide6.QtGui import QFont, QFontDatabase

    for face in sorted((QML_DIR / "fonts").glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(face))
    font = QFont()
    font.setFamilies(FONT_FAMILIES)
    app.setFont(font)


def _tray_for(app, bridge, presence):
    """The companion in the task bar while its window is away. None where the desktop has no tray."""
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QMenu, QSystemTrayIcon

    if not QSystemTrayIcon.isSystemTrayAvailable():
        return None
    icon = QIcon(str(QML_DIR / "mark.png"))
    tray = QSystemTrayIcon(icon)
    menu = QMenu()
    menu.addAction(pick(presence.lang, "打开 secdogie", "Open secdogie"), presence.open_home)
    menu.addSeparator()
    menu.addAction(pick(presence.lang, "让我休息", "Let it rest"), app.quit)
    tray.setContextMenu(menu)
    tray.setToolTip("secdogie")
    # A click on the tray brings the window back. It is shown, never raised over what the person is doing.
    tray.activated.connect(lambda reason: presence.open_home()
                           if reason != QSystemTrayIcon.ActivationReason.Context else None)
    tray.show()
    return tray


def run_gui(root: Path, *, kdf: str = "moderate") -> int:
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtGui import QIcon
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtWidgets import QApplication

    Bridge = bridge_class()

    # QApplication, not QGuiApplication: the task-bar icon is a widget class.
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("secdogie")
    app.setDesktopFileName("secdogie-studio")
    app.setWindowIcon(QIcon(str(QML_DIR / "mark.png")))
    app.setQuitOnLastWindowClosed(False)  # closing the window puts it away; "让我休息" quits
    load_fonts(app)
    presence = Presence(StudioController(root, kdf=kdf))
    bridge = Bridge(presence, reduce_motion(app))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("P", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_DIR / "Home.qml")))
    if len(engine.rootObjects()) != 1:
        presence.shutdown()
        return 1
    tray = _tray_for(app, bridge, presence)
    presence.has_tray = tray is not None
    timer = QTimer()
    timer.timeout.connect(bridge.refresh)
    timer.start(250)
    app.aboutToQuit.connect(presence.shutdown)
    code = app.exec()
    timer.stop()
    if tray is not None:
        tray.hide()
    del engine  # the engine goes before the bridge it reads, so nothing reads a dead object
    return code


__all__ = ["Presence", "QML_DIR", "WELCOME", "bridge_class", "card_dict", "load_fonts", "run_gui"]
