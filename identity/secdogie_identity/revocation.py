"""Master-signed revocation (R1.2 / 3.0).

In an unattended mesh -- headless relays, gossip and state converging with nobody
watching -- an authorization has to be withdrawable, and the withdrawal has to be
unforgeable. That is what a revocation record is: a statement, signed by the
mesh's *master* keys, that one or more DIDs are no longer authorized. A node that
sees a valid one stops trusting the named DID; a node that sees itself named
stops (see policy.py). It is an access-control statement, the decentralized
equivalent of removing a peer from an allowlist -- no new capability, no reach
into anyone's machine.

Two properties make it safe to act on automatically:

  * **Only masters can sign it.** Verification needs an explicit ``MasterSet``
    (there is no "trust any signer" mode); an ``k-of-n`` threshold means a single
    stolen master key cannot revoke on its own. Non-master signatures and bad
    signatures contribute nothing.
  * **It is permanent and monotonic.** Revocation only ever adds DIDs to the
    revoked set; it never restores one. So records can arrive in any order, more
    than once, over any path -- the result is the same union. A revoked DID
    rejoins only by minting a fresh DID and being re-authorized.

Domain separation: records carry ``type = "secdogie/revocation/v1"``, so a
revocation signature can never be replayed as a binding, grant or journal event.
Reuses ``signing.canonical`` / ``Identity.sign`` / ``PublicIdentity.verify``; no
new crypto.
"""
from __future__ import annotations

import base64
import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .did import pubkey_from_did
from .keys import Identity, PublicIdentity
from .signing import canonical

REVOCATION_TYPE = "secdogie/revocation/v1"

MAX_REVOKED = 64  # a single record names at most this many DIDs
_MASTER_KEY = "master_did"
_THRESHOLD_KEY = "threshold"


class MasterSet:
    """The master DIDs that may sign revocations, and how many must agree (k).

    Loaded from a ``key = value`` file in the same style as the allowlist:
    repeatable ``master_did = did:key:z...`` lines and one optional
    ``threshold = k`` (default 1). ``#`` comments and blank lines are ignored."""

    def __init__(self, dids, threshold: int = 1):
        seen: list[str] = []
        for did in dids:
            pubkey_from_did(did)  # a well-formed Ed25519 did:key
            if did in seen:
                raise ValueError(f"duplicate master DID: {did}")
            seen.append(did)
        if not seen:
            raise ValueError("a master set needs at least one master DID")
        if not isinstance(threshold, int) or isinstance(threshold, bool):
            raise ValueError("threshold must be an integer")
        if not 1 <= threshold <= len(seen):
            raise ValueError(f"threshold {threshold} out of range 1..{len(seen)}")
        self._dids = frozenset(seen)
        self.threshold = threshold

    def __len__(self) -> int:
        return len(self._dids)

    def contains(self, did: str) -> bool:
        return did in self._dids

    def dids(self) -> frozenset[str]:
        return self._dids

    @classmethod
    def load(cls, path: str | os.PathLike) -> MasterSet:
        dids: list[str] = []
        threshold: int | None = None
        for lineno, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            if "=" not in s:
                raise ValueError(f"{path}:{lineno}: expected 'key = value'")
            key, _, val = s.partition("=")
            key, val = key.strip(), val.strip()
            if key == _MASTER_KEY:
                dids.append(val.split()[0] if val.split() else "")
            elif key == _THRESHOLD_KEY:
                if threshold is not None:
                    raise ValueError(f"{path}:{lineno}: threshold given more than once")
                try:
                    threshold = int(val)
                except ValueError:
                    raise ValueError(f"{path}:{lineno}: threshold must be an integer, got {val!r}") from None
            else:
                raise ValueError(f"{path}:{lineno}: unknown key {key!r} "
                                 f"(expected {_MASTER_KEY} or {_THRESHOLD_KEY})")
        try:
            return cls(dids, threshold if threshold is not None else 1)
        except ValueError as exc:
            raise ValueError(f"{path}: {exc}") from exc


@dataclass(frozen=True)
class RevocationRecord:
    """A verified revocation: which DIDs it revokes and which masters signed it."""
    record_id: str
    revoked: frozenset[str]
    reason: str
    issued_at: float
    signers: frozenset[str]


