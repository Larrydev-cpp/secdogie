"""The secdogie window, as logic: one conversation, and the cards in it.

``DialogModel`` turns the dialogue App's ``AppController`` into what the window
shows -- one message stream -- and turns what the operator does in the window
into controller calls. There is no tkinter here: ``window.py`` only draws
``messages()`` and calls these methods, so everything the window can do is
tested headless.

The stream, in the order things happened:

  * ``you``      -- a goal the operator sent, or an answer to a question;
  * ``node``     -- what the node said (untrusted text, cleaned for display);
  * ``notice``   -- a note from the window itself (the node went away, ...);
  * ``probe``    -- a Socratic question: answer it (an option, or typed text);
  * ``approval`` -- a Gate 2 challenge: approve it (with the passphrase) or deny;
  * ``memory``   -- a note the model wants remembered: remember it, or skip.

A card is open while the node waits on it. Once settled -- by the operator, by
expiry, or because the node went away -- it stays in the stream, closed, and
says how it ended. Typing in the composer answers the oldest open question if
there is one, and otherwise sends a new goal.

Gate 2 is unchanged: the operator key is unlocked only inside
``AppController.approve``, after the challenge has been reviewed, for one
signature. The first approval sets the passphrase (typed twice); the key is
made then, sealed, and only then trusted by the node. A wrong passphrase signs
nothing, a mismatched pair sets nothing, and there is no approve-all.
"""
from __future__ import annotations

import re
import secrets
import threading
import time
from dataclasses import dataclass, replace

from secdogie_dialogue.app import AppError
from secdogie_dialogue.dialogue import DialogueError
from secdogie_dialogue.guard import GuardRefusal
from secdogie_dialogue.inspector import clean
from secdogie_dialogue.keystore import KeystoreError
from secdogie_dialogue.protocol import DialogueType

from .local import passphrase_problem

YOU, NODE, NOTICE, PROBE, APPROVAL, MEMORY = "you", "node", "notice", "probe", "approval", "memory"
CARDS = (PROBE, APPROVAL, MEMORY)
OPEN = "open"
MAX_TEXT = 2000

# How a closed card ended, as the window says it.
STATE_LABELS = {
    "answered": "已回答",
    "closed": "已关闭：节点不再等这个回答",
    "approved": "已批准（操作员签名）",
    "denied": "已拒绝",
    "expired": "没有批准：节点拒绝了这一步",
    "remembered": "已记住",
    "skipped": "没有记（留在隔离区，到期删除）",
    "withdrawn": "节点已撤回",
}

WELCOME = ("告诉 secdogie 你想在这台电脑上做什么。高风险的步骤会在这里请你批准，"
           "需要澄清时它会在这里问你；你随时可以停止。")

_FINISHED = re.compile(r"^goal (\S+) finished")


class DialogError(Exception):
    """An operator action the window refuses; the message is for the operator."""


@dataclass(frozen=True)
class Message:
    key: str  # stable for the life of the window: the view diffs on it
    kind: str  # YOU / NODE / NOTICE / PROBE / APPROVAL / MEMORY
    text: str  # cleaned: never raw node text
    ref: str = ""  # the probe / challenge / memory / goal id it is about
    options: tuple[str, ...] = ()  # a probe's suggested answers
    detail: tuple[str, ...] = ()  # a card's further lines
    state: str = ""  # a card: OPEN, or how it ended (a STATE_LABELS key)
    actions: tuple[str, ...] = ()  # what an open card offers now
    expires_at: float = 0.0  # an approval card: when the node gives up and refuses


def diff_messages(prev, curr) -> tuple[list[Message], list[Message]]:
    """What the view must draw: messages new in ``curr`` (in order) and
    messages whose content or state changed. Pure; identical streams give
    ([], [])."""
    before = {m.key: m for m in prev}
    added = [m for m in curr if m.key not in before]
    changed = [m for m in curr if m.key in before and before[m.key] != m]
    return added, changed


def new_goal_id(ms: int) -> str:
    """A goal id for millisecond ``ms``. Fixed width, so ids sort as their
    times do (the node runs ready goals in id order); a random tail keeps two
    windows' ids apart."""
    return f"g{int(ms):013d}-{secrets.token_hex(3)}"


