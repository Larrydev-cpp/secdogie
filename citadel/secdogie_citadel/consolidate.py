"""Consolidated memory (S3) and the Socratic memory gate.

Stage S3 is long-term memory the mesh shares: signed ``memory`` events on the
journal (``assert`` / ``retract``), replicated like every other event, projected
into a ``MemoryView``. What gets in is decided by a Socratic review of each S2
candidate, with a bar proportional to what the memory can do:

  * **CAUTION** ("this action failed in N runs") can only make the gates
    stricter, so evidence alone promotes it: it failed or had no effect in at
    least ``min_runs`` usable runs and never succeeded. The evidence is
    re-derived from verified episodes (``lessons.tally``), never taken from the
    candidate. Evidence of later success (``min_runs`` successful runs) retracts
    it -- back to the baseline, never below.
  * **FACT / PREFERENCE** can steer planning, so nothing promotes them but the
    operator: a signed ``memory-confirmation`` statement from the operator's
    session key, bound to the memory's content hash and to this node. Every
    node re-verifies it when projecting, so a node cannot claim a confirmation
    it does not have, nor move one onto different content.

The projection honours a trust policy: a ``memory`` event whose author is no
longer trusted (revoked) drops out of the view, retroactively.

What S3 feeds: ``MemoryView.known_failures`` into ``GateContext.known_failures``
(Gate 1 -- can only add caution), and ``MemoryView.render()`` into the planner's
prompt (confirmed facts and cautions only). Gate 2 reads none of it.

Pure functions over events plus thin writers that append to a journal.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from secdogie_identity import sign_payload, verify_payload

from .lessons import (
    Candidate,
    CandidateStore,
    MemoryClass,
    Tally,
    candidate_id,
    extract_cautions,
    tally,
    validate,
)

MEMORY_EVENT_KIND = "memory"
CONFIRMATION_TYPE = "secdogie/memory-confirmation/v1"
DEFAULT_MIN_RUNS = 3

ASSERT, RETRACT = "assert", "retract"
BY_EVIDENCE, BY_OPERATOR = "evidence", "operator"


# ---- the Socratic memory gate -----------------------------------------------------


@dataclass(frozen=True)
class PromotionDecision:
    promote: bool
    basis: str  # "evidence" when promote; "" otherwise
    needs_operator: bool
    reason: str
    evidence: tuple[str, ...] = ()  # the re-derived failure runs, when promoting a caution


def review_candidate(c: Candidate, tl: Tally | None, *, min_runs: int = DEFAULT_MIN_RUNS) -> PromotionDecision:
    """Should candidate ``c`` become long-term memory on evidence alone? ``tl`` is
    the tally for its key re-derived from verified episodes; the candidate's own
    evidence fields are not consulted."""
    try:
        validate(c.mclass, c.scope, c.key, c.value, c.source)
    except ValueError as e:
        return PromotionDecision(False, "", False, f"not admissible: {e}")
    if c.mclass is not MemoryClass.CAUTION:
        return PromotionDecision(False, "", True, "it can steer planning: only the operator can confirm it")
    fails = len(tl.failure_runs) if tl else 0
    min_runs = max(1, int(min_runs))  # a caution always needs at least one real failure
    if fails < min_runs:
        return PromotionDecision(False, "", False, f"failed in {fails} verified run(s); {min_runs} needed")
    if tl.success_runs:
        return PromotionDecision(False, "", False,
                                 f"also succeeded in {len(tl.success_runs)} run(s): flaky, not a known failure")
    return PromotionDecision(True, BY_EVIDENCE, False, f"failed in {fails} verified runs and never succeeded",
                             tuple(sorted(tl.failure_runs)))


# ---- operator confirmation (session key) ------------------------------------------


def create_confirmation(identity, memory_id: str, subject_did: str, *, clock=time.time) -> dict:
    """The operator's "yes, remember this", signed with the App's session key and
    bound to one memory (its content hash) on one node."""
    return sign_payload(identity, {"type": CONFIRMATION_TYPE, "memory_id": memory_id,
                                   "subject": subject_did, "confirmed_at": float(clock())})


@dataclass(frozen=True)
class ConfirmationResult:
    ok: bool
    reason: str | None = None
    signer: str | None = None


def verify_confirmation(obj, *, memory_id: str, subject: str, confirmers) -> ConfirmationResult:
    if not isinstance(obj, dict) or obj.get("type") != CONFIRMATION_TYPE:
        return ConfirmationResult(False, "not a memory confirmation (domain separation)")
    if confirmers is None:
        return ConfirmationResult(False, "no confirmers configured")
    ok, signer = verify_payload(obj, confirmers)
    if not ok:
        return ConfirmationResult(False, "confirmer not trusted or revoked" if signer else "invalid signature",
                                  signer)
    if obj.get("memory_id") != memory_id:
        return ConfirmationResult(False, "confirms a different memory", signer)
    if obj.get("subject") != subject:
        return ConfirmationResult(False, "confirms a memory on a different node", signer)
    return ConfirmationResult(True, None, signer)


# ---- writing S3 --------------------------------------------------------------------


def assert_memory(journal, c: Candidate, decision: PromotionDecision | None = None, *,
                  confirmation: dict | None = None, confirmers=None) -> dict:
    """Append an ``assert`` for ``c``: on an evidence decision (CAUTION only), or
    on a verified operator confirmation. Anything else raises ``ValueError``."""
    mclass = validate(c.mclass, c.scope, c.key, c.value, c.source)
    if c.candidate_id != candidate_id(mclass, c.scope, c.key, c.value):
        raise ValueError("candidate id does not match its content")
    if confirmation is not None:
        res = verify_confirmation(confirmation, memory_id=c.candidate_id, subject=journal.identity.did,
                                  confirmers=confirmers)
        if not res.ok:
            raise ValueError(f"confirmation refused: {res.reason}")
        basis, evidence = BY_OPERATOR, ()
    elif decision is not None and decision.promote and decision.basis == BY_EVIDENCE:
        if mclass is not MemoryClass.CAUTION:
            raise ValueError("only a caution can be promoted on evidence alone")
        basis, evidence = BY_EVIDENCE, decision.evidence
    else:
        raise ValueError("nothing to promote on: no evidence decision and no operator confirmation")
    return journal.append(MEMORY_EVENT_KIND, {
        "op": ASSERT, "memory_id": c.candidate_id, "mclass": mclass.value, "scope": c.scope,
        "key": c.key, "value": c.value, "basis": basis, "evidence": list(evidence),
        "confirmation": confirmation or {},
    })


def retract_memory(journal, memory_id: str, *, reason: str, evidence=()) -> dict:
    return journal.append(MEMORY_EVENT_KIND, {"op": RETRACT, "memory_id": memory_id, "reason": str(reason),
                                              "evidence": list(evidence)})


# ---- reading S3 ---------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryRecord:
    memory_id: str
    mclass: MemoryClass
    scope: str
    key: str
    value: str
    basis: str
    evidence: tuple[str, ...]
    confirmed_by: str  # the confirmation's signer, for operator-basis records
    author: str  # the node that asserted it


@dataclass(frozen=True)
class MemoryView:
    records: dict[str, MemoryRecord]  # active records by memory_id, in assert order

    @property
    def known_failures(self) -> frozenset[str]:
        """For ``GateContext.known_failures``."""
        return frozenset(r.key for r in self.records.values() if r.mclass is MemoryClass.CAUTION)

    def facts(self) -> dict[tuple[str, str], MemoryRecord]:
        return {(r.scope, r.key): r for r in self.records.values() if r.mclass is not MemoryClass.CAUTION}

    def render(self, *, limit: int = 20, max_chars: int = 2000) -> str:
        """A block for the planner's prompt: confirmed facts and preferences,
        then cautions. Empty string if there is nothing."""
        lines = [f"- {r.key}: {r.value}" for r in self.facts().values()]
        lines += [f"- avoid repeating: {r.value}" for r in self.records.values() if r.mclass is MemoryClass.CAUTION]
        block = "\n".join(lines[:limit])
        return block if len(block) <= max_chars else block[:max_chars].rstrip() + " ..."


def build_memory(events, *, trust=None, confirmers=None, scope: str | None = None) -> MemoryView:
    """Fold ``memory`` events (in journal total order) into the active records.

    ``trust`` (an allowlist / ``TrustPolicy``): events by authors it no longer
    contains are ignored -- revocation reaches memory retroactively.
    ``confirmers``: who may confirm operator-basis memories; without it none
    count (fail closed). ``scope``: keep ``global`` plus that scope."""
    mem = [e for e in events if isinstance(e, dict) and e.get("kind") == MEMORY_EVENT_KIND]
    mem.sort(key=_order)
    active: dict[str, MemoryRecord] = {}
    for ev in mem:
        author = str(ev.get("author", ""))
        if trust is not None and not trust.contains(author):
            continue
        body = ev.get("body")
        if not isinstance(body, dict):
            continue
        if body.get("op") == RETRACT:
            active.pop(str(body.get("memory_id", "")), None)
            continue
        rec = _admit(body, author, confirmers)
        if rec is None:
            continue
        if rec.mclass is not MemoryClass.CAUTION:  # a newer fact for the same key replaces the old
            for mid in [m for m, r in active.items() if (r.mclass, r.scope, r.key) == (rec.mclass, rec.scope, rec.key)]:
                del active[mid]
        active[rec.memory_id] = rec
    if scope is not None:
        active = {m: r for m, r in active.items() if r.scope in ("global", scope)}
    return MemoryView(active)


def _order(ev: dict):
    try:
        return (int(ev.get("lamport", 0)), str(ev.get("author", "")), int(ev.get("seq", 0)))
    except (TypeError, ValueError):
        return (0, "", 0)


def _admit(body: dict, author: str, confirmers) -> MemoryRecord | None:
    """One ``assert`` body -> a record, or None if it does not stand up."""
    if body.get("op") != ASSERT:
        return None
    try:
        basis = body["basis"]
        source = "consolidation" if basis == BY_EVIDENCE else "operator"
        mclass = validate(body["mclass"], body["scope"], body["key"], body["value"], source)
        mid, evidence, confirmation = body["memory_id"], body.get("evidence", []), body.get("confirmation") or {}
    except (KeyError, TypeError, ValueError):
        return None
    if mid != candidate_id(mclass, body["scope"], body["key"], body["value"]):
        return None  # the id must be the content's hash: nothing can be moved under it
    if not isinstance(evidence, list) or not all(isinstance(r, str) for r in evidence):
        return None
    confirmed_by = ""
    if basis == BY_EVIDENCE:
        if mclass is not MemoryClass.CAUTION or not evidence:
            return None
    elif basis == BY_OPERATOR:
        res = verify_confirmation(confirmation, memory_id=mid, subject=author, confirmers=confirmers)
        if not res.ok:
            return None
        confirmed_by = res.signer
    else:
        return None
    return MemoryRecord(mid, mclass, body["scope"], body["key"], body["value"], basis, tuple(evidence),
                        confirmed_by, author)


# ---- one consolidation pass --------------------------------------------------------


@dataclass(frozen=True)
class ConsolidationReport:
    asserted: tuple[str, ...]
    retracted: tuple[str, ...]
    held: tuple[str, ...]  # cautions not (yet) over the bar
    awaiting_operator: tuple[str, ...]  # fact / preference candidates to put to the operator


def consolidate(journal, store: CandidateStore, episodes, *, min_runs: int = DEFAULT_MIN_RUNS,
                scope: str = "global", trust=None, confirmers=None, now: float | None = None) -> ConsolidationReport:
    """S1 -> S2 -> S3 for cautions: distill cautions from ``episodes`` into the
    quarantine, promote those that pass review, retract evidence-based cautions
    the episodes now contradict. Facts and preferences are only reported: they
    wait for the operator (``confirm_and_promote``)."""
    tallies = tally(episodes)
    for c in extract_cautions(episodes, scope=scope, now=now):
        store.upsert(c)
    view = build_memory(journal.events(), trust=trust, confirmers=confirmers)
    asserted, held, retracted = [], [], []
    for c in store.items(mclass=MemoryClass.CAUTION):
        if c.candidate_id in view.records:
            store.remove(c.candidate_id)
            continue
        d = review_candidate(c, tallies.get(c.key), min_runs=min_runs)
        if d.promote:
            assert_memory(journal, c, d)
            store.remove(c.candidate_id)
            asserted.append(c.candidate_id)
        else:
            held.append(c.candidate_id)
    for rec in view.records.values():
        if rec.mclass is MemoryClass.CAUTION and rec.basis == BY_EVIDENCE:
            tl = tallies.get(rec.key)
            if tl is not None and len(tl.success_runs) >= min_runs:
                retract_memory(journal, rec.memory_id, reason=f"succeeded in {len(tl.success_runs)} verified runs",
                               evidence=sorted(tl.success_runs))
                retracted.append(rec.memory_id)
    awaiting = [c.candidate_id for c in store.items() if c.mclass is not MemoryClass.CAUTION]
    return ConsolidationReport(tuple(asserted), tuple(retracted), tuple(held), tuple(awaiting))


def confirm_and_promote(journal, store: CandidateStore, cid: str, confirmation: dict, *, confirmers) -> dict:
    """The operator confirmed quarantined candidate ``cid``: assert it and take
    it out of quarantine. Raises ``KeyError`` / ``ValueError`` otherwise."""
    c = store.get(cid)
    if c is None:
        raise KeyError(f"no candidate {cid!r}")
    ev = assert_memory(journal, c, confirmation=confirmation, confirmers=confirmers)
    store.remove(cid)
    return ev


__all__ = [
    "MEMORY_EVENT_KIND",
    "CONFIRMATION_TYPE",
    "DEFAULT_MIN_RUNS",
    "PromotionDecision",
    "review_candidate",
    "create_confirmation",
    "ConfirmationResult",
    "verify_confirmation",
    "assert_memory",
    "retract_memory",
    "MemoryRecord",
    "MemoryView",
    "build_memory",
    "ConsolidationReport",
    "consolidate",
    "confirm_and_promote",
]
