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
from .words import pick

KEEP, FORGET = "记住吧", "别记"
REFUSE_SIGN = {"zh": "不能签", "en": "Can't sign"}  # a detail row the card shows in amber
REFUSE_KEEP = {"zh": "不能记", "en": "Can't keep"}

_CLASS_WORDS = {"zh": {"fact": "事实", "preference": "偏好", "caution": "要小心的事"},
                "en": {"fact": "a fact", "preference": "a preference", "caution": "a caution"}}
_SOURCE_WORDS = {"zh": {"model": "我自己的判断", "operator": "你说的", "evidence": "几次经历"},
                 "en": {"model": "my own judgment", "operator": "what you said", "evidence": "a few runs"}}


def rollback_words(explanation: str, lang: str = "zh") -> str:
    """The node's risk line, in the companion's words: what backs this step out."""
    _, _, rollback = explanation.partition("; rollback: ")
    if not rollback or rollback == "none stated":
        return pick(lang, "没说怎么撤回", "no way back was given")
    if rollback.startswith("none: declared irreversible"):
        return pick(lang, "撤不回来（已明确说明）", "cannot be undone (declared)")
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
    "zh": {
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
    },
    "en": {
        "loosens": "looser than the built-in rules (rules may only tighten)",
        "unknown-field": "not a field I know",
        "range": "out of the allowed range",
        "type": "the wrong type",
        "format": "the wrong format",
        "not-normalized": "phrases must be normalized first (half-width, lower case, single spaces)",
        "order": "lists must be sorted, without repeats",
        "missing": "this is missing",
        "conflict": "contradicts another entry",
        "value": "not an allowed value",
    },
}

_REPLY_WORDS = {
    "zh": {
        "unauthorized:sigs": "签名不被节点认可（这把钥匙不是它的操作员）",
        "mesh:mesh": "不是这个网格的规则",
        "scope:scope": "范围里没有这台节点",
        "chain:version": "版本号没接上",
        "chain:supersedes": "没有接在当前生效的规则之后",
        "not-yet:not_before": "还没到生效时间",
    },
    "en": {
        "unauthorized:sigs": "the node does not accept this signature (this key is not its operator)",
        "mesh:mesh": "these rules are for another mesh",
        "scope:scope": "this node is not in their scope",
        "chain:version": "the version does not follow on",
        "chain:supersedes": "they do not follow the rules in force",
        "not-yet:not_before": "not in force yet",
    },
}


def explain_errors(errors, lang: str = "zh") -> list[str]:
    replies, codes = _REPLY_WORDS[_l(lang)], _CODE_WORDS[_l(lang)]
    out = []
    for e in errors:
        code, _, path = e.partition(":")
        if e in replies:
            out.append(replies[e])
        else:
            out.append(f"{path.removeprefix('body.')}{pick(lang, '：', ': ')}{codes.get(code, code)}")
    return out


def _l(lang: str) -> str:
    return "en" if lang == "en" else "zh"


def short(did: str) -> str:
    return did if len(did) <= 20 else f"{did[:14]}…{did[-6:]}"


# ---- what arrives -> what the window shows ---------------------------------------------


def moments_from(probes, challenges, memories, arrived: dict, now: float, lang: str = "zh") -> list[Moment]:
    """Cards for what waits on the person. ``arrived`` remembers when each was
    first seen (so a held card keeps its place in line)."""
    out = []
    for p in probes:
        mid = f"probe:{p.probe_id}"
        out.append(Moment(mid, "ask", p.content, tuple(p.suggested_options), free_text=True, urgency=SOON,
                          arrived=arrived.setdefault(mid, now), label=pick(lang, "想问你一件事", "A question for you")))
    for pc in challenges:
        c = pc.challenge
        a = c.target_action
        mid = f"sign:{c.challenge_id}"
        details = [(pick(lang, "动作", "Action"), a.kind),
                   (pick(lang, "对象", "Target"), a.target_name or a.target_id or a.text or "—"),
                   (pick(lang, "回退", "Way back"), rollback_words(c.risk_explanation, lang)),
                   (pick(lang, "节点", "Node"), short(c.subject_did)),
                   (pick(lang, "有效到", "Valid until"), time.strftime("%H:%M:%S", time.localtime(c.expires_at))),
                   (pick(lang, "指纹", "Fingerprint"), pc.review.local_hash[:16])]
        if pc.review.problems:
            details.append((REFUSE_SIGN[_l(lang)], pick(lang, "；", "; ").join(pc.review.problems)))
        irreversible = getattr(c.risk_level, "value", c.risk_level) == "irreversible"
        says = (pick(lang, "这一步做了就改不回来。", "This step cannot be undone.") if irreversible
                else pick(lang, "这一步风险很高，做之前要你点头。", "This step is risky. It needs your yes first."))
        out.append(Moment(mid, "sign", says, ("签名同意", "先不"), tuple(details), urgency=SOON,
                          arrived=arrived.setdefault(mid, now), expires=c.expires_at,
                          can_approve=pc.review.signable, label=pick(lang, "需要你的签名", "Your signature is needed")))
    for m in memories:
        pkt = m.packet
        mid = f"memory:{pkt.memory_id}"
        details = [(pick(lang, "类别", "Kind"), _CLASS_WORDS[_l(lang)].get(pkt.mclass, pkt.mclass)),
                   (pick(lang, "来源", "From"), _SOURCE_WORDS[_l(lang)].get(pkt.source, pkt.source))]
        if m.problems:
            details.append((REFUSE_KEEP[_l(lang)], pick(lang, "；", "; ").join(m.problems)))
        says = pick(lang, f"我想记住「{pkt.key}」：{pkt.value}", f"I'd like to remember \u201c{pkt.key}\u201d: {pkt.value}")
        out.append(Moment(mid, "memory", says, (KEEP, FORGET), tuple(details),
                          urgency=GAP_URGENCY, arrived=arrived.setdefault(mid, now),
                          can_approve=m.confirmable, label=pick(lang, "想记住一件事", "Something to remember")))
    return out


