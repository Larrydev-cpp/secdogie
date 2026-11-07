"""The companion's side of the conversation with its node.

Studio holds one long-lived operator dialogue with its own node (the same
DID-authenticated session the Dialogue App uses) plus the ``policy/v1``
channel. This module turns what arrives into what the window shows, and turns
what the person does into what goes back:

  a Socratic question (Gate 1)      -> an ask card: the question, its options, free text
  a Gate 2 challenge                -> a sign card: what cannot be undone, the six
                                       fields it commits to, the passphrase
  a memory the node would keep      -> a memory card: keep it, or not
  "note: ..." / "hold: ..." lines   -> whispers (calm / warm)
  "letter: ..." (a refusal at length) -> its first paragraph as a warm whisper; the
                                       whole letter in the home window's conversation
  everything else                   -> the inner log only

Gate 2 is the operator's key, unlocked with the passphrase for one signature
and dropped. The challenge is reviewed locally (hash recomputed, node and
expiry checked) before the passphrase is even asked for, as in the Dialogue
App. Rules are drafted in YAML, tried against a sample instruction locally,
then signed with the operator key and offered to the node, which verifies them
itself.
"""
from __future__ import annotations

import re
import time

import yaml
from secdogie_citadel.decision import HOLD, DecisionModel
from secdogie_citadel.rules import GateRules
from secdogie_dialogue.app import AppController
from secdogie_dialogue.guard import GuardRefusal
from secdogie_dialogue.keystore import KeystoreError, unseal_identity
from secdogie_dialogue.protocol import DialogueType
from secdogie_dialogue.session import DialogueSession, SessionRouter
from secdogie_identity import Allowlist, Identity
from secdogie_node.admin import AdminClient
from secdogie_node.policy_host import PolicyClient
from secdogie_policy import assemble, check_bundle, create_bundle, sign_bundle
from secdogie_transport import ChannelMux, DirectUDPTransport, Endpoint, PeerIdentity, Session, UDPChannel

from .nodeproc import Ready
from .presence import GAP_URGENCY, SOON, Moment, Whisper
from .profile import Profile

KEEP, FORGET = "记住吧", "别记"

_CLASS_WORDS = {"fact": "事实", "preference": "偏好", "caution": "要小心的事"}
_SOURCE_WORDS = {"model": "我自己的判断", "operator": "你说的", "evidence": "几次经历"}


def rollback_words(explanation: str) -> str:
    """The node's risk line, in the companion's words: what backs this step out."""
    _, _, rollback = explanation.partition("; rollback: ")
    if not rollback or rollback == "none stated":
        return "没说怎么撤回"
    if rollback.startswith("none: declared irreversible"):
        return "撤不回来（已明确说明）"
    return rollback

RULES_TEMPLATE = """\
# 门的规则：只能更严。先试一试，再签名注入。
instruction:          # Gate 1 · 指令
  max_len: 600        # 一句话最长（≤600）
  max_steps: 6        # 最多几个「然后」
  revise_on:          # 遇到这些说法先问清楚
    - phrases: ["批量注册"]
      reason: "批量注册账号不是我们要一起做的事"
      suggestion: "说说你要处理的那一个账号"
plan:                 # Gate 1 · 计划
  require_intent: true
  blocked_kinds: []   # 不做的动作，如 [run]
  extra_destructive_kinds: []  # 要你签名的，如 [save]
# presence:           # 何时找你（只能打开）
#   ask_before_new_goal: true
#   note_every_mutation: true
"""

_CODE_WORDS = {
    "loosens": "比内置规则更松（只能更严）",
    "unknown-field": "不认识这个字段",
    "range": "超出允许的范围",
    "type": "类型不对",
    "format": "格式不对",
    "not-normalized": "短语要先规范化（半角、小写、单空格）",
    "order": "列表要排好序、不重复",
    "missing": "缺少这一项",
    "conflict": "和其他项矛盾",
    "value": "不是允许的取值",
}

_REPLY_WORDS = {
    "unauthorized:sigs": "签名不被节点认可（这把钥匙不是它的操作员）",
    "mesh:mesh": "不是这个网格的规则",
    "scope:scope": "范围里没有这台节点",
    "chain:version": "版本号没接上",
    "chain:supersedes": "没有接在当前生效的规则之后",
    "not-yet:not_before": "还没到生效时间",
}


