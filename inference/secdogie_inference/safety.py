"""The safety decision layer: whether an answer may reach the user.

``SafetyLayer.inspect`` judges one ``(decision, response)`` against the user's
signed request, from signatures and deterministic screens only -- no model sits
in this path, because the thing being guarded against is a model. In order:

  1. the decision verifies and is for this request; one that works against
     the request is ``decider.tampering`` evidence;
  2. the response verifies, answers this decision, and the decider and the
     node and model are not latched;
  3. the embedded lease is signed by a trusted broker, for this node and this
     model (if not: ``provider.lease-violation``), unrevoked, current;
  4. a second, different answer from the same node to the same request and
     model is ``provider.equivocation``;
  5. the lease's canary in the answer is ``provider.secret-exfiltration``;
  6. for an action-bearing request, a forbidden proposal is
     ``model.forbidden-capability``; an ungranted one is rejected.

A failed check rejects that response. A check that produced evidence also
returns the evidence, for the caller to sign as an incident and publish.

``adjudicate`` decides the request: any evidence intercepts it outright (fail
closed -- the content is withheld, not delivered with a warning); otherwise it
needs ``quorum`` valid answers from distinct nodes, and with a quorum above one
they must agree. ``default_agree`` compares answers that parse as one JSON
value structurally and anything else as whitespace- and case-normalised text;
plug in your own comparator for free-form answers.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from secdogie_identity.signing import canonical

from . import screen
from .lease import LeaseBook, check_lease
from .mutiny import (
    DECIDER_TAMPERING,
    MODEL_FORBIDDEN_CAPABILITY,
    PROVIDER_EQUIVOCATION,
    PROVIDER_LEASE_VIOLATION,
    PROVIDER_SECRET_EXFILTRATION,
    MutinyLedger,
    decider_target,
    model_target,
)
from .protocol import (
    MAX_SKEW,
    MODE_LOCKDOWN,
    STATUS_OK,
    check_advisory,
    check_decision,
    check_response,
    decision_violations,
    routed_through,
)
from .session import BoundedCache

ACCEPTED = "accepted"
INTERCEPTED = "intercepted"
INSUFFICIENT = "insufficient"


@dataclass(frozen=True)
class Finding:
    ok: bool
    reason: str | None = None
    evidence: tuple[str, dict] | None = None  # (incident kind, evidence) to sign and publish


@dataclass(frozen=True)
class Verdict:
    status: str
    output: str | None = None
    providers: tuple[str, ...] = ()                # whose answers carried the verdict
    reasons: tuple[str, ...] = ()
    evidence: tuple[tuple[str, dict], ...] = ()
    rejected: tuple[tuple[str, str], ...] = ()     # (provider, why) for answers that did not count
    dissent: tuple[str, ...] = ()                  # providers whose valid answers disagreed


def _sole_json(text: str):
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def default_agree(a: str, b: str) -> bool:
    ja, jb = _sole_json(a), _sole_json(b)
    if ja is not None and jb is not None:
        return canonical({"v": ja}) == canonical({"v": jb})
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


class SafetyLayer:
    def __init__(self, trust, leases: LeaseBook, ledger: MutinyLedger, *,
                 agree: Callable[[str, str], bool] = default_agree, max_skew: float = MAX_SKEW,
                 memory: int = 4096, memory_bytes: int = 16 << 20):
        self._trust = trust
        self._leases = leases
        self._ledger = ledger
        self._agree = agree
        self._skew = float(max_skew)
        # (provider, request, model) -> the answer it signed: equivocation memory, bounded
        # by count and by the answers' size
        self._seen = BoundedCache(max_items=int(memory), max_bytes=int(memory_bytes),
                                  size=lambda r: 1024 + len(r.get("output", "")))

    def inspect(self, request: dict, decision: dict, response: dict, *, now: float,
                advisory: dict | None = None) -> Finding:
        reason = check_decision(decision, self._trust, request=request)
        if reason:
            return Finding(False, f"decision rejected: {reason}")
        violations = decision_violations(decision, request)
        evidence = {"request": request, "decision": decision}
        if (advisory is not None and advisory.get("epoch") == decision["epoch"]
                and check_advisory(advisory, self._trust) is None
                and routed_through(decision, advisory["quarantined"])):
            violations.append("quarantined-hop")
            evidence["advisory"] = advisory
        if violations:
            return Finding(False, f"decision tampering: {', '.join(violations)}", (DECIDER_TAMPERING, evidence))
        if decision["mode"] != MODE_LOCKDOWN and self._ledger.is_latched(decider_target(decision["decider"])):
            return Finding(False, "the decider is latched")

        reason = check_response(response, self._trust, request=request, decision=decision)
        if reason:
            return Finding(False, f"response rejected: {reason}")
        provider, model, lease = response["provider"], response["model"], response["lease"]
        if self._ledger.excludes_node(provider) or self._ledger.is_latched(model_target(model)):
            return Finding(False, "the node or model is latched")

        if check_lease(lease, self._trust) or lease["subject"] != provider or lease["model"] != model:
            return Finding(False, "lease violation", (PROVIDER_LEASE_VIOLATION, {"response": response}))
        reason = self._leases.check(lease, provider=provider, model=model, now=now, skew=self._skew)
        if reason:
            return Finding(False, reason)
        if not request["issued_at"] - self._skew <= response["issued_at"] <= now + self._skew:
            return Finding(False, "the response is dated outside the request's life")
        if response["status"] != STATUS_OK:
            return Finding(False, f"refused by the model node: {response['reason'] or 'no reason given'}")

        key = (provider, response["request_hash"], model)
        earlier = self._seen.get(key)
        if earlier is not None and earlier["output"] != response["output"]:
            return Finding(False, "equivocation", (PROVIDER_EQUIVOCATION, {"a": earlier, "b": response}))
        self._remember(key, response)

        if screen.canary_leaked(response["output"], lease["canary_hash"]):
            return Finding(False, "lease material in the answer",
                           (PROVIDER_SECRET_EXFILTRATION, {"response": response}))
        if request["scopes"]:
            bad = screen.forbidden(response["output"])
            if bad:
                return Finding(False, f"forbidden proposal: {', '.join(bad)}",
                               (MODEL_FORBIDDEN_CAPABILITY, {"request": request, "response": response}))
            over = screen.overreach(response["output"], request["scopes"])
            if over:
                return Finding(False, f"beyond the grant: {', '.join(over)}")
        return Finding(True)

    def _remember(self, key, response) -> None:
        self._seen.put(key, response)

    def adjudicate(self, request: dict, items: Iterable[tuple[dict, dict]], *, now: float,
                   quorum: int | None = None, advisory: dict | None = None) -> Verdict:
        need = int(quorum if quorum is not None else request["quorum"])
        valid: dict[str, dict] = {}
        reasons, evidence, rejected = [], [], []
        for decision, response in items:
            finding = self.inspect(request, decision, response, now=now, advisory=advisory)
            who = response.get("provider", "?") if isinstance(response, dict) else "?"
            if finding.evidence is not None:
                evidence.append(finding.evidence)
            if not finding.ok:
                reasons.append(finding.reason)
                rejected.append((who, finding.reason))
            elif who not in valid:
                valid[who] = response
        if evidence:
            return Verdict(INTERCEPTED, reasons=tuple(reasons), evidence=tuple(evidence), rejected=tuple(rejected))
        if len(valid) < need:
            return Verdict(INSUFFICIENT, reasons=tuple(reasons) + (f"{len(valid)} of {need} answers valid",),
                           rejected=tuple(rejected))
        groups: list[list[dict]] = []
        for r in valid.values():
            for g in groups:
                if self._agree(g[0]["output"], r["output"]):
                    g.append(r)
                    break
            else:
                groups.append([r])
        best = max(groups, key=len)
        if len(best) < need:
            return Verdict(INTERCEPTED, reasons=tuple(reasons) + ("the model nodes disagree",),
                           rejected=tuple(rejected), dissent=tuple(sorted(valid)))
        agreed = tuple(r["provider"] for r in best)
        return Verdict(ACCEPTED, output=best[0]["output"], providers=agreed, reasons=tuple(reasons),
                       rejected=tuple(rejected),
                       dissent=tuple(sorted(p for p in valid if p not in agreed)))


__all__ = ["SafetyLayer", "Finding", "Verdict", "default_agree", "ACCEPTED", "INTERCEPTED", "INSUFFICIENT"]
