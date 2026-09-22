"""A peer's authenticated identity: a DID bound to a transport key.

Upper layers (fleet, citadel sync) should depend on a PeerIdentity, not on a raw
socket or a hub address. A PeerIdentity is built from a verified DID<->transport
binding (secdogie-identity, Phase 2.1), so "who is this peer" is a cryptographic
fact, not an IP address.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PeerIdentity:
    did: str
    transport_public_key: str  # base64 X25519 static key the binding authorizes
    capabilities: tuple[str, ...] = ()

    @classmethod
    def from_binding(cls, binding: dict, *, allowlist=None, now=None) -> PeerIdentity | None:
        """Build a PeerIdentity from a signed DID->transport binding, or None if
        the binding is invalid / the DID is not authorized."""
        from secdogie_identity import verify_binding

        res = verify_binding(binding, allowlist=allowlist, now=now)
        if not res.ok or res.did is None or res.transport_public_key is None:
            return None
        return cls(res.did, res.transport_public_key, tuple(res.capabilities))

    def has_capability(self, cap: str) -> bool:
        return cap in self.capabilities