def explain_errors(errors) -> list[str]:
    out = []
    for e in errors:
        code, _, path = e.partition(":")
        if e in _REPLY_WORDS:
            out.append(_REPLY_WORDS[e])
        else:
            out.append(f"{path.removeprefix('body.')}：{_CODE_WORDS.get(code, code)}")
    return out


def short(did: str) -> str:
    return did if len(did) <= 20 else f"{did[:14]}…{did[-6:]}"


# ---- what arrives -> what the window shows ---------------------------------------------


def moments_from(probes, challenges, memories, arrived: dict, now: float) -> list[Moment]:
    """Cards for what waits on the person. ``arrived`` remembers when each was
    first seen (so a held card keeps its place in line)."""
    out = []
    for p in probes:
        mid = f"probe:{p.probe_id}"
        out.append(Moment(mid, "ask", p.content, tuple(p.suggested_options), free_text=True, urgency=SOON,
                          arrived=arrived.setdefault(mid, now), label="想问你一件事"))
    for pc in challenges:
        c = pc.challenge
        a = c.target_action
        mid = f"sign:{c.challenge_id}"
        details = [("动作", a.kind), ("对象", a.target_name or a.target_id or a.text or "—"),
                   ("回退", rollback_words(c.risk_explanation)), ("节点", short(c.subject_did)),
                   ("有效到", time.strftime("%H:%M:%S", time.localtime(c.expires_at))),
                   ("指纹", pc.review.local_hash[:16])]
        if pc.review.problems:
            details.append(("不能签", "；".join(pc.review.problems)))
        irreversible = getattr(c.risk_level, "value", c.risk_level) == "irreversible"
        says = "这一步做了就改不回来。" if irreversible else "这一步风险很高，做之前要你点头。"
        out.append(Moment(mid, "sign", says, ("签名同意", "先不"), tuple(details), urgency=SOON,
                          arrived=arrived.setdefault(mid, now), expires=c.expires_at,
                          can_approve=pc.review.signable, label="需要你的签名"))
    for m in memories:
        pkt = m.packet
        mid = f"memory:{pkt.memory_id}"
        details = [("类别", _CLASS_WORDS.get(pkt.mclass, pkt.mclass)), ("来源", _SOURCE_WORDS.get(pkt.source, pkt.source))]
        if m.problems:
            details.append(("不能记", "；".join(m.problems)))
        out.append(Moment(mid, "memory", f"我想记住「{pkt.key}」：{pkt.value}", (KEEP, FORGET), tuple(details),
                          urgency=GAP_URGENCY, arrived=arrived.setdefault(mid, now),
                          can_approve=m.confirmable, label="想记住一件事"))
    return out


def line(who: str, text: str, tone: str = "", *, key: str = "") -> dict:
    return {"who": who, "text": text, "tone": tone, "key": key}


_FINISHED = re.compile(r"^goal (\S+) finished: exit (-?\d+)")


def line_from(entry, titles: dict[str, str]) -> dict | None:
    """One transcript entry as a conversation line, in the companion's words;
    None for what the conversation does not show (replies to requests)."""
    if entry.who == "you":
        return line("you", entry.text)
    if entry.who == "app":
        return line("note", entry.text)
    if entry.text.startswith("letter: "):
        return line("it", entry.text[len("letter: "):], "letter")
    if entry.text == "answer adopted":
        return None
    if entry.text.startswith("no answer in time"):
        return line("note", "没等到你的回答，这件事先放着。")
    if entry.kind is DialogueType.SOCRATIC_QUESTION:
        return line("it", entry.text, "ask")
    for prefix, tone in (("note: ", "calm"), ("hold: ", "warm")):
        if entry.text.startswith(prefix):
            return line("it", entry.text[len(prefix):], tone)
    done = _FINISHED.match(entry.text)
    if done:
        what = titles.get(done.group(1))
        what = f"「{what}」" if what else "这件事"
        if done.group(2) == "0":
            return line("it", f"{what}做完了。", "calm")
        return line("it", f"{what}没做成（退出码 {done.group(2)}）。", "warm")
    if entry.text.startswith(("accepted: ", "refused: ")):
        return None
    return line("note", entry.text)


