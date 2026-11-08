"""Work permits: how a public node learns *what* it may do for you, and never
*why*.

Your Socratic gates, your decision model and everything they draw on (your
history, your memory, your rules) are evaluated on your own nodes only. They
are never injected into a node someone else runs. A machine that evaluates a
rule has to hold the rule in the clear, and its owner controls that machine's
memory: no software can hide a rule from the owner of the machine that applies
it. So the rules stay home, and what leaves is the outcome.

When your node hands a task to a public node, it has already judged the task
with your gates. It sends the task together with a **work permit**: a short,
signed statement of exactly which actions this task may take on that node,
for a few minutes:

    { type, permit_id, issuer, holder, task, task_digest,
      allow: [ {kind, target} | {kind, prefix} ... ],
      valid_from, expires_at, signer, sig }

  * **What it reveals:** the actions allowed for this one task. Not the rules,
    the decision model, your standing, or the reasons behind any of it.
  * **Bound and short-lived.** A permit names its holder (the public node's
    DID) and the digest of the task it came with, so it cannot be moved to
    another node or another task. It expires within ``MAX_TTL``.
  * **Exact, closed.** An action is allowed only if an entry names its kind and
    either its exact target or a prefix of it (a URL path, say). Entries with
    anything else are refused. At most ``MAX_ACTIONS`` entries.
  * **Domain separated.** ``type = "secdogie/work-permit/v1"``, so a permit
    can never pass as a capability grant, a Gate 2 authorization or a policy
    bundle. Verification needs an explicit issuer set: on a public node, the
    DID that sent the task.

What a permit is not: protection against a public node that ignores it. A
public node can always misbehave with its own machine. The protections for you
are elsewhere:

  * public nodes get no authority over your nodes: no journal, no dialogue, no
    capability, no signature;
  * they only get work that is safe outside your machines (public fetches,
    sealed relaying, encrypted storage);
  * their results are cross-checked.

The permit tells an honest node exactly where its job ends, and gives you an
auditable record of what you allowed.

Pure and deterministic; reuses the identity signing helpers (no new crypto).
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from secdogie_identity import Identity, pubkey_from_did, sign_payload, verify_payload
from secdogie_identity.signing import canonical

PERMIT_TYPE = "secdogie/work-permit/v1"
DEFAULT_TTL = 300.0  # five minutes
MAX_TTL = 900.0  # a permit never outlives a quarter of an hour
MAX_ACTIONS = 32
MAX_FIELD = 512  # characters in a kind, target or prefix


@dataclass(frozen=True)
class PermitResult:
    ok: bool
    reason: str | None = None
    permit_id: str | None = None
    issuer: str | None = None
    task: str | None = None
    allow: tuple[tuple[str, str, str], ...] = ()  # (kind, "target" | "prefix", value)


def task_digest(task: bytes | str) -> str:
    """The digest a permit is bound to: SHA-256 of the task as sent."""
    data = task.encode("utf-8") if isinstance(task, str) else bytes(task)
    return hashlib.sha256(data).hexdigest()


def _check_allow(allow) -> tuple[tuple[str, str, str], ...] | str:
    if not isinstance(allow, (list, tuple)):
        return "allow must be a list"
    if not allow:
        return "a permit allows at least one action"
    if len(allow) > MAX_ACTIONS:
        return f"at most {MAX_ACTIONS} actions"
    out = []
    for entry in allow:
        if not isinstance(entry, dict) or set(entry) - {"kind", "target", "prefix"}:
            return "an entry has kind and one of target or prefix, nothing else"
        kind = entry.get("kind")
        if not isinstance(kind, str) or not kind or len(kind) > MAX_FIELD:
            return "an entry needs a kind"
        has_t, has_p = "target" in entry, "prefix" in entry
        if has_t == has_p:
            return "an entry has exactly one of target or prefix"
        how = "target" if has_t else "prefix"
        value = entry[how]
        if not isinstance(value, str) or len(value) > MAX_FIELD or (how == "prefix" and not value):
            return f"an entry's {how} must be a string" + (" (not empty)" if how == "prefix" else "")
        out.append((kind, how, value))
    return tuple(sorted(set(out)))


def create_permit(issuer: Identity, holder_did: str, task_id: str, task: bytes | str, allow, *,
                  ttl: float = DEFAULT_TTL, clock=time.time) -> dict:
    """Sign a permit for ``holder_did`` to take the ``allow``ed actions for one
    task. Raises ``ValueError`` for a malformed holder, task or allow list, or a
    lifetime outside (0, MAX_TTL]."""
    pubkey_from_did(holder_did)
    if not isinstance(task_id, str) or not task_id or len(task_id) > MAX_FIELD:
        raise ValueError("a permit names its task")
    checked = _check_allow(allow)
    if isinstance(checked, str):
        raise ValueError(checked)
    if not 0 < float(ttl) <= MAX_TTL:
        raise ValueError(f"a permit lives between 0 and {int(MAX_TTL)} seconds")
    now = float(clock())
    body = {
        "type": PERMIT_TYPE,
        "issuer": issuer.did,
        "holder": holder_did,
        "task": task_id,
        "task_digest": task_digest(task),
        "allow": [{"kind": k, how: v} for k, how, v in checked],
        "valid_from": now,
        "expires_at": now + float(ttl),
    }
    body["permit_id"] = hashlib.sha256(canonical(body)).hexdigest()[:16]
    return sign_payload(issuer, body)


def verify_permit(obj, *, holder: str, issuers, task: bytes | str | None = None, now: float | None = None,
                  clock=time.time) -> PermitResult:
    """Verify a permit on the holder's side: type, a signature by an issuer on
    ``issuers`` (``signer == issuer``), addressed to ``holder``, bound to
    ``task`` when given, a well-formed allow list, and the validity window."""
    if not isinstance(obj, dict):
        return PermitResult(False, "not an object")
    if obj.get("type") != PERMIT_TYPE:
        return PermitResult(False, "wrong or missing type (domain separation)")
    if issuers is None:
        return PermitResult(False, "no trusted issuers configured")
    ok, signer = verify_payload(obj, issuers)
    if not ok:
        return PermitResult(False, "issuer not trusted" if signer else "invalid or missing signature", issuer=signer)
    if obj.get("issuer") != signer:
        return PermitResult(False, "issuer does not match signer", issuer=signer)
    if obj.get("holder") != holder:
        return PermitResult(False, "permit is for another node", issuer=signer)
    task_id = obj.get("task")
    pid = obj.get("permit_id")
    if not isinstance(task_id, str) or not task_id or not isinstance(pid, str) or not pid:
        return PermitResult(False, "invalid task or permit_id", issuer=signer)
    if task is not None and obj.get("task_digest") != task_digest(task):
        return PermitResult(False, "permit is for another task", pid, signer, task_id)
    checked = _check_allow(obj.get("allow"))
    if isinstance(checked, str):
        return PermitResult(False, checked, pid, signer, task_id)
    vf, exp = obj.get("valid_from"), obj.get("expires_at")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (vf, exp)):
        return PermitResult(False, "invalid validity window", pid, signer, task_id)
    if float(exp) - float(vf) > MAX_TTL + 1e-6 or float(exp) <= float(vf):
        return PermitResult(False, "validity window too long or empty", pid, signer, task_id)
    t = float(now) if now is not None else float(clock())
    if t < float(vf):
        return PermitResult(False, "permit not yet valid", pid, signer, task_id)
    if t >= float(exp):
        return PermitResult(False, "permit expired", pid, signer, task_id)
    return PermitResult(True, None, pid, signer, task_id, checked)


def permits(result: PermitResult, kind: str, target: str) -> bool:
    """Whether a verified permit allows this one action: the same kind, and the
    exact target or a target under an allowed prefix. Anything else: no."""
    if not result.ok:
        return False
    for k, how, value in result.allow:
        if k != kind:
            continue
        if how == "target" and target == value:
            return True
        if how == "prefix" and target.startswith(value):
            return True
    return False


__all__ = ["DEFAULT_TTL", "MAX_ACTIONS", "MAX_TTL", "PERMIT_TYPE", "PermitResult", "create_permit", "permits",
           "task_digest", "verify_permit"]