def _approval_lines(pc) -> tuple[str, tuple[str, ...]]:
    c, a, r = pc.challenge, pc.challenge.target_action, pc.review
    target = " ".join(p for p in (clean(a.target_role, 40), f'"{clean(a.target_name, 60)}"' if a.target_name
                                  else "", f"#{clean(a.target_id, 40)}" if a.target_id else "") if p)
    head = f"高风险（{c.risk_level.value}）：{clean(a.kind, 40)} {target}".rstrip()
    detail = []
    if a.text:
        detail.append(f"内容：{clean(a.text, 200)}")
    detail.append(f"为什么危险：{clean(c.risk_explanation, 300)}")
    detail.append(f"操作哈希 {r.local_hash[:16]}… " + ("（本机重算：一致）" if r.hash_matches
                                                     else "（本机重算：不一致）"))
    detail.extend(f"✗ {p}" for p in r.problems)
    return head, tuple(detail)


def _memory_lines(m) -> tuple[str, tuple[str, ...]]:
    p = m.packet
    head = f"要记住吗（{clean(p.mclass, 12)}，{clean(p.scope, 40)}）：{clean(p.key, 60)} = {clean(p.value, 300)}"
    return head, tuple(f"✗ {x}" for x in m.problems)


class DialogModel:
    """``controller`` is the App's ``AppController``; ``operator_key`` an
    ``OperatorKey`` (``local.py``); ``api_keys`` an ``ApiKeys``, or None when
    the window does not manage the model key."""

    def __init__(self, controller, operator_key, api_keys=None, *, label: str = "本机节点", clock=time.time):
        self.controller = controller
        self.label = label  # how the status line names the node
        self.key = operator_key
        self.api_keys = api_keys
        self._clock = clock
        self._lock = threading.RLock()
        self._stream: list[Message] = []  # in the order seen; card state is filled in by messages()
        self._keys: set[str] = set()
        self._entries = 0  # transcript entries already in the stream
        self._outcomes: dict[str, str] = {}  # card key -> how the operator settled it
        self._goals: dict[str, str] = {}  # goal id -> its add_goal request id, in the order sent
        self._finished: set[str] = set()
        self._stopped: set[str] = set()
        self._local = 0  # bumped on every change made here
        self._last_ms = 0  # goal ids strictly increase, even within one millisecond
        self._seen = (-1, -1)

    # -- catching up --------------------------------------------------------------

    def refresh(self, now: float | None = None) -> bool:
        """Catch up with the controller (and expire what is past its time).
        True when anything the window shows may have changed."""
        self.controller.tick(now)
        with self._lock:
            entries = self.controller.transcript()
            for i in range(self._entries, len(entries)):
                self._entry(i, entries[i])
            self._entries = len(entries)
            for pc in self.controller.challenges():
                key = f"{APPROVAL}:{pc.challenge.challenge_id}"
                if key not in self._keys:
                    head, detail = _approval_lines(pc)
                    self._append(Message(key, APPROVAL, head, ref=pc.challenge.challenge_id, detail=detail,
                                         expires_at=pc.challenge.expires_at))
            for m in self.controller.memories():
                key = f"{MEMORY}:{m.packet.memory_id}"
                if key not in self._keys:
                    head, detail = _memory_lines(m)
                    self._append(Message(key, MEMORY, head, ref=m.packet.memory_id, detail=detail))
            seen = (self.controller.version, self._local)
            changed, self._seen = seen != self._seen, seen
            return changed

    def _entry(self, i: int, e) -> None:
        text = clean(e.text, MAX_TEXT)
        if e.who == "agent" and e.kind is DialogueType.SOCRATIC_QUESTION:
            self._append(Message(f"{PROBE}:{e.probe_id}", PROBE, text, ref=e.probe_id,
                                 options=tuple(clean(o, 200) for o in e.options)))
            return
        if e.who == "agent":
            done = _FINISHED.match(e.text)
            if done:
                self._finished.add(done.group(1))
        kind = {"you": YOU, "agent": NODE}.get(e.who, NOTICE)
        self._append(Message(f"t:{i}", kind, text, ref=e.probe_id))

    def _append(self, msg: Message) -> None:
        if msg.key in self._keys:
            return
        self._keys.add(msg.key)
        self._stream.append(msg)
        self._local += 1

    # -- what the window shows ----------------------------------------------------------

    def messages(self) -> tuple[Message, ...]:
        """The stream, with every card's current state and actions."""
        with self._lock:
            pending = {p.probe_id for p in self.controller.pending_probes()}
            challenges = {pc.challenge.challenge_id: pc for pc in self.controller.challenges()}
            memories = {m.packet.memory_id: m for m in self.controller.memories()}
            out = []
            for m in self._stream:
                state, actions = "", ()
                if m.kind == PROBE:
                    if m.ref in pending:
                        state, actions = OPEN, ("answer",)
                    else:
                        state = self._outcomes.get(m.key, "closed")
                elif m.kind == APPROVAL:
                    pc = challenges.get(m.ref)
                    if pc is not None:
                        state, actions = OPEN, (("approve", "deny") if pc.review.signable else ("deny",))
                    else:
                        state = self._outcomes.get(m.key, "expired")
                elif m.kind == MEMORY:
                    offered = memories.get(m.ref)
                    if offered is not None:
                        state, actions = OPEN, (("remember", "skip") if offered.confirmable else ("skip",))
                    else:
                        state = self._outcomes.get(m.key, "withdrawn")
                out.append(replace(m, state=state, actions=actions) if state else m)
            return tuple(out)

    @property
    def passphrase_set(self) -> bool:
        return self.key.is_set

    def active_goals(self) -> list[str]:
        """Goals sent from here that the node took and has not finished (nor
        been told to stop), oldest first."""
        requests = self.controller.requests()
        with self._lock:
            out = []
            for gid, rid in self._goals.items():
                req = requests.get(rid)
                if req is not None and (req.undelivered or (req.reply or "").startswith("refused")):
                    continue
                if gid not in self._finished and gid not in self._stopped:
                    out.append(gid)
            return out

    def status(self) -> str:
        parts = [f"{self.label}已连接" if self.controller.peer_up else f"{self.label}没有响应"]
        waiting = [(len(self.controller.challenges()), "待批准"), (len(self.controller.pending_probes()), "待回答"),
                   (len(self.controller.memories()), "待确认记忆")]
        parts.extend(f"{n} 个{what}" for n, what in waiting if n)
        parts.append("口令已设置" if self.key.is_set else "口令未设置")
        return " · ".join(parts)

    def waiting(self) -> int:
        """How many cards wait on the operator: approvals, questions, notes."""
        c = self.controller
        return len(c.challenges()) + len(c.pending_probes()) + len(c.memories())

    def view_lines(self) -> list[str]:
        """The structural view the node sent (zero screenshots): a header line,
        then one line per element, focus marked with ★. Already cleaned."""
        return self.controller.inspector_lines()

    def composer_hint(self) -> str:
        pending = self.controller.pending_probes()
        if pending:
            return f"回答：{clean(pending[0].content, 80)}"
        return "告诉 secdogie 你想做什么，回车发送"

    # -- what the operator does ----------------------------------------------------------

    def send(self, text: str) -> str:
        """The composer. Answers the oldest open question if there is one
        (returns ``"answer"``), else sends ``text`` as a new goal (``"goal"``)."""
        text = (text or "").strip()
        if not text:
            raise DialogError("先写点什么。")
        pending = self.controller.pending_probes()
        if pending:
            self.answer(pending[0].probe_id, text)
            return "answer"
        self.add_goal(text)
        return "goal"

    def add_goal(self, title: str) -> str:
        title = (title or "").strip()
        if not title:
            raise DialogError("目标是空的。")
        with self._lock:  # the node's reply cannot reach the stream before the goal itself
            self._last_ms = max(int(self._clock() * 1000), self._last_ms + 1)
            gid = new_goal_id(self._last_ms)
            self._append(Message(f"goal:{gid}", YOU, clean(title, MAX_TEXT), ref=gid))
            pkt = self.controller.add_goal(title, gid)
            self._goals[gid] = pkt.request_id
        return gid

    def answer(self, probe_id: str, text: str | None = None, *, option: int | None = None) -> None:
        with self._lock:
            try:
                self.controller.answer(probe_id, text, option=option)
            except (AppError, DialogueError) as e:
                raise DialogError(f"没有发出回答：{e}") from None
            self._outcomes[f"{PROBE}:{probe_id}"] = "answered"
            self._local += 1

    def approve(self, challenge_id: str, passphrase: str, again: str | None = None) -> None:
        """Sign challenge ``challenge_id`` with the operator key. The first time,
        ``again`` must repeat ``passphrase``: the key is made and sealed under it.
        After that, ``passphrase`` unseals the key for this one signature. Blocks
        for the passphrase hashing (a second or so): call it off the UI thread."""
        setting = not self.key.is_set
        if setting:
            if again is None:
                raise DialogError("第一次批准要先设置口令：请输入两次。")
            problem = passphrase_problem(passphrase, again)
            if problem:
                raise DialogError(problem)

            def unlock():
                return self.key.create(passphrase, again)
        else:
            if not passphrase:
                raise DialogError("请输入口令。")

            def unlock():
                return self.key.unlock(passphrase)
        try:
            self.controller.approve(challenge_id, unlock)
        except KeystoreError as e:
            raise DialogError(str(e) if setting else "口令不对（或密钥文件被改动过），没有签名。") from None
        except GuardRefusal as e:
            raise DialogError(f"这一步不能签：{e}") from None
        except AppError as e:
            raise DialogError(f"这一步已经不在等待批准：{e}") from None
        with self._lock:
            self._outcomes[f"{APPROVAL}:{challenge_id}"] = "approved"
            self._local += 1

    def deny(self, challenge_id: str) -> None:
        try:
            self.controller.deny(challenge_id)
        except AppError as e:
            raise DialogError(f"这一步已经不在等待批准：{e}") from None
        with self._lock:
            self._outcomes[f"{APPROVAL}:{challenge_id}"] = "denied"
            self._local += 1

    def remember(self, memory_id: str) -> None:
        try:
            self.controller.confirm_memory(memory_id)
        except AppError as e:
            raise DialogError(f"不能确认这条记忆：{e}") from None
        with self._lock:
            self._outcomes[f"{MEMORY}:{memory_id}"] = "remembered"
            self._local += 1

    def skip(self, memory_id: str) -> None:
        try:
            self.controller.dismiss_memory(memory_id)
        except AppError as e:
            raise DialogError(f"这条记忆已经不在了：{e}") from None
        with self._lock:
            self._outcomes[f"{MEMORY}:{memory_id}"] = "skipped"
            self._local += 1

    def stop(self) -> list[str]:
        """Stop every goal sent from here that has not finished: the running
        one and any queued behind it. Returns their ids."""
        goals = self.active_goals()
        if not goals:
            raise DialogError("现在没有进行中的目标。")
        for gid in goals:
            self.controller.stop(gid)
        with self._lock:
            self._stopped.update(goals)
            self._append(Message(f"stop:{self._local}", YOU, "停止"))
        return goals

    # -- the model's API key ----------------------------------------------------------

    def api_key_needed(self) -> bool:
        return self.api_keys is not None and not self.api_keys.configured()

    def save_api_key(self, key: str, *, provider: str | None = None, model: str | None = None):
        if self.api_keys is None:
            raise DialogError("这个窗口不管理 API key。")
        problem = self.api_keys.problem(key)
        if problem:
            raise DialogError(problem)
        try:
            path = self.api_keys.save(key, provider=provider, model=model)
        except (OSError, ValueError) as e:
            raise DialogError(f"没有保存：{e}") from None
        with self._lock:
            self._local += 1
        return path


__all__ = [
    "APPROVAL",
    "CARDS",
    "MEMORY",
    "NODE",
    "NOTICE",
    "OPEN",
    "PROBE",
    "STATE_LABELS",
    "WELCOME",
    "YOU",
    "DialogError",
    "DialogModel",
    "Message",
    "diff_messages",
    "new_goal_id",
]
