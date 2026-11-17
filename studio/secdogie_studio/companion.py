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
  "begin: ..."                      -> a task started (a calm whisper; counts as work in progress)
  "say: ..."                        -> an answer in words to something that was not a task
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
from secdogie_citadel.decision import DecisionModel
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


def reading_words(r: dict, lang: str) -> str:
    """What the cortex made of a request (a node's ``read:`` line), in labels: how it read it, and what decided."""
    from secdogie_citadel.cortex import questions as Q

    kind = (r.get("answers") or {}).get("kind", [""])[0]
    read = Q.BY_ID["kind"].meaning(kind, lang) if kind else ""
    words = pick(lang, *BECAUSE_WORDS.get(r.get("because", ""), ("", "")))
    strange = r.get("strange") or []
    unknown = pick(lang, "没见过：", "never met: ") + pick(lang, "、", ", ").join(strange) if strange else ""
    head = pick(lang, f"我读到的：{read}。", f"How I read it: {read}.") if read else ""
    return " ".join(x for x in (head, words, unknown) if x)


def line_from(entry, titles: dict[str, str], lang: str = "zh") -> dict | None:
    """One transcript entry as a conversation line, in the companion's words;
    None for what the conversation does not show (replies to requests)."""
    if entry.who == "you":
        return line("you", entry.text)
    if entry.text.startswith("read: "):  # the cortex's reading: shown when nothing else says what it decided
        import json

        try:
            r = json.loads(entry.text[len("read: "):])
        except ValueError:
            return None
        if r.get("verdict") in ("proceed", "clarify") or r.get("source"):
            return None  # the task's own line, the question, or the reply says it
        tone = "warm" if r.get("verdict") in ("refuse", "dissuade", "support") else "calm"
        return line("it", reading_words(r, lang), tone)
    if entry.who == "app":
        return line("note", entry.text)
    if entry.text.startswith("letter: "):
        return line("it", entry.text[len("letter: "):], "letter")
    if entry.text.startswith("say: "):  # an answer in words: not a task, nothing was started
        said = entry.text[len("say: "):]
        return line("it", said, "letter" if "\n\n" in said else "calm")
    if entry.text == "answer adopted":
        return None
    if entry.text.startswith("no answer in time"):
        return line("note", pick(lang, "没等到你的回答，这件事先放着。", "No answer came, so this waits."))
    if entry.kind is DialogueType.SOCRATIC_QUESTION:
        return line("it", entry.text, "ask")
    for prefix, tone in (("note: ", "calm"), ("begin: ", "calm"), ("hold: ", "warm")):
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
    if entry.text.startswith(("accepted: ", "refused: ", "answered: ")):
        return None
    return line("note", entry.text)


def whisper_from(entry, index: int, now: float, lang: str = "zh") -> Whisper | None:
    if entry.kind is not DialogueType.SYSTEM_STATUS:
        return None
    if entry.text.startswith("read: "):
        said = line_from(entry, {}, lang)
        return Whisper(f"w{index}", said["text"], said["tone"], now) if said else None
    for prefix, tone in (("note: ", "calm"), ("begin: ", "calm"), ("hold: ", "warm")):
        if entry.text.startswith(prefix):
            return Whisper(f"w{index}", entry.text[len(prefix):], tone, now)
    if entry.text.startswith("letter: "):  # the first paragraph in passing; the rest is read in the home window
        return Whisper(f"w{index}", entry.text[len("letter: "):].split("\n\n", 1)[0], "warm", now)
    if entry.text.startswith("say: "):
        return Whisper(f"w{index}", entry.text[len("say: "):].split("\n\n", 1)[0], "calm", now)
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
            w = whisper_from(entries[i], i, self._clock(), self.lang)
            if w is not None:
                out.append(w)
        self._seen = len(entries)
        return out

    def busy(self) -> bool:
        started = finished = 0
        for e in self.ctl.transcript():
            if e.kind is DialogueType.SYSTEM_STATUS:
                started += e.text.startswith("begin: ")  # the node's own mark that a task started
                finished += e.text.startswith("goal ") and " finished: " in e.text
        return started > finished

    def log_lines(self, n: int = 40) -> list[str]:
        def plain(text: str) -> str:
            for prefix in ("note: ", "begin: ", "say: ", "hold: "):
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


