"""secdogie-identity: Ed25519 DID identities (@Larryx).

Sign and verify commands and node handshakes with a self-certifying did:key,
gated by an authorized-node allowlist. One crypto dependency (PyNaCl/libsodium),
nothing hand-rolled.
"""
from __future__ import annotations

from .allowlist import Allowlist
from .binding import (
    BindingResult,
    create_binding,
    is_superseded,
    session_did,
    verify_binding,
)
from .capability import (
    GRANTABLE_SCOPES,
    CapabilityResult,
    create_capability,
    effective_scopes,
    verify_capability,
)
from .did import did_document, did_key_from_pubkey, pubkey_from_did
from .keys import Identity, PublicIdentity
from .signing import canonical, sign_payload, verify_payload

__version__ = "0.5.0"

__all__ = [
    "Identity",
    "PublicIdentity",
    "Allowlist",
    "did_key_from_pubkey",
    "pubkey_from_did",
    "did_document",
    "sign_payload",
    "verify_payload",
    "canonical",
    "create_binding",
    "verify_binding",
    "is_superseded",
    "session_did",
    "BindingResult",
    "create_capability",
    "verify_capability",
    "effective_scopes",
    "CapabilityResult",
    "GRANTABLE_SCOPES",
]