def line(who: str, text: str, tone: str = "", *, key: str = "") -> dict:
    return {"who": who, "text": text, "tone": tone, "key": key}


_FINISHED = re.compile(r"^goal (\S+) finished: exit (-?\d+)")


def line_from(entry, titles: dict[str, str], lang: str = "zh") -> dict | None:
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
        return line("note", pick(lang, "没等到你的回答，这件事先放着。", "No answer came, so this waits."))
    if entry.kind is DialogueType.SOCRATIC_QUESTION:
        return line("it", entry.text, "ask")
    for prefix, tone in (("note: ", "calm"), ("hold: ", "warm")):
        if entry.text.startswith(prefix):
            return line("it", entry.text[len(prefix):], tone)
    done = _FINISHED.match(entry.text)
    if done:
        what = titles.get(done.group(1))
        if lang == "en":
            what = f"\u201c{what}\u201d" if what else "That"
            if done.group(2) == "0":
                return line("it", f"{what} is done.", "calm")
            return line("it", f"{what} did not get done (exit code {done.group(2)}).", "warm")
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
        self.lang = "zh"  # Studio's language for the words it composes (the node's own words are its own)
        self._said: list[tuple[int, str]] = []  # (transcript length when said, what the person said)
        self._titles: dict[str, str] = {}  # goal id -> what the person asked for

    def close(self) -> None:
        self.ctl.close()
        self.channel.close()

    # -- reading -------------------------------------------------------------------------

    def moments(self) -> list[Moment]:
        probes, challenges, memories = self.ctl.outstanding()
        return moments_from(probes, challenges, memories, self._arrived, self._clock(), self.lang)

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
            shown = line_from(e, self._titles, self.lang)
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
            return pick(self.lang, "没有可以回答的问题", "There is no question to answer")
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
            return pick(self.lang, "口令不对，再输一次。", "That passphrase is wrong. Type it again.")
        except GuardRefusal as e:
            return pick(self.lang, f"这一步不能签：{e}", f"This step cannot be signed: {e}")
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
        lang = self.lang
        problems = check_rules(body, self.mesh, lang)
        if problems:
            return False, pick(lang, "规则还有问题：", "The rules still have problems: ") + pick(lang, "；", "; ").join(problems)
        listing = self.rules_in_force(timeout)
        if listing is None:
            return False, pick(lang, "联系不上节点。等它醒着的时候再签。", "The node can't be reached. Sign when it is awake.")
        current = next((a for a in listing.get("active", []) if a["kind"] == "gate-rules"
                        and a["scope"] == {"nodes": [], "roles": []}), None)
        version = current["version"] + 1 if current else 1
        bundle = create_bundle("gate-rules", mesh=self.mesh, version=version, body=body,
                               supersedes=current["bundle_id"] if current else "")
        try:
            operator = unseal_identity(self.profile.operator_keystore, passphrase.encode("utf-8"))
        except KeystoreError:
            return False, pick(lang, "口令不对，再输一次。", "That passphrase is wrong. Type it again.")
        reply = self.policy.offer(sign_bundle(operator, bundle), timeout=timeout)
        if reply is None:
            return False, pick(lang, "节点没有回应，过一会儿再签一次。", "The node did not answer. Sign again in a moment.")
        if reply.get("ok"):
            return True, pick(lang, f"第 {version} 版规则已经生效。", f"Version {version} of the rules is in force.")
        return False, pick(lang, "节点没有接受：", "The node did not accept them: ") + pick(lang, "；", "; ").join(
            explain_errors(reply.get("errors", []), lang))

    @property
    def mesh(self) -> str:
        return self.profile.settings().get("mesh", "home")


def parse_rules(text: str, lang: str = "zh") -> dict:
    """YAML (or JSON) to a gate-rules body; raises ValueError in plain words."""
    try:
        body = yaml.safe_load(text) if text.strip() else {}
    except yaml.YAMLError as e:
        raise ValueError(pick(lang, f"看不懂这段 YAML：{e}", f"This YAML does not read: {e}")) from None
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise ValueError(pick(lang, "规则应该是一组「名字: 值」", "Rules are a set of \u201cname: value\u201d pairs"))
    return body


def check_rules(body: dict, mesh: str, lang: str = "zh") -> list[str]:
    """The schema's verdict on a draft body, in plain words (envelope fields
    are filled in at signing time and not judged here)."""
    errors = [e for e in check_bundle(assemble("gate-rules", mesh=mesh, version=1, body=body))
              if e.partition(":")[2].startswith("body")]
    return explain_errors(errors, lang)


def try_rules(body: dict, sample: str, lang: str = "zh") -> str:
    """What Gate 1 would say to ``sample`` under the draft rules. (What the
    gate itself says is the node's words, in Chinese today.)"""
    rules = GateRules.from_bodies([("draft", body)])
    involvement = DecisionModel(rules).intake(sample, rules.review(sample))
    if involvement.disposition == HOLD:
        return pick(lang, f"我不会做：「{involvement.says}」", f"I won't do it: \u201c{involvement.says}\u201d")
    if involvement.needs_person:
        return pick(lang, f"我会先问你：「{involvement.says}」", f"I'd ask you first: \u201c{involvement.says}\u201d")
    return pick(lang, "通过：我会直接开始做。", "It passes: I would start right away.")


__all__ = ["Companion", "FORGET", "KEEP", "RULES_TEMPLATE", "check_rules", "explain_errors", "line_from", "moments_from",
           "parse_rules", "try_rules", "whisper_from"]
