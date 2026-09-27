"""Gate 2: cryptographic authorization for irreversible actions (T2).

The action-plan gate (``action_gate.py``) can judge an action cautious or
destructive, but a destructive, physically-irreversible action -- deleting core
files, moving funds, an elevated launch -- must not proceed on the node's own
say-so. It requires a **human operator's Ed25519 signature**, bound to that exact
action, before it is allowed. This is the human-in-the-loop confirmation
expressed cryptographically:

  * the operator's signing key lives with the operator, off the node;
  * the node holds no operator key, so it cannot mint its own authorization;
  * there is no code path that lets a destructive action through without a
    valid, unrevoked, unexpired signature bound to that action's hash.

So this strengthens the "high-risk is always confirmed by a human, no off
switch" rule; it does not replace it. An authorization is not a general
capability grant (``identity/capability.py`` covers standing scopes) -- it is a
one-shot approval of one concrete action, short-lived and non-transferable
because it commits to the action's hash and the node's DID.

Reuses ``signing.canonical`` / ``sign_payload`` / ``verify_payload`` and a
``TrustPolicy`` (a revoked operator's signatures stop counting automatically).
No new crypto. Pure and deterministic: unit-tested headless.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from secdogie_identity import sign_payload, verify_payload
from secdogie_identity.signing import canonical

AUTHORIZATION_TYPE = "secdogie/action-authorization/v1"

_DEFAULT_TTL = 300.0  # a per-action approval is short-lived: 5 minutes

# The PlannedAction fields that define *what the action does*. The token commits
# to a hash of exactly these, so an approval for "click Save" cannot be replayed
# to authorize "delete /etc" -- any change to one of these changes the hash.
_AUTHORIZED_FIELDS = ("kind", "target_id", "target_role", "target_name", "text", "high_risk")


def action_hash(action) -> str:
    """A stable content hash of the action's effect-defining fields. Two actions
    that do the same thing hash the same; any change to kind / target / text /
    risk changes it."""
    body = {k: _plain(getattr(action, k, None)) for k in _AUTHORIZED_FIELDS}
    return hashlib.sha256(canonical(body)).hexdigest()


def _plain(v):
    # canonical() needs JSON-native scalars; PlannedAction fields already are.
    return v


def create_authorization(
    operator,
    action,
    subject_did: str,
    *,
    valid_from: float | None = None,
    expires_at: float | None = None,
    ttl: float | None = None,
    clock=time.time,
) -> dict:
    """Sign an authorization for ``action`` on the node ``subject_did``. The
    operator does this off the node (it holds the signing key). Raises
    ``ValueError`` for an empty validity window."""
    now = float(clock())
    vf = float(valid_from) if valid_from is not None else now
    if expires_at is not None:
        exp = float(expires_at)
    else:
        exp = vf + float(ttl if ttl is not None else _DEFAULT_TTL)
    if exp <= vf:
        raise ValueError("expires_at must be after valid_from")
    body = {
        "type": AUTHORIZATION_TYPE,
        "action_hash": action_hash(action),
        "subject": subject_did,
        "valid_from": vf,
        "expires_at": exp,
    }
    return sign_payload(operator, body)


@dataclass(frozen=True)
class AuthzResult:
    ok: bool
    reason: str | None = None
    signer: str | None = None
    action_hash: str | None = None


def verify_authorization(
    token,
    action,
    *,
    operators,
    subject: str,
    now: float | None = None,
    clock=time.time,
) -> AuthzResult:
    """Whether ``token`` is a valid operator authorization for ``action`` on this
    node (``subject``). Every check must pass:

      * type tag is the authorization type (domain separation);
      * ``action_hash`` equals this action's hash (the approval is for exactly
        this action, not a different one);
      * ``subject`` is this node's DID (an approval for another node does not
        apply here);
      * the validity window covers now (short-lived, so a captured token lapses);
      * the signature is valid AND the signer is an operator on ``operators``
        -- a ``TrustPolicy``, so a revoked operator's signature no longer counts.

    ``operators`` is required; without a trusted-operator set nothing verifies.
    Returns ``AuthzResult(ok=False, ...)`` with a reason on any failure."""
    if not isinstance(token, dict):
        return AuthzResult(False, "not an authorization object")
    if token.get("type") != AUTHORIZATION_TYPE:
        return AuthzResult(False, "wrong or missing type (domain separation)")
    if operators is None:
        return AuthzResult(False, "no trusted operators configured")
    ok, signer = verify_payload(token, operators)
    if not ok:
        reason = "operator not trusted or revoked" if signer else "invalid or missing signature"
        return AuthzResult(False, reason, signer=signer)
    expected = action_hash(action)
    if token.get("action_hash") != expected:
        return AuthzResult(False, "token authorizes a different action", signer=signer,
                           action_hash=expected)
    if token.get("subject") != subject:
        return AuthzResult(False, "token is for a different node", signer=signer, action_hash=expected)
    vf, exp = token.get("valid_from"), token.get("expires_at")
    if not _is_num(vf) or not _is_num(exp):
        return AuthzResult(False, "invalid validity window", signer=signer, action_hash=expected)
    t = float(now) if now is not None else float(clock())
    if t < float(vf):
        return AuthzResult(False, "authorization not yet valid", signer=signer, action_hash=expected)
    if t >= float(exp):
        return AuthzResult(False, "authorization expired", signer=signer, action_hash=expected)
    return AuthzResult(True, None, signer=signer, action_hash=expected)


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


__all__ = [
    "AUTHORIZATION_TYPE",
    "AuthzResult",
    "action_hash",
    "create_authorization",
    "verify_authorization",
]