VERDICT_WORDS = {"proceed": ("照做", "Goes ahead"), "clarify": ("先问一句", "Asks one thing"), "talk": ("回话", "Answers in words"),
                 "dissuade": ("劝一劝", "Talks you out of it"), "refuse": ("不做", "Won't do it"),
                 "support": ("先陪你", "You first")}


BECAUSE_WORDS = {  # what decided it, as the Gate page names it
    "danger": ("你的安全在一切之前。", "Your safety comes before everything."),
    "talk": ("这是在说话，不是要它做事：它只回话，什么都不动。", "This is talk, not a task: it answers, and touches nothing."),
    "unclear": ("它没读懂，所以什么都不做。", "It could not read this, so it does nothing."),
    "principle": ("落到没同意的人身上的事，它不做。", "What would fall on someone who has not agreed, it does not do."),
    "consent": ("要看别人同不同意，这只有你能说清。", "It depends on whether someone else agreed, which only you can say."),
    "unsure": ("它对自己的读法没把握，先问你。", "It is not sure of its own reading, so it asks you first."),
    "thinking": ("它把每条路推演了一遍，选了最好的那条。", "It played every way forward out and chose the best."),
    "everyday": ("寻常的事，它有把握，就去做。", "An everyday thing it is sure of: it goes ahead."),
    "rule": ("你定的规则要它先问一句。", "Your own rule asks it to check with you first."),
    "empty": ("你还没说想做什么。", "You have not said what you want yet."),
}


def _at(hour: str, now: float | None = None) -> float:
    """Today in the afternoon, or in the small hours: the hour changes the judgment."""
    lt = time.localtime(time.time() if now is None else now)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 2 if hour == "night" else 14, 10, 0, 0, 0, -1))


_CORTEX: dict = {}


def cortex_at(folder=None):
    """The living cortex kept in the profile (reloaded when what it learned changes), or the generation SecDogie
    came with."""
    from pathlib import Path

    from secdogie_citadel.cortex.core import Cortex
    from secdogie_citadel.cortex.life import BORN, shipped

    where = Path(folder) if folder and (Path(folder) / "cortex.json").exists() else BORN
    stamp = tuple((where / n).stat().st_mtime if (where / n).exists() else 0 for n in ("cortex.json", "memory.jsonl", "keys.npy"))
    got = _CORTEX.get(str(where))
    if got is None or got[0] != stamp:
        try:
            got = (stamp, Cortex.load(where))
        except (OSError, ValueError):
            got = (stamp, shipped())
        _CORTEX[str(where)] = got
    return got[1]


def _chain(ids: list, lang: str) -> str:
    from secdogie_citadel.mind.world import RULES

    names = {r.id: pick(lang, r.zh, r.en) for r in RULES}
    return " → ".join(n for n in (names.get(i, "") for i in ids) if n)


def _world_label(world: dict, act: str, lang: str) -> str:
    from secdogie_citadel.mind.belief import UNKNOWNS
    from secdogie_citadel.mind.world import ACTS

    parts = []
    for k, v in world.items():
        if k == "goal":
            g = next((g for g in ACTS[act].goals if g.id == v), None) if act in ACTS else None
            if g is not None:
                parts.append(pick(lang, f"你想{g.zh}", f"you want to {g.en}"))
        elif k in UNKNOWNS:
            u = UNKNOWNS[k]
            parts.append(pick(lang, u.feared_zh, u.feared_en) if v == u.feared else pick(lang, u.fine_zh, u.fine_en))
    return pick(lang, "，", ", ").join(parts)


