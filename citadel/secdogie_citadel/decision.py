"""The companion's decision model: when to involve the person, and how.

The two gates judge (Gate 1: is this what we mean, and does it hold up; Gate 2:
only a signature lets an irreversible step through). This model decides what
the judgment means for the person on the other side. It is a partner, so it
does not report every step and it does not ask permission for everything. It
speaks when the work needs the person:

  proceed   carry on quietly
  note      leave one line that fades: "I've started on ...", "I skipped a step"
  ask       one Socratic question (Gate 1): something is unclear or does not hold
  sign      a step that cannot be undone needs the person's signature (Gate 2)
  hold      the step is not taken; one line says why

``urgency`` says when the person should see it. ``gap`` means wait for a pause
in what they are doing. ``soon`` means the work is waiting on them, so surface
at the next pause but do not wait forever. Neither ever takes the keyboard
focus; the window decides how (Studio's presence layer).

The model has a deterministic floor (below) and optional *proposers*: a
proposer, an LLM critic for example, may raise the involvement (proceed ->
note -> ask), never lower it. It cannot turn a hold into anything else or
invent a signature; a proposer that fails changes nothing. So whatever is
plugged in, the person is involved at least as much as the floor says.

Wording is Chinese, in the companion's own voice. Identifiers stay English.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .action_gate import GateDecision, PlannedAction
from .rules import GateRules
from .socratic import Review

log = logging.getLogger("secdogie_citadel.decision")

PROCEED, NOTE, ASK, SIGN, HOLD = "proceed", "note", "ask", "sign", "hold"
GAP, SOON = "gap", "soon"
_RANK = {PROCEED: 0, NOTE: 1, ASK: 2, SIGN: 3, HOLD: 4}

# The options a goal-intake question offers.
REPHRASE, AS_SAID, START, DROP = "我换个说法", "就按原话做", "开始吧", "先不做了"

# Gate 1 reasons the person may overrule by saying "just as I said": they are
# about how well-formed a request is, not about safety.
_OVERRIDABLE = ("overlong", "inefficient polling")

_REASON_WORDS = (
    ("empty instruction", "你还没说想做什么。"),
    ("self-contradiction", "这句话一边说只读，一边又要改东西。"),
    ("unattended posting", "这是要替你对外发送内容，而且不经你确认。这种事我需要你在场。"),
    ("inefficient polling", "这样一直轮询很费力。有变化时我再告诉你，好吗？"),
    ("overlong", "一次要做的事情有点多，拆成几步会更稳。"),
)


def say_reason(reason: str) -> str:
    """One Gate 1 reason in the companion's words. An operator's own rule
    reason is already in their words and passes through."""
    for prefix, words in _REASON_WORDS:
        if reason.startswith(prefix):
            return words
    return reason


@dataclass(frozen=True)
class Involvement:
    disposition: str
    urgency: str = GAP
    says: str = ""
    options: tuple[str, ...] = ()
    why: tuple[str, ...] = ()

    @property
    def needs_person(self) -> bool:
        return self.disposition in (ASK, SIGN)


Proposer = Callable[[str, dict], "Involvement | None"]


def _target(planned: PlannedAction) -> str:
    return planned.target_name or planned.target_id or planned.text or ""


class DecisionModel:
    def __init__(self, rules: GateRules | None = None, proposers: Iterable[Proposer] = ()):
        self.rules = rules or GateRules()
        self.proposers = tuple(proposers)

    # -- moments ------------------------------------------------------------------

    def intake(self, title: str, review: Review) -> Involvement:
        """A new goal arrives (Gate 1, instruction layer)."""
        if not review.accepted:
            first = review.reasons[0] if review.reasons else ""
            overridable = all(r.startswith(_OVERRIDABLE) for r in review.reasons)
            says = "开始之前，我想先弄清楚一点：" + say_reason(first)
            options = (REPHRASE, AS_SAID, DROP) if overridable else (REPHRASE, DROP)
            floor = Involvement(ASK, GAP, says, options, tuple(review.reasons))
        elif self.rules.ask_before_new_goal:
            floor = Involvement(ASK, GAP, f"我理解的是：「{title}」。就这样开始吗？", (START, REPHRASE, DROP),
                                ("ask before every new goal",))
        else:
            floor = Involvement(NOTE, GAP, f"好，我开始做「{title}」。")
        return self._final("intake", {"title": title, "review": review}, floor)

    def step(self, planned: PlannedAction, decision: GateDecision | None, *, blocked: bool) -> Involvement:
        """A step the loop is about to take (Gate 1, plan layer; Gate 2)."""
        if blocked:
            reason = decision.reason if decision is not None else ""
            floor = Involvement(HOLD, GAP, "我停下了一步：" + (reason or planned.kind),
                                why=tuple(decision.findings) if decision is not None else ())
        elif planned.destructive:
            target = _target(planned)
            floor = Involvement(SIGN, SOON, f"这一步做了就改不回来：{planned.kind} {target}".rstrip()
                                + "。需要你签名同意。", why=("destructive",))
        elif planned.mutating and self.rules.note_every_mutation:
            floor = Involvement(NOTE, GAP, f"我{planned.kind}了 {_target(planned)}".rstrip() + "。")
        else:
            floor = Involvement(PROCEED)
        return self._final("step", {"action": planned, "decision": decision, "blocked": blocked}, floor)

    def finish(self, title: str, code: int, summary: str = "") -> Involvement:
        if code == 0:
            says = f"做完了：「{title}」。"
        elif code == 5:
            says = f"停下了：「{title}」，你说继续我就接着做。"
        else:
            says = f"没做成：「{title}」。" + (f"（{summary}）" if summary else "")
        return self._final("finish", {"title": title, "code": code, "summary": summary},
                           Involvement(NOTE, GAP, says))

    # -- the floor and the proposers ---------------------------------------------------

    def _final(self, moment: str, facts: dict, floor: Involvement) -> Involvement:
        if floor.disposition in (HOLD, SIGN) or not self.proposers:
            return floor  # a hold is final; a signature is Gate 2's, not a proposer's
        best = floor
        for propose in self.proposers:
            try:
                p = propose(moment, facts)
            except Exception:  # noqa: BLE001 - a failing proposer changes nothing
                log.exception("decision proposer failed at %s", moment)
                continue
            if not isinstance(p, Involvement) or p.disposition not in (NOTE, ASK):
                continue  # a proposer may ask or note, never sign, hold or lower
            if _RANK[p.disposition] > _RANK[best.disposition]:
                best = Involvement(p.disposition, p.urgency if p.urgency in (GAP, SOON) else GAP,
                                   p.says or best.says, p.options or best.options, best.why + p.why)
        return best


__all__ = ["ASK", "AS_SAID", "DROP", "DecisionModel", "GAP", "HOLD", "Involvement", "NOTE", "PROCEED",
           "Proposer", "REPHRASE", "SIGN", "SOON", "START", "say_reason"]
