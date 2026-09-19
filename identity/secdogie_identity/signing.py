"""Sign and verify a JSON payload with a DID, reusing the same canonical-JSON
encoding as the agent's execution trace (sort_keys + tight separators) so a
signature is reproducible byte-for-byte by any verifier regardless of dict order.

A signed message is the payload dict plus two extra keys, `signer` (the author's
did:key) and `sig` (base64 detached Ed25519 signature over the canonical bytes of
the payload *without* those two keys). This layers on top of any existing JSON
contract that ignores unknown keys -- e.g. fleet's protocol decoder.
"""
from __future__ import annotations

import base64
import json

from .allowlist import Allowlist
from .keys import Identity, PublicIdentity

_ENVELOPE_KEYS = ("signer", "sig")


def canonical(payload: dict) -> bytes:
    """Canonical JSON bytes: sorted keys, tight separators, UTF-8 (matches
    agent/secdogie_agent/trace.py so hashes/signatures agree across languages)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign_payload(identity: Identity, payload: dict) -> dict:
    """Return `payload` plus `signer`/`sig`. Payload must not already carry them."""
    if any(k in payload for k in _ENVELOPE_KEYS):
        raise ValueError("payload already contains a 'signer'/'sig' envelope")
    sig = identity.sign(canonical(payload))
    return {**payload, "signer": identity.did, "sig": base64.b64encode(sig).decode("ascii")}


def verify_payload(obj: dict, allowlist: Allowlist | None = None) -> tuple[bool, str | None]:
    """Verify a signed object.

    Returns (ok, signer_did):
      * (True, did)   signature valid and (if an allowlist was given) authorized;
      * (False, did)  signature valid but the signer is not in the allowlist;
      * (False, None) missing/malformed envelope or invalid signature.
    """
    if not isinstance(obj, dict):
        return False, None
    signer = obj.get("signer")
    sig_b64 = obj.get("sig")
    if not isinstance(signer, str) or not isinstance(sig_b64, str):
        return False, None
    payload = {k: v for k, v in obj.items() if k not in _ENVELOPE_KEYS}
    try:
        pub = PublicIdentity.from_did(signer)
        sig = base64.b64decode(sig_b64, validate=True)
    except (ValueError, TypeError):
        return False, None
    if not pub.verify(canonical(payload), sig):
        return False, None
    if allowlist is not None and not allowlist.contains(signer):
        return False, signer
    return True, signer
