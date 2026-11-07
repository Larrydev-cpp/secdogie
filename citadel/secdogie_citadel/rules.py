"""The gate rules a node has been given, as the two gates use them.

Operators inject rules into the mesh as signed ``gate-rules`` policy bundles
(``secdogie_policy``). The node verifies each bundle and hands the bodies here.
``GateRules`` is the result: plain data, merged so that the strictest rule wins.

Three properties make it safe to apply automatically:

  * **Only ever stricter.** Every value is clamped against the built-in
    default: a limit can go down, a "switch it on" can only switch on, and
    a list can only grow. A body that somehow carried a looser value (the
    policy validator refuses those) still cannot loosen anything here.
  * **Merging is strictest-wins.** Several bundles can apply at once (the whole
    mesh, this node's role, this node). Merging takes the minimum of the limits
    and the union of the lists and switches, so adding a bundle never loosens
    the gates.
  * **Gate 2 is not in here.** Nothing in a rule touches operator signatures,
    HITL for high-risk steps or the capability allowlist. A rule can mark more
    kinds as destructive, which sends more steps to Gate 2, never fewer.

Phrases are matched as in ``secdogie_policy.text``: NFKC, then ASCII-only lower
case, then single spaces. That package's tests check that the two copies of
``normalize_phrase`` agree.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field, replace

from . import socratic
from .action_gate import GateContext, PlannedAction

_ASCII_UPPER = {c: c + 32 for c in range(ord("A"), ord("Z") + 1)}
_SPACES = re.compile(" +")
_STEP_MARKERS = re.compile(r"\band then\b|然后|接着|再然后", re.IGNORECASE)

_DEFAULT_CTX = GateContext()
DEFAULT_KNOWN_FAILURE_MIN_RUNS = 3  # consolidate.DEFAULT_MIN_RUNS


def normalize_phrase(s: str) -> str:
    out = unicodedata.normalize("NFKC", s).translate(_ASCII_UPPER)
    return _SPACES.sub(" ", out).strip(" ")


@dataclass(frozen=True)
class RevisePhrase:
    phrases: tuple[str, ...]
    reason: str
    suggestion: str = ""


@dataclass(frozen=True)
class BlockedTarget:
    role: str = ""
    name: str = ""

    def matches(self, action: PlannedAction) -> bool:
        if self.role and normalize_phrase(action.target_role) != self.role:
            return False
        if self.name:
            haystack = normalize_phrase(" ".join((action.target_name, action.target_id, action.text)))
            if self.name not in haystack:
                return False
        return bool(self.role or self.name)


def _int(v, default: int) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else default


def _strs(v) -> tuple[str, ...]:
    return tuple(x for x in v if isinstance(x, str)) if isinstance(v, list) else ()


@dataclass(frozen=True)
class GateRules:
    """What the node's gates enforce beyond their built-in behaviour. The
    defaults are the built-ins, so ``GateRules()`` changes nothing."""

    # Gate 1, instruction layer (socratic.py)
    max_len: int = socratic._MAX_LEN
    max_steps: int = socratic._MAX_STEPS
    revise_on: tuple[RevisePhrase, ...] = ()
    # Gate 1, plan layer (action_gate.py, loop_gate.py)
    require_intent: bool = False
    enforce_capabilities: bool = False
    repeat_threshold: int = _DEFAULT_CTX.repeat_threshold
    poll_window: int = _DEFAULT_CTX.poll_window
    known_failure_min_runs: int = DEFAULT_KNOWN_FAILURE_MIN_RUNS
    max_cost: int = 0  # 0: no budget beyond the caller's
    extra_destructive_kinds: frozenset[str] = frozenset()
    blocked_kinds: frozenset[str] = frozenset()
    blocked_targets: tuple[BlockedTarget, ...] = ()
    # Presence: when the companion involves the person
    ask_before_new_goal: bool = False
    note_every_mutation: bool = False
    sources: tuple[str, ...] = field(default=(), compare=False)  # bundle ids, for the inner view

    # -- building ---------------------------------------------------------------

    @classmethod
    def from_bodies(cls, bodies: Iterable[tuple[str, dict]]) -> GateRules:
        """Merge ``(bundle_id, body)`` pairs of verified gate-rules bundles."""
        rules = cls()
        for bundle_id, body in bodies:
            rules = rules.merged(cls._from_body(body if isinstance(body, dict) else {}), source=bundle_id)
        return rules

    @classmethod
    def _from_body(cls, body: dict) -> GateRules:
        base = cls()
        ins = body.get("instruction") if isinstance(body.get("instruction"), dict) else {}
        plan = body.get("plan") if isinstance(body.get("plan"), dict) else {}
        presence = body.get("presence") if isinstance(body.get("presence"), dict) else {}
        revise = []
        for entry in ins.get("revise_on", []) if isinstance(ins.get("revise_on"), list) else []:
            if not isinstance(entry, dict):
                continue
            phrases = tuple(p for p in (normalize_phrase(x) for x in _strs(entry.get("phrases"))) if p)
            reason = entry.get("reason")
            if phrases and isinstance(reason, str) and reason:
                suggestion = entry.get("suggestion")
                revise.append(RevisePhrase(phrases, reason, suggestion if isinstance(suggestion, str) else ""))
        targets = []
        for entry in plan.get("blocked_targets", []) if isinstance(plan.get("blocked_targets"), list) else []:
            if isinstance(entry, dict):
                t = BlockedTarget(normalize_phrase(entry.get("role", "") if isinstance(entry.get("role"), str) else ""),
                                  normalize_phrase(entry.get("name", "") if isinstance(entry.get("name"), str) else ""))
                if t.role or t.name:
                    targets.append(t)
        return cls(
            max_len=_int(ins.get("max_len"), base.max_len),
            max_steps=_int(ins.get("max_steps"), base.max_steps),
            revise_on=tuple(revise),
            require_intent=plan.get("require_intent") is True,
            enforce_capabilities=plan.get("enforce_capabilities") is True,
            repeat_threshold=_int(plan.get("repeat_threshold"), base.repeat_threshold),
            poll_window=_int(plan.get("poll_window"), base.poll_window),
            known_failure_min_runs=_int(plan.get("known_failure_min_runs"), base.known_failure_min_runs),
            max_cost=_int(plan.get("max_cost"), 0),
            extra_destructive_kinds=frozenset(_strs(plan.get("extra_destructive_kinds"))),
            blocked_kinds=frozenset(_strs(plan.get("blocked_kinds"))),
            blocked_targets=tuple(targets),
            ask_before_new_goal=presence.get("ask_before_new_goal") is True,
            note_every_mutation=presence.get("note_every_mutation") is True,
        )

    def merged(self, other: GateRules, *, source: str = "") -> GateRules:
        """Strictest of the two, clamped to the built-ins: lower limits, more
        switches on, longer lists. Never looser than either side."""
        base = GateRules()

        def lower(a: int, b: int, floor_value: int, lo: int) -> int:
            return max(lo, min(a, b, floor_value))

        budgets = [c for c in (self.max_cost, other.max_cost) if c > 0]
        return GateRules(
            max_len=lower(self.max_len, other.max_len, base.max_len, 1),
            max_steps=lower(self.max_steps, other.max_steps, base.max_steps, 1),
            revise_on=self.revise_on + tuple(r for r in other.revise_on if r not in self.revise_on),
            require_intent=self.require_intent or other.require_intent,
            enforce_capabilities=self.enforce_capabilities or other.enforce_capabilities,
            repeat_threshold=lower(self.repeat_threshold, other.repeat_threshold, base.repeat_threshold, 1),
            poll_window=lower(self.poll_window, other.poll_window, base.poll_window, 2),
            known_failure_min_runs=lower(self.known_failure_min_runs, other.known_failure_min_runs,
                                         base.known_failure_min_runs, 1),
            max_cost=min(budgets) if budgets else 0,
            extra_destructive_kinds=self.extra_destructive_kinds | other.extra_destructive_kinds,
            blocked_kinds=self.blocked_kinds | other.blocked_kinds,
            blocked_targets=self.blocked_targets + tuple(t for t in other.blocked_targets
                                                         if t not in self.blocked_targets),
            ask_before_new_goal=self.ask_before_new_goal or other.ask_before_new_goal,
            note_every_mutation=self.note_every_mutation or other.note_every_mutation,
            sources=self.sources + ((source,) if source else ()),
        )

    # -- Gate 1, instruction layer -------------------------------------------------

    def instruction_checks(self) -> list:
        checks = []
        if self.max_len < socratic._MAX_LEN or self.max_steps < socratic._MAX_STEPS:
            def overlong(text: str):
                steps = len(_STEP_MARKERS.findall(text))
                if len(text) > self.max_len or steps > self.max_steps:
                    return (f"overlong for this node's rules: at most {self.max_len} characters "
                            f"and {self.max_steps} chained steps",
                            "Split it into a few smaller goals.")
                return None
            checks.append(overlong)
        for rule in self.revise_on:
            def phrase_check(text: str, rule=rule):
                normalized = normalize_phrase(text)
                if any(p in normalized for p in rule.phrases):
                    return (rule.reason, rule.suggestion)
                return None
            checks.append(phrase_check)
        return checks

    def review(self, instruction: str) -> socratic.Review:
        """Gate 1's instruction review under these rules."""
        return socratic.review(instruction, extra_checks=self.instruction_checks())

    # -- Gate 1, plan layer ----------------------------------------------------------

    def mark(self, action: PlannedAction) -> PlannedAction:
        """The action as the gates must see it: a kind the rules call
        destructive is high-risk, so it needs Gate 2."""
        if action.kind in self.extra_destructive_kinds and not action.high_risk:
            return replace(action, high_risk=True)
        return action

    def blocked_reason(self, action: PlannedAction) -> str | None:
        if action.kind in self.blocked_kinds:
            return f"the rules do not allow {action.kind!r} on this node"
        for t in self.blocked_targets:
            if t.matches(action):
                what = " / ".join(x for x in (t.role, t.name) if x)
                return f"the rules keep this node away from {what}"
        return None

    def context(self, ctx: GateContext) -> GateContext:
        """``ctx`` with these rules applied: never looser than ``ctx`` itself."""
        budget = ctx.cost_budget
        if self.max_cost > 0:
            budget = min(budget, float(self.max_cost)) if budget > 0 else float(self.max_cost)
        return replace(
            ctx,
            enforce_capabilities=ctx.enforce_capabilities or self.enforce_capabilities,
            require_intent=ctx.require_intent or self.require_intent,
            repeat_threshold=min(ctx.repeat_threshold, self.repeat_threshold),
            poll_window=max(2, min(ctx.poll_window, self.poll_window)),
            cost_budget=budget,
        )


__all__ = ["BlockedTarget", "GateRules", "RevisePhrase", "normalize_phrase"]