def whisper_from(entry, index: int, now: float) -> Whisper | None:
    if entry.kind is not DialogueType.SYSTEM_STATUS:
        return None
    for prefix, tone in (("note: ", "calm"), ("hold: ", "warm")):
        if entry.text.startswith(prefix):
            return Whisper(f"w{index}", entry.text[len(prefix):], tone, now)
    if entry.text.startswith("letter: "):  # the first paragraph in passing; the rest is read in the home window
        return Whisper(f"w{index}", entry.text[len("letter: "):].split("\n\n", 1)[0], "warm", now)
    return None


# ---- the session --------------------------------------------------------------------------


class Companion:
    def __init__(self, profile: Profile, ready: Ready, *, clock=time.time):
        self.profile = profile
        self.ready = ready
        self._clock = clock
        identity = Identity.load(profile.app_key)
        trust = Allowlist({ready.did})
        self.channel = UDPChannel("127.0.0.1", 0)
        try:
            transport = DirectUDPTransport(identity, self.channel, allowlist=trust)
            transport.set_peer_endpoint(ready.did, ready.dial_host, ready.port)
            mux = ChannelMux(transport, Session("studio", PeerIdentity(identity.did, ""),
                                                active=Endpoint("local", *self.channel.address)))
            router = SessionRouter(mux)
            session = router.add(DialogueSession(identity, ready.did, router.sender_for(ready.did), trust=trust))
            self.ctl = AppController(session)
            self.policy = PolicyClient(mux, ready.did)
            self.admin = AdminClient(mux, ready.did)
            session.start(0.05)
            self.ctl.start()
        except Exception:
            self.channel.close()
            raise
        self._arrived: dict[str, float] = {}
        self._seen = 0
        self._said: list[tuple[int, str]] = []  # (transcript length when said, what the person said)
        self._titles: dict[str, str] = {}  # goal id -> what the person asked for

    def close(self) -> None:
        self.ctl.close()
        self.channel.close()

    # -- reading -------------------------------------------------------------------------

    def moments(self) -> list[Moment]:
        probes, challenges, memories = self.ctl.outstanding()
        return moments_from(probes, challenges, memories, self._arrived, self._clock())

    def new_whispers(self) -> list[Whisper]:
        entries = self.ctl.transcript()
        out = []
        for i in range(self._seen, len(entries)):
            w = whisper_from(entries[i], i, self._clock())
            if w is not None:
                out.append(w)
        self._seen = len(entries)
        return out

    def busy(self) -> bool:
        started = finished = 0
        for e in self.ctl.transcript():
            if e.kind is DialogueType.SYSTEM_STATUS:
                started += e.text.startswith("note: 好，我开始做")
                finished += e.text.startswith("goal ") and " finished: " in e.text
        return started > finished

    def log_lines(self, n: int = 40) -> list[str]:
        def plain(text: str) -> str:
            for prefix in ("note: ", "hold: "):
                if text.startswith(prefix):
                    return text[len(prefix):]
            return text

        return [f"{'它' if e.who == 'agent' else '你'}：{plain(e.text)}" for e in self.ctl.transcript()[-n:]]

    def conversation(self, n: int = 200) -> list[dict]:
        """The conversation as the home window shows it, oldest first: what the
        person said, what the companion said, and quiet notes in between. The
        last ``n`` lines, each with a stable ``key`` (so the window appends
        what is new instead of redrawing). A question still waiting for its
        answer is left out: the card above the input line asks it."""
        entries = self.ctl.transcript()
        waiting = {p.probe_id for p in self.ctl.outstanding()[0]}
        said = list(self._said)
        out: list[dict] = []
        j = 0
        for i, e in enumerate(entries):
            while j < len(said) and said[j][0] <= i:
                out.append(line("you", said[j][1], key=f"s{j}"))
                j += 1
            if e.kind is DialogueType.SOCRATIC_QUESTION and e.probe_id in waiting:
                continue
            shown = line_from(e, self._titles)
            if shown is not None:
                out.append({**shown, "key": f"t{i}"})
        out.extend(line("you", text, key=f"s{k}") for k, (_, text) in enumerate(said) if k >= j)
        return out[-n:]

    def node_status(self, timeout: float = 3.0) -> dict | None:
        return self.admin.status(timeout=timeout)

    @property
    def connected(self) -> bool:
        return bool(self.ctl.peer_up)

    # -- acting ---------------------------------------------------------------------------

    def say(self, text: str) -> None:
        text = text.strip()
        if text:
            self._said.append((len(self.ctl.transcript()), text))
            pkt = self.ctl.add_goal(text)
            self._titles[pkt.goal_id] = text

    def answer(self, moment_id: str, text: str) -> str | None:
        if not moment_id.startswith("probe:") or not text.strip():
            return "没有可以回答的问题"
        try:
            self.ctl.answer(moment_id.removeprefix("probe:"), text.strip())
        except ValueError as e:
            return str(e)
        return None

    def approve(self, moment_id: str, passphrase: str) -> str | None:
        """Sign a Gate 2 challenge. Returns None when signed, else why not."""
        cid = moment_id.removeprefix("sign:")
        try:
            self.ctl.approve(cid, lambda: unseal_identity(self.profile.operator_keystore, passphrase.encode("utf-8")))
        except KeystoreError:
            return "口令不对，再输一次。"
        except GuardRefusal as e:
            return f"这一步不能签：{e}"
        except ValueError as e:
            return str(e)
        return None

    def deny(self, moment_id: str) -> None:
        try:
            self.ctl.deny(moment_id.removeprefix("sign:"))
        except ValueError:
            pass

    def remember(self, moment_id: str, keep: bool) -> str | None:
        mid = moment_id.removeprefix("memory:")
        try:
            if keep:
                self.ctl.confirm_memory(mid)
            else:
                self.ctl.dismiss_memory(mid)
        except ValueError as e:
            return str(e)
        return None

    # -- rules -----------------------------------------------------------------------------

    def rules_in_force(self, timeout: float = 5.0) -> dict | None:
        reply = self.policy.list(timeout=timeout)
        return reply if reply is not None and reply.get("ok") else None

    def push_rules(self, body: dict, passphrase: str, *, timeout: float = 10.0) -> tuple[bool, str]:
        """Sign ``body`` as the next gate-rules version for the whole mesh and
        offer it to the node. Returns (in force?, what to tell the person)."""
        problems = check_rules(body, self.mesh)
        if problems:
            return False, "规则还有问题：" + "；".join(problems)
        listing = self.rules_in_force(timeout)
        if listing is None:
            return False, "联系不上节点。等它醒着的时候再签。"
        current = next((a for a in listing.get("active", []) if a["kind"] == "gate-rules"
                        and a["scope"] == {"nodes": [], "roles": []}), None)
        version = current["version"] + 1 if current else 1
        bundle = create_bundle("gate-rules", mesh=self.mesh, version=version, body=body,
                               supersedes=current["bundle_id"] if current else "")
        try:
            operator = unseal_identity(self.profile.operator_keystore, passphrase.encode("utf-8"))
        except KeystoreError:
            return False, "口令不对，再输一次。"
        reply = self.policy.offer(sign_bundle(operator, bundle), timeout=timeout)
        if reply is None:
            return False, "节点没有回应，过一会儿再签一次。"
        if reply.get("ok"):
            return True, f"第 {version} 版规则已经生效。"
        return False, "节点没有接受：" + "；".join(explain_errors(reply.get("errors", [])))

    @property
    def mesh(self) -> str:
        return self.profile.settings().get("mesh", "home")


