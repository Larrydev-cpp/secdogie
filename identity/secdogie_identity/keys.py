"""Ed25519 signing identities and their on-disk keyfile.

`Identity` holds a private signing key; `PublicIdentity` holds only a verify
key (what you get from a peer's DID). The keyfile mirrors the tunnel's
`genkey` shape exactly -- a `key = value` file written mode 0600 -- so an
operator manages secdogie DIDs the same way they manage tunnel keys.

The 32-byte value stored on disk is the Ed25519 *seed* (libsodium's private
key material); the public key and did:key are derived from it, never stored as
the secret.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path

from nacl import signing
from nacl.exceptions import BadSignatureError

from . import did as _did

_SEED_KEY = "signing_seed"


class PublicIdentity:
    """A peer's public verify key + its did:key. Verify-only."""

    def __init__(self, verify_key: signing.VerifyKey):
        self._vk = verify_key

    @property
    def public_key(self) -> bytes:
        return bytes(self._vk)

    @property
    def verify_key_b64(self) -> str:
        return base64.b64encode(bytes(self._vk)).decode("ascii")

    @property
    def did(self) -> str:
        return _did.did_key_from_pubkey(bytes(self._vk))

    @classmethod
    def from_did(cls, did: str) -> PublicIdentity:
        return cls(signing.VerifyKey(_did.pubkey_from_did(did)))

    @classmethod
    def from_b64(cls, b64: str) -> PublicIdentity:
        return cls(signing.VerifyKey(base64.b64decode(b64)))

    def verify(self, data: bytes, sig: bytes) -> bool:
        """True iff `sig` is this identity's signature over `data`."""
        try:
            self._vk.verify(data, sig)
            return True
        except (BadSignatureError, ValueError):
            return False


class Identity:
    """A signing identity. Wraps a libsodium Ed25519 signing key."""

    def __init__(self, signing_key: signing.SigningKey):
        self._sk = signing_key

    @classmethod
    def generate(cls) -> Identity:
        return cls(signing.SigningKey.generate())

    @classmethod
    def from_seed_b64(cls, b64: str) -> Identity:
        return cls(signing.SigningKey(base64.b64decode(b64)))

    @property
    def seed_b64(self) -> str:
        return base64.b64encode(bytes(self._sk)).decode("ascii")

    @property
    def public(self) -> PublicIdentity:
        return PublicIdentity(self._sk.verify_key)

    @property
    def verify_key_b64(self) -> str:
        return self.public.verify_key_b64

    @property
    def did(self) -> str:
        return self.public.did

    def sign(self, data: bytes) -> bytes:
        """Detached 64-byte Ed25519 signature over `data`."""
        return self._sk.sign(data).signature

    def save(self, path: str | os.PathLike) -> None:
        """Write the seed to `path` as a `key = value` file, mode 0600."""
        fd = os.open(os.fspath(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"# did:key: {self.did}\n")
            f.write(f"# public_key = {self.verify_key_b64}   (share this)\n")
            f.write(f"{_SEED_KEY} = {self.seed_b64}\n")

    @classmethod
    def load(cls, path: str | os.PathLike) -> Identity:
        seed: str | None = None
        for lineno, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            if "=" not in s:
                raise ValueError(f"{path}:{lineno}: expected 'key = value'")
            key, _, val = s.partition("=")
            if key.strip() == _SEED_KEY:
                seed = val.strip()
        if seed is None:
            raise ValueError(f"{path}: no {_SEED_KEY} line found")
        return cls.from_seed_b64(seed)
