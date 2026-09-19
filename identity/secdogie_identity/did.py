"""did:key identifiers for Ed25519 public keys.

A DID (Decentralized Identifier) here is a self-certifying name derived purely
from a public key -- no registry, CA, or blockchain. We use the standard
`did:key` method with the Ed25519 multicodec, so a `did:key:z...` string *is*
the public key (multicodec-prefixed, base58btc-encoded, `z` multibase tag).
Anyone can resolve the key from the DID offline and verify a signature against
it; membership/authorization is a separate allowlist (see allowlist.py).

base58btc is implemented inline (~20 lines) rather than adding a dependency.
"""
from __future__ import annotations

# Multicodec prefix for an Ed25519 public key: unsigned-varint of 0xed -> 0xed 0x01.
_ED25519_MULTICODEC = b"\xed\x01"

_DID_KEY_PREFIX = "did:key:z"  # 'z' is the multibase tag for base58btc

# Bitcoin/IPFS base58 alphabet (base58btc).
_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {c: i for i, c in enumerate(_B58_ALPHABET)}


def _b58encode(data: bytes) -> str:
    n = int.from_bytes(data, "big")
    out = ""
    while n > 0:
        n, rem = divmod(n, 58)
        out = _B58_ALPHABET[rem] + out
    pad = 0
    for b in data:
        if b == 0:
            pad += 1
        else:
            break
    return "1" * pad + out


def _b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        try:
            n = n * 58 + _B58_INDEX[ch]
        except KeyError as exc:
            raise ValueError(f"invalid base58 character {ch!r}") from exc
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = 0
    for ch in s:
        if ch == "1":
            pad += 1
        else:
            break
    return b"\x00" * pad + body


def did_key_from_pubkey(pk: bytes) -> str:
    """Encode a 32-byte Ed25519 public key as a `did:key:z...` string."""
    if len(pk) != 32:
        raise ValueError(f"Ed25519 public key must be 32 bytes, got {len(pk)}")
    return _DID_KEY_PREFIX + _b58encode(_ED25519_MULTICODEC + pk)


def pubkey_from_did(did: str) -> bytes:
    """Recover the 32-byte Ed25519 public key from a `did:key:z...` string.

    Raises ValueError for anything that is not an Ed25519 base58btc did:key."""
    if not isinstance(did, str) or not did.startswith(_DID_KEY_PREFIX):
        raise ValueError("not a did:key (base58btc / Ed25519) DID")
    raw = _b58decode(did[len(_DID_KEY_PREFIX):])
    if raw[:2] != _ED25519_MULTICODEC:
        raise ValueError("did:key is not tagged as Ed25519 (0xed multicodec)")
    pk = raw[2:]
    if len(pk) != 32:
        raise ValueError(f"Ed25519 did:key must wrap a 32-byte key, got {len(pk)}")
    return pk


def multibase_key(did: str) -> str:
    """The `z...` multibase portion of a did:key (its verificationMethod key)."""
    if not did.startswith("did:key:"):
        raise ValueError("not a did:key DID")
    return did[len("did:key:"):]


def did_document(did: str) -> dict:
    """A resolved DID document for a did:key, for display in the console.

    Follows the did:key / Ed25519VerificationKey2020 shape."""
    pubkey_from_did(did)  # validate the DID before describing it
    mb = multibase_key(did)
    vm_id = f"{did}#{mb}"
    return {
        "@context": [
            "https://www.w3.org/ns/did/v1",
            "https://w3id.org/security/suites/ed25519-2020/v1",
        ],
        "id": did,
        "verificationMethod": [
            {
                "id": vm_id,
                "type": "Ed25519VerificationKey2020",
                "controller": did,
                "publicKeyMultibase": mb,
            }
        ],
        "authentication": [vm_id],
        "assertionMethod": [vm_id],
    }