def parse_rules(text: str) -> dict:
    """YAML (or JSON) to a gate-rules body; raises ValueError in plain words."""
    try:
        body = yaml.safe_load(text) if text.strip() else {}
    except yaml.YAMLError as e:
        raise ValueError(f"看不懂这段 YAML：{e}") from None
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise ValueError("规则应该是一组「名字: 值」")
    return body


def check_rules(body: dict, mesh: str) -> list[str]:
    """The schema's verdict on a draft body, in plain words (envelope fields
    are filled in at signing time and not judged here)."""
    errors = [e for e in check_bundle(assemble("gate-rules", mesh=mesh, version=1, body=body))
              if e.partition(":")[2].startswith("body")]
    return explain_errors(errors)


def try_rules(body: dict, sample: str) -> str:
    """What Gate 1 would say to ``sample`` under the draft rules."""
    rules = GateRules.from_bodies([("draft", body)])
    involvement = DecisionModel(rules).intake(sample, rules.review(sample))
    if involvement.disposition == HOLD:
        return f"我不会做：「{involvement.says}」"
    if involvement.needs_person:
        return f"我会先问你：「{involvement.says}」"
    return "通过：我会直接开始做。"


__all__ = ["Companion", "FORGET", "KEEP", "RULES_TEMPLATE", "check_rules", "explain_errors", "line_from", "moments_from",
           "parse_rules", "try_rules", "whisper_from"]