def thinking_view(th: dict | None, lang: str) -> dict:
    """The planner's trace (``Thought.journal``) in the Studio's words: the unknowns and the cues behind them, what
    the person is likely after, and every way forward played out."""
    if not th:
        return {"unknowns": [], "goals": [], "goalsRead": False, "plans": [], "turns": [], "upset": 0.0, "autonomy": 0.0}
    from secdogie_citadel.mind.belief import UNKNOWNS
    from secdogie_citadel.mind.plan import STEPS
    from secdogie_citadel.mind.world import ACTS, FACTS

    act = th.get("act", "")
    unknowns = []
    for uid, p, cues in th.get("unknowns", []):
        u = UNKNOWNS[uid]
        unknowns.append({"question": pick(lang, u.zh, u.en), "p": p, "feared": pick(lang, u.feared_zh, u.feared_en),
                         "fine": pick(lang, u.fine_zh, u.fine_en),
                         "evidence": [{"ratio": r, "text": pick(lang, *FACTS.get(f, (f, f))), "words": ""} for f, r, _ in cues]})
    from secdogie_citadel.cortex import questions as Q

    goals = []
    for gid, p, because in th.get("motives", []):
        g = next((g for g in ACTS[act].goals if g.id == gid), None) if act in ACTS else None
        if g is not None:
            said = [Q.BY_ID["why"].meaning(w, lang) for w in because if w not in ("unsaid", "other")]
            goals.append({"name": pick(lang, g.zh, g.en), "p": p, "because": pick(lang, "；", "; ").join(said)})
    plans = []
    for sid, eu, _parts in th.get("options", []):
        st = STEPS[sid]
        worlds = [{"label": _world_label(w, act, lang), "p": wp, "u": wu, "chain": _chain(chain, lang),
                   "then": pick(lang, STEPS[then].zh, STEPS[then].en) if then else ""}
                  for w, wp, wu, chain, then in th.get("worlds", {}).get(sid, [])]
        plans.append({"name": pick(lang, st.zh, st.en), "eu": eu, "chosen": sid == th.get("choice"), "asked": sid == "do",
                      "undo": st.does, "worlds": sorted(worlds, key=lambda w: -w["p"])[:4]})
    turns = [{"text": pick(lang, f"{round(need * 100)}% 的把握：{UNKNOWNS[uid].fine_zh}", f"{round(need * 100)}% sure that {UNKNOWNS[uid].fine_en}")}
             for uid, _now, need in th.get("turns", []) if need <= 1.0]
    return {"unknowns": unknowns, "goals": goals, "goalsRead": any(g["because"] for g in goals), "plans": plans,
            "turns": turns, "upset": float(th.get("upset", 0.0)), "autonomy": float(th.get("autonomy", 0.0))}


def think_through(body: dict | None, sample: str, hour: str = "day", lang: str = "zh", now: float | None = None,
                  cortex=None) -> dict:
    """How the Socratic gate thinks about ``sample`` under the draft rules: the same path the node takes (the
    rules' review, the cortex, the decision), shown as the Gate page shows it. Without the person's history."""
    from secdogie_citadel.cortex import questions as Q
    from secdogie_citadel.deliberate import deliberate, language

    text = (sample or "").strip()
    if not text:
        return {}
    c = cortex if cortex is not None else cortex_at(None)
    rules = GateRules.from_bodies([("draft", body)]) if body else GateRules()
    review = rules.review(text)
    at = _at(hour, now)
    d = deliberate(text, review, cortex=c, now=at)
    involvement = DecisionModel(rules).intake(text, review, d)
    u = d.understanding or {}
    unsure = set((d.decision or {}).get("unsure", []))
    shown = []
    for q in Q.QUESTIONS:
        if q.id not in d.answers:
            continue
        a, sure = d.answers[q.id][0], float(d.answers[q.id][1])
        shown.append({"id": q.id, "q": pick(lang, q.zh, q.en), "a": q.meaning(a, lang), "answer": a, "sure": sure,
                      "safety": q.id in ("self", "others", "seat"), "unsure": q.id in unsure})
    question = None
    if d.question is not None:
        from secdogie_citadel.deliberate import question_for

        qq = question_for(d.question.qid, lang)
        question = {"text": qq.text, "options": list(qq.labels)}
    record = c.record() if hasattr(c, "record") else {}
    return {
        "text": text, "hour": hour, "lang": language(text), "verdict": d.verdict,
        "verdictWords": pick(lang, *VERDICT_WORDS.get(d.verdict, ("", ""))), "category": d.category, "because": d.category,
        "becauseWords": pick(lang, *BECAUSE_WORDS.get(d.category, ("", ""))),
        "says": d.says or "", "source": d.source, "rule": d.category == "rule", "asks": involvement.needs_person,
        "answers": shown, "strange": list(u.get("strange", [])), "novelty": float(u.get("novelty", 0.0)),
        "recalled": float(u.get("recalled", 0.0)), "question": question, "weighty": bool(d.thought),
        "acts": list((d.decision or {}).get("acts", [])), "until": date_words(d.until, at, lang) if d.until else "",
        "generation": {"number": record.get("number", 0), "digest": (record.get("digest") or "")[:12],
                       "neurons": record.get("neurons", 0), **{k: v for k, v in (record.get("scores") or {}).items()}},
        **thinking_view(d.thought, lang),
    }


