"""The operator's Gate 2 signing key, encrypted at rest.

The operator key is what authorizes a destructive action, so it is kept apart
from the App's session (device) key that signs ordinary envelopes: the session
key stays unlocked while the App is connected; the operator key is unlocked with
a passphrase only when the operator approves a Gate 2 challenge, used for that
one signature, and dropped. A stolen session key can talk to a node; it cannot
authorize anything.

At rest: the Ed25519 seed sealed with ``SecretBox`` (XSalsa20-Poly1305) under a
key derived from the passphrase with Argon2id -- all PyNaCl / libsodium, the same
library the tunnel links. Nothing hand-rolled. The file is written 0600 and never
overwritten, so a slip cannot destroy the only copy of a key.

Limits, stated plainly: the seed is in process memory while an ``Identity`` built
from it is alive. CPython cannot guarantee wiping it, so this module does not
claim to; the App keeps that window short by unsealing per approval.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from nacl import pwhash, secret, utils
from nacl.exceptions import CryptoError
from secdogie_identity import Identity

KEYSTORE_TYPE = "secdogie/operator-keystore/v1"

_A = pwhash.argon2id
# Accepted cost range when reading a file: at least libsodium's minimum, at most
# its "sensitive" preset -- so a tampered file cannot demand a runaway KDF.
_OPS_RANGE = (_A.OPSLIMIT_MIN, _A.OPSLIMIT_SENSITIVE)
_MEM_RANGE = (_A.MEMLIMIT_MIN, _A.MEMLIMIT_SENSITIVE)


class KeystoreError(ValueError):
    """Wrong passphrase, a corrupted or tampered keystore, or a refused write."""


def _derive(passphrase: bytes, salt: bytes, opslimit: int, memlimit: int) -> bytes:
    return _A.kdf(secret.SecretBox.KEY_SIZE, passphrase, salt, opslimit=opslimit, memlimit=memlimit)


def seal_identity(identity: Identity, passphrase: bytes, path: str | os.PathLike, *,
                  opslimit: int = _A.OPSLIMIT_MODERATE, memlimit: int = _A.MEMLIMIT_MODERATE) -> None:
    """Encrypt ``identity``'s seed under ``passphrase`` into a new file at
    ``path`` (mode 0600). Refuses an empty passphrase and an existing file."""
    if not passphrase:
        raise KeystoreError("an empty passphrase protects nothing")
    if not (_OPS_RANGE[0] <= opslimit <= _OPS_RANGE[1] and _MEM_RANGE[0] <= memlimit <= _MEM_RANGE[1]):
        raise KeystoreError("Argon2id cost outside the accepted range")
    salt = utils.random(_A.SALTBYTES)
    box = secret.SecretBox(_derive(passphrase, salt, opslimit, memlimit))
    sealed = box.encrypt(base64.b64decode(identity.seed_b64))
    doc = {
        "type": KEYSTORE_TYPE,
        "did": identity.did,
        "kdf": "argon2id",
        "opslimit": opslimit,
        "memlimit": memlimit,
        "salt": base64.b64encode(salt).decode("ascii"),
        "box": base64.b64encode(bytes(sealed)).decode("ascii"),
    }
    try:
        fd = os.open(os.fspath(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise KeystoreError(f"{path} already exists; refusing to overwrite a key") from None
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, sort_keys=True)
        f.write("\n")


def keystore_did(path: str | os.PathLike) -> str:
    """The DID a keystore claims to hold (unauthenticated until unsealed)."""
    return _load(path)["did"]


def unseal_identity(path: str | os.PathLike, passphrase: bytes, *, expected_did: str | None = None) -> Identity:
    """Decrypt the keystore at ``path``. Raises ``KeystoreError`` on a wrong
    passphrase, any tampering, a key that does not match the file's DID, or --
    when ``expected_did`` is given -- a key other than the operator's."""
    doc = _load(path)
    ops, mem = doc["opslimit"], doc["memlimit"]
    if not (_OPS_RANGE[0] <= ops <= _OPS_RANGE[1] and _MEM_RANGE[0] <= mem <= _MEM_RANGE[1]):
        raise KeystoreError("Argon2id cost outside the accepted range")
    try:
        salt = base64.b64decode(doc["salt"], validate=True)
        sealed = base64.b64decode(doc["box"], validate=True)
    except ValueError:
        raise KeystoreError("keystore fields are not valid base64") from None
    if len(salt) != _A.SALTBYTES:
        raise KeystoreError("keystore salt has the wrong length")
    try:
        seed = secret.SecretBox(_derive(passphrase, salt, ops, mem)).decrypt(sealed)
    except CryptoError:
        raise KeystoreError("wrong passphrase, or the keystore was tampered with") from None
    identity = Identity.from_seed_b64(base64.b64encode(seed).decode("ascii"))
    if identity.did != doc["did"]:
        raise KeystoreError("the key inside does not match the keystore's DID")
    if expected_did is not None and identity.did != expected_did:
        raise KeystoreError("this keystore holds a different operator's key")
    return identity


def _load(path) -> dict:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise KeystoreError(f"cannot read keystore: {e}") from None
    if not isinstance(doc, dict) or doc.get("type") != KEYSTORE_TYPE or doc.get("kdf") != "argon2id":
        raise KeystoreError("not an operator keystore")
    for k, t in (("did", str), ("salt", str), ("box", str), ("opslimit", int), ("memlimit", int)):
        if type(doc.get(k)) is not t:
            raise KeystoreError(f"keystore field {k!r} is missing or malformed")
    return doc


__all__ = ["KEYSTORE_TYPE", "KeystoreError", "seal_identity", "unseal_identity", "keystore_did"]
