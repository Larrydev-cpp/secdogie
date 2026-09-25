"""Signed capability grants (Phase 2.9).

An operator (a trusted *issuer* DID) grants a node (the *subject* DID) a set of
action scopes for a limited time. The grant is a short Ed25519-signed statement:

    { type, capability_id, issuer, subject, scopes, valid_from, expires_at,
      signer, sig }

Three properties keep it narrow:

  * **Allowlist, not denylist.** Only scopes in ``GRANTABLE_SCOPES`` can ever be
    minted, verified or matched. Anything else is refused whoever signs it, so a
    new or unexpected scope is closed by default rather than open.
  * **Scopes are distinct.** ``observe.read`` does not imply ``physical.click``:
    read != write, observe != execute. Matching is exact -- no wildcards.
  * **Fail closed.** Verification requires an explicit trusted-issuer allowlist
    (there is no "accept any signer" mode), and grants expire -- one day by
    default -- so a grant that is not renewed simply lapses.

Domain separation: grants carry ``type = "secdogie/capability/v1"``, so a grant
signature can never be replayed as a binding, fleet message or journal event.
Reuses ``sign_payload`` / ``verify_payload`` / ``canonical``; no new crypto.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from .did import pubkey_from_did
from .keys import Identity
from .signing import canonical, sign_payload, verify_payload

CAPABILITY_TYPE = "secdogie/capability/v1"

_DEFAULT_TTL = 24 * 3600.0  # one day

# The only scopes that can ever be granted. They match the action-plan gate's
# action -> scope mapping (citadel/action_gate.py). Anything not listed here --
# including elevated process launch -- is not grantable.
GRANTABLE_SCOPES = frozenset({
    "observe.read",
    "physical.click",
    "physical.type",
    "physical.key",
    "physical.scroll",
    "physical.drag",
    "system.open",
    "process.run",
    "network.post",
    "network.send",
})


@dataclass(frozen=True)
class CapabilityResult:
    ok: bool
    reason: str | None = None
    capability_id: str | None = None
    issuer: str | None = None
    subject: str | None = None
    scopes: tuple[str, ...] = ()


def _check_scopes(scopes) -> tuple[str, ...] | str:
    """Normalized scopes, or a reason string if they are not acceptable."""
    if not isinstance(scopes, (list, tuple, set, frozenset)):
        return "scopes must be a list"
    if any(not isinstance(s, str) for s in scopes):
        return "scopes must be strings"
    if not scopes:
        return "a capability needs at least one scope"
    bad = sorted(s for s in scopes if s not in GRANTABLE_SCOPES)
    if bad:
        return f"not grantable: {', '.join(bad)}"
    return tuple(sorted(set(scopes)))


def create_capability(
    issuer: Identity,
    subject_did: str,
    scopes,
    *,
    valid_from: float | None = None,
    expires_at: float | None = None,
    ttl: float | None = None,
    clock=time.time,
) -> dict:
    """Build and sign a grant of ``scopes`` from ``issuer`` to ``subject_did``.
    Raises ``ValueError`` for a malformed subject, an empty scope list, a scope
    outside ``GRANTABLE_SCOPES``, or an empty validity window."""
    pubkey_from_did(subject_did)  # a well-formed Ed25519 did:key
    checked = _check_scopes(scopes)
    if isinstance(checked, str):
        raise ValueError(checked)
    now = float(clock())
    vf = float(valid_from) if valid_from is not None else now
    if expires_at is not None:
        exp = float(expires_at)
    elif ttl is not None:
        exp = vf + float(ttl)
    else:
        exp = vf + _DEFAULT_TTL
    if exp <= vf:
        raise ValueError("expires_at must be after valid_from")
    body = {
        "type": CAPABILITY_TYPE,
        "issuer": issuer.did,
        "subject": subject_did,
        "scopes": list(checked),
        "valid_from": vf,
        "expires_at": exp,
    }
    # A content-derived id: identical grants share an id, distinct ones don't.
    body["capability_id"] = hashlib.sha256(canonical(body)).hexdigest()[:16]
    return sign_payload(issuer, body)


def verify_capability(
    obj,
    *,
    issuers,
    subject: str | None = None,
    now: float | None = None,
    clock=time.time,
) -> CapabilityResult:
    """Verify a grant: type tag, a signature by an issuer on ``issuers``
    (``signer == issuer``), an optional expected ``subject``, grantable scopes, and
    the validity window. ``issuers`` is required -- without a trusted-issuer list
    nothing verifies."""
    if not isinstance(obj, dict):
        return CapabilityResult(False, "not an object")
    if obj.get("type") != CAPABILITY_TYPE:
        return CapabilityResult(False, "wrong or missing type (domain separation)")
    if issuers is None:
        return CapabilityResult(False, "no trusted issuers configured")
    ok, signer = verify_payload(obj, issuers)
    if not ok:
        reason = "issuer not trusted" if signer else "invalid or missing signature"
        return CapabilityResult(False, reason, issuer=signer)
    if obj.get("issuer") != signer:
        return CapabilityResult(False, "issuer does not match signer", issuer=signer)
    subj = obj.get("subject")
    if not isinstance(subj, str):
        return CapabilityResult(False, "invalid subject", issuer=signer)
    if subject is not None and subj != subject:
        return CapabilityResult(False, "grant is for a different subject", issuer=signer, subject=subj)
    cid = obj.get("capability_id")
    if not isinstance(cid, str) or not cid:
        return CapabilityResult(False, "invalid capability_id", issuer=signer, subject=subj)
    checked = _check_scopes(obj.get("scopes"))
    if isinstance(checked, str):
        return CapabilityResult(False, checked, cid, signer, subj)
    vf, exp = obj.get("valid_from"), obj.get("expires_at")
    if not _is_num(vf) or not _is_num(exp):
        return CapabilityResult(False, "invalid validity window", cid, signer, subj)
    t = float(now) if now is not None else float(clock())
    if t < float(vf):
        return CapabilityResult(False, "grant not yet valid", cid, signer, subj)
    if t >= float(exp):
        return CapabilityResult(False, "grant expired", cid, signer, subj)
    return CapabilityResult(True, None, cid, signer, subj, checked)


def effective_scopes(grants, *, subject: str, issuers, now: float | None = None, clock=time.time) -> frozenset:
    """The union of scopes ``subject`` currently holds across ``grants``. Grants
    that fail verification (untrusted, tampered, expired, for someone else) simply
    contribute nothing."""
    out: set[str] = set()
    for g in grants:
        res = verify_capability(g, issuers=issuers, subject=subject, now=now, clock=clock)
        if res.ok:
            out.update(res.scopes)
    return frozenset(out)


def allows(granted, required: str) -> bool:
    """Whether a granted scope set covers ``required``: exact match, and the
    required scope must itself be grantable."""
    return required in GRANTABLE_SCOPES and required in granted


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


__all__ = [
    "CAPABILITY_TYPE",
    "GRANTABLE_SCOPES",
    "CapabilityResult",
    "create_capability",
    "verify_capability",
    "effective_scopes",
    "allows",
]
