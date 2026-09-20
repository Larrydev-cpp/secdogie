"""DID <-> transport-key binding.

A secdogie DID is an Ed25519 *signing* key; the tunnel authenticates an X25519
*key-agreement* (transport) static key. These were two independent identities.
A binding is a short, Ed25519-signed statement in which a DID authorizes a
specific transport public key, with a version and a validity window:

    { type, did, transport_public_key, key_version, capabilities,
      valid_from, expires_at, signer, sig }

Composed authentication (no new crypto protocol invented):
  * the binding proves DID -> transport_key (this module verifies it);
  * the existing tunnel handshake proves possession of that transport key's
    private key;
so a completed session whose authenticated static key matches a valid binding
is transitively bound to the DID (see `session_did`).

Domain separation: the signed payload carries `type =
"secdogie/transport-binding/v1"`, so a binding signature can never be replayed as
a fleet message or a journal event (different type strings hash differently).
Reuses secdogie_identity.canonical / sign_payload / verify_payload.
"""
from __future__ import annotations

import base64
import hmac
import time
from dataclasses import dataclass

from .keys import Identity
from .signing import sign_payload, verify_payload

BINDING_TYPE = "secdogie/transport-binding/v1"

_DEFAULT_TTL = 365 * 24 * 3600.0  # one year


@dataclass(frozen=True)
class BindingResult:
    ok: bool
    reason: str | None = None
    did: str | None = None
    transport_public_key: str | None = None
    key_version: int = 0
    capabilities: tuple[str, ...] = ()


def _x25519_from_b64(pk_b64) -> bytes:
    if not isinstance(pk_b64, str):
        raise ValueError("transport_public_key must be a base64 string")
    raw = base64.b64decode(pk_b64, validate=True)
    if len(raw) != 32:
        raise ValueError("transport_public_key must decode to 32 bytes (X25519)")
    return raw


def create_binding(
    identity: Identity,
    transport_public_key_b64: str,
    *,
    key_version: int,
    capabilities=(),
    valid_from: float | None = None,
    expires_at: float | None = None,
    ttl: float | None = None,
    clock=time.time,
) -> dict:
    """Build and sign a DID -> transport-key binding."""
    _x25519_from_b64(transport_public_key_b64)  # validate format up front
    if isinstance(key_version, bool) or not isinstance(key_version, int) or key_version < 0:
        raise ValueError("key_version must be a non-negative int")
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
    payload = {
        "type": BINDING_TYPE,
        "did": identity.did,
        "transport_public_key": transport_public_key_b64,
        "key_version": int(key_version),
        "capabilities": list(capabilities),
        "valid_from": vf,
        "expires_at": exp,
    }
    return sign_payload(identity, payload)


def verify_binding(obj: dict, *, now: float | None = None, allowlist=None, clock=time.time) -> BindingResult:
    """Verify a binding: type tag, signature (signer == did), and validity window.

    A binding whose `now` is before `valid_from` or at/after `expires_at` is
    rejected (stale/not-yet-valid). With an allowlist, the DID must be on it."""
    if not isinstance(obj, dict):
        return BindingResult(False, "not an object")
    if obj.get("type") != BINDING_TYPE:
        return BindingResult(False, "wrong or missing type (domain separation)")
    ok, signer = verify_payload(obj, allowlist)
    if not ok:
        reason = "signer not authorized" if signer else "invalid or missing signature"
        return BindingResult(False, reason, did=signer)
    if obj.get("did") != signer:
        return BindingResult(False, "did does not match signer", did=signer)
    try:
        tpk = obj["transport_public_key"]
        _x25519_from_b64(tpk)
    except (KeyError, ValueError, TypeError):
        return BindingResult(False, "invalid transport_public_key", did=signer)
    kv = obj.get("key_version")
    if isinstance(kv, bool) or not isinstance(kv, int) or kv < 0:
        return BindingResult(False, "invalid key_version", did=signer)
    caps = obj.get("capabilities", [])
    if not isinstance(caps, list) or any(not isinstance(c, str) for c in caps):
        return BindingResult(False, "invalid capabilities", did=signer)
    vf, exp = obj.get("valid_from"), obj.get("expires_at")
    if not _is_num(vf) or not _is_num(exp):
        return BindingResult(False, "invalid validity window", did=signer)
    t = float(now) if now is not None else float(clock())
    if t < float(vf):
        return BindingResult(False, "binding not yet valid", did=signer)
    if t >= float(exp):
        return BindingResult(False, "binding expired", did=signer)
    return BindingResult(True, None, did=signer, transport_public_key=tpk,
                         key_version=int(kv), capabilities=tuple(caps))


def is_superseded(a: dict, b: dict) -> bool:
    """True if binding `b` supersedes `a`: same DID, higher key_version."""
    return a.get("did") == b.get("did") and _kv(b) > _kv(a)


def session_did(
    binding: dict, authenticated_transport_pk_b64: str, *, now: float | None = None, allowlist=None
) -> str | None:
    """The DID a session belongs to: the binding must be valid AND its bound
    transport key must equal the key the tunnel handshake authenticated.
    Constant-time key compare. Returns None on any mismatch/failure."""
    res = verify_binding(binding, now=now, allowlist=allowlist)
    if not res.ok or res.transport_public_key is None:
        return None
    try:
        want = _x25519_from_b64(res.transport_public_key)
        got = _x25519_from_b64(authenticated_transport_pk_b64)
    except (ValueError, TypeError):
        return None
    return res.did if hmac.compare_digest(want, got) else None


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _kv(b: dict) -> int:
    v = b.get("key_version")
    return v if isinstance(v, int) and not isinstance(v, bool) else -1
