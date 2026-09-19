"""secdogie-identity: Ed25519 DID identities (@Larryx).

Sign and verify commands and node handshakes with a self-certifying did:key,
gated by an authorized-node allowlist. One crypto dependency (PyNaCl/libsodium),
nothing hand-rolled.
"""
from __future__ import annotations

from .allowlist import Allowlist
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
]
