"""Action-plan Socratic gate (Phase 2.6): question a *proposed action*, not just
the natural-language instruction.

``socratic.py`` reviews a request's wording. This gate sits one layer down: given
a concrete ``PlannedAction`` (click this target, type this text, run this) plus
the current observation / recent history / granted capabilities, it asks the
questions an autonomous loop most needs answered *before* it acts:

  * Is the target still there, at the generation I decided against?  (uses the
    2.4 observation generation and the 2.5 target-identity keys, passed in as
    plain fields so this layer stays decoupled from the agent.)
  * Am I repeating myself, busy-polling, or doing a literal no-op?
  * Is this a destructive step chained onto another with no verification between?
  * Did I declare what I expect to observe afterwards?
  * Is it within the cost budget and within a granted capability?  (Phase 2.9:
    scopes come from signed grants -- ``secdogie_identity.capability`` -- and are
    enforced when the caller turns enforcement on or passes a non-empty set.)
  * Does the instruction behind it ask to post/send unattended?  (reuses the
    instruction gate, so the two Socratic layers agree.)
  * Gate 1, intent: why this step (which active goal), how to back out if it
    fails (or an explicit "irreversible"), and do its stated preconditions still
    hold?  And has this exact action already failed repeatedly (consolidated
    memory)?  Memory is passed in as plain data and can only ADD caution: with
    or without it the verdict is never less strict, and Gate 2 never reads it.

It returns a ``GateDecision`` -- ``allow`` / ``reject`` / ``rewrite`` /
``request_reobserve`` -- and NOTHING else. The gate JUDGES; it does not execute,
does not touch OS security, does not bypass a permission prompt, and never turns
HITL into auto-approval. A rewrite only ever makes an action *more* cautious
(e.g. attaches a required verification). Execution still passes the agent's
Safety layer and human-in-the-loop confirmation.

Pure, deterministic, and dependency-light (``socratic`` from this package, and
the capability matcher from ``secdogie_identity``).
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

from secdogie_identity.capability import allows as capability_allows

from . import socratic
from .authz import action_hash, verify_authorization

# --- Verdicts ---------------------------------------------------------------

ALLOW = "allow"
REJECT = "reject"
REWRITE = "rewrite"
REQUEST_REOBSERVE = "request_reobserve"

# --- Finding kinds ----------------------------------------------------------

STALE_TARGET = "stale-target"
TARGET_MISMATCH = "target-mismatch"
NO_OP = "no-op"
REPEATED = "repeated"
POLLING = "polling"
DESTRUCTIVE_CHAIN = "destructive-chain"
MISSING_VERIFICATION = "missing-verification"
EXCESSIVE_COST = "excessive-cost"
OUT_OF_CAPABILITY = "out-of-capability"
UNATTENDED_POSTING = "unattended-posting"
UNAUTHORIZED_ACTION = "unauthorized-action"
INTENT_UNPROVEN = "intent-unproven"
INTENT_CONTRADICTION = "intent-contradiction"
PRECONDITION_FAILED = "precondition-failed"
KNOWN_FAILURE = "known-failure"

# Kinds that only observe -- they never mutate, so they need no verification and
# a run of them with nothing else between is the busy-poll signal.
_READ_KINDS = frozenset({"read", "observe", "get", "inspect", "screenshot", "wait", "poll"})
# Kinds that publish/send content to the outside world (external state change).
_POSTING_KINDS = frozenset({"post", "publish", "submit", "send", "reply", "comment", "tweet", "dm"})
# Kinds that are inherently destructive on their own.
_DESTRUCTIVE_KINDS = frozenset({"delete", "overwrite", "format", "drop", "uninstall", "run_elevated"})

# What capability each action kind requires (Phase 2.9). Enforced when the
# caller sets ``enforce_capabilities`` or supplies a non-empty capability set.
# A scope only counts if it is in ``secdogie_identity.capability.GRANTABLE_SCOPES``,
# so ``run_elevated`` (whose scope is not grantable) is always refused under
# enforcement.
_CAPABILITY_FOR = {
    "click": "physical.click",
    "press": "physical.click",
    "type": "physical.type",
    "key": "physical.key",
    "scroll": "physical.scroll",
    "drag": "physical.drag",
    "open": "system.open",
    "run": "process.run",
    "run_elevated": "process.run_elevated",
    "post": "network.post",
    "publish": "network.post",
    "submit": "network.post",
    "send": "network.send",
    "reply": "network.send",
    "comment": "network.post",
    "read": "observe.read",
    "observe": "observe.read",
}


@dataclass(frozen=True)
class IntentContract:
    """Gate 1: the reasons behind one action, stated so the gate can question
    them. ``purpose`` names the goal the step serves (checked against the active
    goals when the caller knows them); ``rollback`` says how to back out if it
    fails; ``irreversible`` says there is no way back -- an acknowledgment, not
    a pass: an irreversible destructive action still needs Gate 2.
    ``requires_present`` lists target ids that must still be in the current
    observation for the step to make sense."""

    purpose: str = ""
    rollback: str = ""
    irreversible: bool = False
    requires_present: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlannedAction:
    """One concrete action the loop proposes. ``target_id`` is the 2.5 identity
    key (``target.identity_key``); ``generation`` is the 2.4 observation
    generation it was decided against; ``expected_observation`` is the
    verification contract -- what the loop expects to see afterwards;
    ``intent`` is the Gate 1 contract (not part of ``authz.action_hash``: it
    explains the action, it does not change what the action does)."""

    kind: str
    target_id: str = ""
    target_role: str = ""
    target_name: str = ""
    text: str = ""
    high_risk: bool = False
    generation: int = 0
    expected_observation: str = ""
    estimated_cost: float = 1.0
    intent: IntentContract = IntentContract()

    @property
    def mutating(self) -> bool:
        return self.kind not in _READ_KINDS

    @property
    def destructive(self) -> bool:
        return self.high_risk or self.kind in _DESTRUCTIVE_KINDS

    def signature(self) -> tuple[str, str, str]:
        """What makes two actions "the same" for repeat / no-op detection."""
        return (self.kind, self.target_id, self.text)


@dataclass(frozen=True)
class GateContext:
    """Everything the gate needs beyond the action itself. All plain data, so the
    agent computes it (from fused observations, target keys, run history) and
    hands it over -- the gate imports nothing from the agent."""

    current_generation: int = 0
    target_present_ids: frozenset[str] = field(default_factory=frozenset)
    recent_actions: tuple[PlannedAction, ...] = ()
    capabilities: frozenset[str] = field(default_factory=frozenset)
    # Turn capability checks on even with an empty set (then nothing is granted).
    # Off by default so callers that predate 2.9 keep their behavior.
    enforce_capabilities: bool = False
    cost_budget: float = 0.0  # 0 = unbudgeted
    requires_verification: bool = True
    instruction: str = ""  # the request behind this plan, for the instruction gate
    poll_window: int = 3
    repeat_threshold: int = 2
    # Gate 2 (authz.py): a destructive / irreversible action needs an
    # operator-signed authorization token bound to it. Off by default so callers
    # predating this keep their behavior; a node enforcing Gate 2 turns it on and
    # supplies the operator trust set + this node's DID. The gate only VERIFIES a
    # token the caller collected; it never mints one.
    require_authorization: bool = False
    authorization: dict | None = None
    operators: object = None  # a TrustPolicy / Allowlist of operator DIDs (revocation-aware)
    subject_did: str = ""  # this node's DID, the token's expected subject
    now: float | None = None  # clock for token expiry; None = time.time
    # Gate 1 intent. ``require_intent`` makes a mutating action state its purpose
    # (and a destructive one its rollback or irreversibility); off by default.
    # ``active_goal_ids`` empty means "unknown", not "no goals", like
    # ``target_present_ids``. ``known_failures`` are ``authz.action_hash`` keys
    # from consolidated (S3) memory: actions that failed repeatedly before.
    require_intent: bool = False
    active_goal_ids: frozenset[str] = field(default_factory=frozenset)
    known_failures: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Finding:
    kind: str
    detail: str
    verdict: str
    risk: float = 0.0


@dataclass(frozen=True)
class GateDecision:
    verdict: str
    reason: str = ""
    findings: tuple[str, ...] = ()
    replacement: PlannedAction | None = None
    risk: float = 0.0
    estimated_cost: float = 0.0
    expected_observation: str = ""

    @property
    def allowed(self) -> bool:
        return self.verdict == ALLOW


# --- Individual checks (each pure: (action, ctx) -> Finding | None) ----------


def _check_stale_target(a: PlannedAction, ctx: GateContext) -> Finding | None:
    if a.generation and ctx.current_generation and a.generation != ctx.current_generation:
        return Finding(
            STALE_TARGET,
            f"decided at generation {a.generation}, current is {ctx.current_generation}",
            REQUEST_REOBSERVE,
            risk=0.6,
        )
    return None


def _check_target_present(a: PlannedAction, ctx: GateContext) -> Finding | None:
    # Only judge presence when we were told what IS present; an empty set means
    # "unknown", not "nothing there", so it must not cause a false reject.
    if a.target_id and ctx.target_present_ids and a.target_id not in ctx.target_present_ids:
        return Finding(
            TARGET_MISMATCH,
            f"target {a.target_id!r} is not in the current observation",
            REQUEST_REOBSERVE,
            risk=0.6,
        )
    return None


def _check_no_op(a: PlannedAction, ctx: GateContext) -> Finding | None:
    if ctx.recent_actions and ctx.recent_actions[-1].signature() == a.signature() and a.mutating:
        return Finding(
            NO_OP,
            "identical to the action just taken -- likely a no-op repeat",
            REJECT,
            risk=0.2,
        )
    return None


def _check_repeated(a: PlannedAction, ctx: GateContext) -> Finding | None:
    same = sum(1 for r in ctx.recent_actions if r.signature() == a.signature())
    if same >= ctx.repeat_threshold:
        return Finding(
            REPEATED,
            f"the same action has already run {same}x -- fix the cause, don't loop",
            REJECT,
            risk=0.4,
        )
    return None


def _check_polling(a: PlannedAction, ctx: GateContext) -> Finding | None:
    if a.kind not in _READ_KINDS:
        return None
    window = ctx.recent_actions[-(ctx.poll_window - 1) :] if ctx.poll_window > 1 else ()
    if len(window) >= ctx.poll_window - 1 and window and all(r.kind in _READ_KINDS for r in window):
        return Finding(
            POLLING,
            f"{ctx.poll_window} observe-only actions in a row with no change between -- busy-poll",
            REJECT,
            risk=0.3,
        )
    return None


def _check_destructive_chain(a: PlannedAction, ctx: GateContext) -> Finding | None:
    if a.destructive and ctx.recent_actions and ctx.recent_actions[-1].destructive:
        return Finding(
            DESTRUCTIVE_CHAIN,
            "a destructive action chained onto another with no verification between",
            REJECT,
            risk=0.9,
        )
    return None


def _check_missing_verification(a: PlannedAction, ctx: GateContext) -> Finding | None:
    if ctx.requires_verification and a.mutating and not a.expected_observation.strip():
        return Finding(
            MISSING_VERIFICATION,
            "a mutating action with no declared expected observation to verify against",
            REWRITE,
            risk=0.3,
        )
    return None


def _check_excessive_cost(a: PlannedAction, ctx: GateContext) -> Finding | None:
    if ctx.cost_budget > 0 and a.estimated_cost > ctx.cost_budget:
        return Finding(
            EXCESSIVE_COST,
            f"estimated cost {a.estimated_cost} exceeds budget {ctx.cost_budget}",
            REJECT,
            risk=0.3,
        )
    return None


def _check_capability(a: PlannedAction, ctx: GateContext) -> Finding | None:
    if not (ctx.enforce_capabilities or ctx.capabilities):
        return None  # capability checks not enabled by this caller
    required = _CAPABILITY_FOR.get(a.kind)
    if required is None:
        if not a.mutating:
            return None  # pure observation kinds (wait/get/...) need no grant
        # Fail closed: an action kind with no known scope can't be granted.
        return Finding(
            OUT_OF_CAPABILITY,
            f"action {a.kind!r} has no capability scope; it is refused while capabilities are enforced",
            REJECT,
            risk=0.7,
        )
    if not capability_allows(ctx.capabilities, required):
        return Finding(
            OUT_OF_CAPABILITY,
            f"action {a.kind!r} needs capability {required!r}, which was not granted",
            REJECT,
            risk=0.7,
        )
    return None


def _check_unattended_posting(a: PlannedAction, ctx: GateContext) -> Finding | None:
    # Reuse the instruction gate: a posting/sending action whose originating
    # instruction asks to publish/send unattended is refused here too, so the
    # two Socratic layers cannot disagree about auto-posting.
    if a.kind not in _POSTING_KINDS or not ctx.instruction.strip():
        return None
    review = socratic.review(ctx.instruction)
    if any(r.startswith("unattended posting") for r in review.reasons):
        return Finding(
            UNATTENDED_POSTING,
            "the instruction asks to post/send unattended -- real-world posting needs a human",
            REJECT,
            risk=0.9,
        )
    return None


def _check_authorization(a: PlannedAction, ctx: GateContext) -> Finding | None:
    """Gate 2: a destructive / irreversible action needs a valid operator-signed
    authorization token bound to it. When enforcement is on and the action is
    destructive, verify the token; anything short of a valid one is a hard
    reject. Fail closed -- no token, or no operators configured, means reject."""
    if not ctx.require_authorization or not a.destructive:
        return None
    res = verify_authorization(
        ctx.authorization, a, operators=ctx.operators, subject=ctx.subject_did, now=ctx.now
    )
    if res.ok:
        return None
    return Finding(
        UNAUTHORIZED_ACTION,
        f"destructive action needs an operator-signed authorization: {res.reason}",
        REJECT,
        risk=1.0,
    )


def _check_intent_unproven(a: PlannedAction, ctx: GateContext) -> Finding | None:
    """Gate 1: a mutating step must say which goal it serves; a destructive one
    must also say how to back out, or acknowledge that it cannot. The gate
    cannot invent a reason or a rollback, so this rejects (the Agent then asks
    the operator) rather than rewriting."""
    if not ctx.require_intent or not a.mutating:
        return None
    missing = []
    if not a.intent.purpose.strip():
        missing.append("its purpose (which goal it serves)")
    if a.destructive and not a.intent.rollback.strip() and not a.intent.irreversible:
        missing.append("a rollback path, or an explicit irreversible acknowledgment")
    if not missing:
        return None
    return Finding(INTENT_UNPROVEN, "intent not stated: " + "; ".join(missing), REJECT, risk=0.5)


def _check_intent_contradiction(a: PlannedAction, ctx: GateContext) -> Finding | None:
    problems = []
    if a.intent.irreversible and a.intent.rollback.strip():
        problems.append("declared irreversible yet claims a rollback")
    purpose = a.intent.purpose.strip()
    if purpose and ctx.active_goal_ids and purpose not in ctx.active_goal_ids:
        problems.append(f"serves {purpose!r}, which is not an active goal")
    if not problems:
        return None
    return Finding(INTENT_CONTRADICTION, "intent contradicts itself: " + "; ".join(problems), REJECT, risk=0.6)


def _check_preconditions(a: PlannedAction, ctx: GateContext) -> Finding | None:
    # As with target presence, judge only when we know what IS present.
    if not a.intent.requires_present or not ctx.target_present_ids:
        return None
    missing = [t for t in a.intent.requires_present if t not in ctx.target_present_ids]
    if not missing:
        return None
    return Finding(
        PRECONDITION_FAILED,
        f"precondition not met: {missing} not in the current observation",
        REQUEST_REOBSERVE,
        risk=0.5,
    )


def _check_known_failure(a: PlannedAction, ctx: GateContext) -> Finding | None:
    """Consolidated memory: this exact action (by its effect hash) failed in
    several earlier runs. Only ever adds a reject -- memory cannot allow."""
    if not ctx.known_failures or action_hash(a) not in ctx.known_failures:
        return None
    return Finding(
        KNOWN_FAILURE,
        "this exact action failed repeatedly in earlier runs -- fix the cause, don't retry it",
        REJECT,
        risk=0.6,
    )


_CHECKS = (
    _check_stale_target,
    _check_target_present,
    _check_no_op,
    _check_repeated,
    _check_polling,
    _check_destructive_chain,
    _check_missing_verification,
    _check_excessive_cost,
    _check_capability,
    _check_unattended_posting,
    _check_intent_unproven,
    _check_intent_contradiction,
    _check_preconditions,
    _check_known_failure,
    _check_authorization,
)

# Verdict precedence: re-observe (the world moved, judging the rest is moot)
# beats a hard reject, which beats a cautious rewrite, which beats allow.
_PRECEDENCE = {REQUEST_REOBSERVE: 3, REJECT: 2, REWRITE: 1, ALLOW: 0}


def gate(action: PlannedAction, context: GateContext | None = None) -> GateDecision:
    """Judge one proposed action. Collects every finding, then returns the
    highest-precedence verdict; a REWRITE carries a replacement action that is
    strictly more cautious than the original."""
    ctx = context or GateContext()
    findings = [f for f in (check(action, ctx) for check in _CHECKS) if f is not None]

    if not findings:
        return GateDecision(
            ALLOW,
            "",
            (),
            None,
            0.0,
            action.estimated_cost,
            action.expected_observation,
        )

    verdict = max((f.verdict for f in findings), key=lambda v: _PRECEDENCE[v])
    risk = max(f.risk for f in findings)
    reason = "; ".join(f.detail for f in findings)
    kinds = tuple(f.kind for f in findings)

    replacement: PlannedAction | None = None
    expected = action.expected_observation
    if verdict == REWRITE:
        # The only rewrite today attaches a required verification -- more
        # cautious, never less. If other rewrites are added they must keep that
        # invariant (a rewrite cannot widen what the action does).
        if any(f.kind == MISSING_VERIFICATION for f in findings) and not expected.strip():
            expected = (
                "the targeted UI visibly changes; verify by re-observation / pixel-diff before "
                "treating this action as done"
            )
            replacement = dataclasses.replace(action, expected_observation=expected)

    return GateDecision(
        verdict=verdict,
        reason=reason,
        findings=kinds,
        replacement=replacement,
        risk=risk,
        estimated_cost=action.estimated_cost,
        expected_observation=expected,
    )


def record_gate_decision(journal, action: PlannedAction, decision: GateDecision) -> dict:
    """Append the decision to a journal as a signed ``action-gate`` event (audit
    trail), mirroring ``socratic.record_review``."""
    return journal.append(
        "action-gate",
        {
            "action": {
                "kind": action.kind,
                "target_id": action.target_id,
                "text": action.text,
                "high_risk": action.high_risk,
                "generation": action.generation,
            },
            "verdict": decision.verdict,
            "findings": list(decision.findings),
            "reason": decision.reason,
            "risk": decision.risk,
        },
    )


__all__ = [
    "ALLOW",
    "REJECT",
    "REWRITE",
    "REQUEST_REOBSERVE",
    "UNAUTHORIZED_ACTION",
    "STALE_TARGET",
    "TARGET_MISMATCH",
    "NO_OP",
    "REPEATED",
    "POLLING",
    "DESTRUCTIVE_CHAIN",
    "MISSING_VERIFICATION",
    "EXCESSIVE_COST",
    "OUT_OF_CAPABILITY",
    "UNATTENDED_POSTING",
    "INTENT_UNPROVEN",
    "INTENT_CONTRADICTION",
    "PRECONDITION_FAILED",
    "KNOWN_FAILURE",
    "IntentContract",
    "PlannedAction",
    "GateContext",
    "GateDecision",
    "Finding",
    "gate",
    "record_gate_decision",
]