def date_words(t: float, now: float, lang: str) -> str:
    days = max(1, round((t - now) / 86400))
    when = time.strftime("%Y-%m-%d", time.localtime(t))
    return pick(lang, f"{when}（{days} 天后）", f"{when} (in {days} day{'s' if days != 1 else ''})")


def mind_view(folder=None, lang: str = "zh") -> dict:
    """The page about the cortex itself: which generation, how it did on the exam, how it grew, how independent it is
    of the external model, and what it learned lately."""
    import json
    from pathlib import Path

    c = cortex_at(folder)
    rec = c.record()
    curve = []
    root = Path(folder) if folder else None
    if root is not None and (root / "lineage" / "curve.json").exists():
        try:
            curve = json.loads((root / "lineage" / "curve.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            curve = []
    if not curve:  # no lineage on this device yet: how the generation it came with grew
        from secdogie_citadel.cortex.life import BORN

        try:
            curve = json.loads((BORN / "growth.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            curve = []
    if not curve:
        curve = [{"generation": rec["number"], **rec.get("scores", {}), "op": (rec.get("lineage") or [{}])[-1].get("op", "")}]
    for point in curve:
        if "round" in point and "note" not in point:
            n, agreed = point.get("lessons", 0), point.get("agreed")
            point["note"] = (pick(lang, f"出生：学了 {n} 课", f"born from {n} lessons") if point["round"] == 0 else
                             pick(lang, f"看 AI 模型做了一批新事，原本就读对 {round((agreed or 0) * 100)}%；学完、复盘后是第 {point.get('generation', 0)} 代",
                                  f"watched your AI model on new requests, already read {round((agreed or 0) * 100)}% the same; "
                                  f"learned them, reviewed itself: generation {point.get('generation', 0)}"))
    led = c.ledger.view()
    kept = [{"text": x.text, "source": x.source} for x in c.memory.lessons if x.source in ("teacher", "person")][-8:]
    return {"number": rec["number"], "digest": rec["digest"][:12], "neurons": rec["neurons"], "synapses": rec["synapses"],
            "kept": rec["kept"], "elders": len(c.elders), "scores": rec.get("scores", {}), "curve": curve,
            "lineage": [{"op": x.get("op", ""), "gain": x.get("gain", 0.0)} for x in rec.get("lineage", [])],
            "ledger": led, "learned": kept, "lang": lang}


def try_rules(body: dict, sample: str, lang: str = "zh") -> str:
    """What your draft rules alone would do with ``sample`` (Gate 1's review). What the request means is the
    cortex's to think through after that; the Gate page's trial shows it."""
    from secdogie_citadel.decision import say_reason

    rules = GateRules.from_bodies([("draft", body)])
    review = rules.review(sample)
    if not review.accepted:
        reason = pick(lang, "；", "; ").join(say_reason(r).rstrip("。.") for r in review.reasons)
        return pick(lang, f"我会先问你：「你定过一条规则：{reason}。」", f"I'd ask you first: \u201cyour rule: {reason}.\u201d")
    return pick(lang, "你的规则放行；接下来由它的头脑去想。", "Your rules let it through; its mind thinks it through from here.")


__all__ = ["Companion", "FORGET", "KEEP", "RULES_TEMPLATE", "VERDICT_WORDS", "check_rules", "cortex_at", "explain_errors",
           "line_from", "mind_view", "moments_from", "parse_rules", "think_through", "thinking_view", "try_rules", "whisper_from"]
