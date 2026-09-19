"""DID-signed fleet messages.

This is the *only* fleet module that imports `secdogie_identity` (and thus
PyNaCl), so the pure coordinator core and the unsigned dev/loopback path keep
their "standard library only" property. It wraps the existing `protocol.py`
codec: a signed message is the ordinary protocol JSON object plus a `signer`
(the author's did:key) and `sig` (Ed25519 signature over the canonical payload).
Because `protocol.from_json` reads only named keys and ignores unknown ones,
these two extra keys ride along without any change to the wire contract.
"""
from __future__ import annotations

import json

from secdogie_identity import Allowlist, Identity, sign_payload, verify_payload

from .protocol import Message, ProtocolError, from_json, to_json


def signed_to_json(msg: Message, identity: Identity) -> str:
    """Encode `msg` as a single signed JSON line authored by `identity`."""
    payload = json.loads(to_json(msg))
    return json.dumps(sign_payload(identity, payload), separators=(",", ":"))


def verify_and_from_json(
    line: str, allowlist: Allowlist, *, expect: frozenset[str] | None = None
) -> tuple[Message, str]:
    """Verify a signed line and decode it. Returns (message, signer_did).

    Raises ProtocolError if the line is not JSON, the signature is missing/invalid,
    or the (authentic) signer is not on `allowlist` -- so the caller handles it
    exactly like any other bad line."""
    try:
        obj = json.loads(line)
    except json.JSONDecodeError as e:
        raise ProtocolError(f"signed message is not valid JSON: {e}") from e
    if not isinstance(obj, dict):
        raise ProtocolError("signed message must be a JSON object")
    ok, signer = verify_payload(obj, allowlist)
    if not ok:
        if signer is None:
            raise ProtocolError("invalid or missing signature")
        raise ProtocolError(f"signer {signer} is not an authorized DID")
    inner = {k: v for k, v in obj.items() if k not in ("signer", "sig")}
    return from_json(json.dumps(inner), expect=expect), signer