def _body(revoked, reason: str, issued_at: float) -> dict:
    return {
        "type": REVOCATION_TYPE,
        "revoked": sorted(set(revoked)),
        "reason": reason,
        "issued_at": issued_at,
    }


def _record_id(body: dict) -> str:
    return hashlib.sha256(canonical(body)).hexdigest()


def create_revocation(revoked, *, reason: str = "", clock=time.time) -> dict:
    """Build an unsigned revocation naming ``revoked``. Sign it with `cosign`."""
    dids = sorted(set(revoked))
    if not dids:
        raise ValueError("a revocation must name at least one DID")
    if len(dids) > MAX_REVOKED:
        raise ValueError(f"a revocation names at most {MAX_REVOKED} DIDs, got {len(dids)}")
    for did in dids:
        pubkey_from_did(did)  # a well-formed Ed25519 did:key
    if not isinstance(reason, str):
        raise ValueError("reason must be a string")
    body = _body(dids, reason, float(clock()))
    return {**body, "record_id": _record_id(body), "sigs": []}


def cosign(identity: Identity, record: dict) -> dict:
    """Return a copy of ``record`` with ``identity``'s signature appended. A
    signer already present is a no-op, so cosigning is idempotent."""
    body = _body(record.get("revoked", ()), record.get("reason", ""), record.get("issued_at"))
    sigs = [dict(s) for s in record.get("sigs", []) if isinstance(s, dict)]
    if any(s.get("signer") == identity.did for s in sigs):
        return {**body, "record_id": record.get("record_id"), "sigs": sigs}
    sig = base64.b64encode(identity.sign(canonical(body))).decode("ascii")
    sigs.append({"signer": identity.did, "sig": sig})
    return {**body, "record_id": _record_id(body), "sigs": sigs}


def verify_revocation(obj, masters: MasterSet, *, exclude=()) -> RevocationRecord | None:
    """A ``RevocationRecord`` when ``obj`` is a well-formed revocation that
    ``masters.threshold`` distinct master DIDs (none in ``exclude``) validly
    signed, else ``None``. Non-master and invalid signatures are ignored, not
    fatal, so extra signatures never lower the count below the real one."""
    if not isinstance(obj, dict) or obj.get("type") != REVOCATION_TYPE:
        return None
    revoked = obj.get("revoked")
    reason = obj.get("reason")
    issued_at = obj.get("issued_at")
    sigs = obj.get("sigs")
    if not isinstance(revoked, list) or not (1 <= len(revoked) <= MAX_REVOKED):
        return None
    if any(not isinstance(d, str) for d in revoked) or sorted(set(revoked)) != revoked:
        return None  # must be sorted + de-duplicated + all strings
    for did in revoked:
        try:
            pubkey_from_did(did)
        except ValueError:
            return None
    if not isinstance(reason, str) or not _is_num(issued_at) or not isinstance(sigs, list):
        return None
    body = _body(revoked, reason, float(issued_at))
    record_id = _record_id(body)
    if obj.get("record_id") != record_id:
        return None  # id must commit to the body
    message = canonical(body)
    excluded = set(exclude)
    good: set[str] = set()
    for entry in sigs:
        if not isinstance(entry, dict):
            continue
        signer, sig_b64 = entry.get("signer"), entry.get("sig")
        if not isinstance(signer, str) or not isinstance(sig_b64, str):
            continue
        if signer in good or signer in excluded or not masters.contains(signer):
            continue  # only distinct, non-excluded master signatures count
        try:
            sig = base64.b64decode(sig_b64, validate=True)
        except (ValueError, TypeError):
            continue
        if PublicIdentity.from_did(signer).verify(message, sig):
            good.add(signer)
    if len(good) < masters.threshold:
        return None
    return RevocationRecord(record_id, frozenset(revoked), reason, float(issued_at), frozenset(good))


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


__all__ = [
    "REVOCATION_TYPE",
    "MAX_REVOKED",
    "MasterSet",
    "RevocationRecord",
    "create_revocation",
    "cosign",
    "verify_revocation",
]
