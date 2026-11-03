"""Key leases: how the key authority ("API key" in the diagram) lends an
upstream model credential to a model node without the mesh ever holding a
standing secret.

A lease has a public half and a sealed half:

  * the **public lease**, signed by the broker, says *who* may answer *with
    which model* until *when*, under which key version, and commits to a
    canary by hash. It travels freely -- model nodes advertise it in their
    heartbeats, and embed it in every response -- so any node or user can check
    that an answer came from a node entitled to give it;
  * the **sealed grant** carries the secret itself and the canary, sealed with
    PyNaCl's ``SealedBox`` to the model node's X25519 transport key (the one
    its signed DID binding names). Only that node can open it. The sealed body
    names the lease and the canary, whose hash the signed lease commits to, so a
    sealed body cannot be swapped onto another lease.

The canary is a honeytoken (``sdgc_`` + 32 hex). It sits beside the secret in
the node's lease material and nowhere else. If it ever shows up in an answer,
the lease material has leaked into model output: anyone can prove it from the
signed response and the public lease, because the token hashes to the lease's
``canary_hash`` (see ``mutiny.PROVIDER_SECRET_EXFILTRATION``). It catches a dump
of the lease material, not a careful exfiltrator who strips it; rotating the
key on every node-level mutiny is the backstop for that.

Leases are short (ten minutes by default) and revocable by the broker; a
revocation is a broker-signed list of lease ids, applied by every node.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from abc import ABC, abstractmethod

from nacl.exceptions import CryptoError
from nacl.public import PrivateKey, PublicKey, SealedBox
from secdogie_identity import sign_payload
from secdogie_identity.signing import canonical

from .protocol import (
    MAX_REASON,
    MAX_SKEW,
    digest,
    is_did,
    is_hex32,
    is_hex64,
    is_int,
    is_model,
    is_num,
    signed_by,
)

LEASE = "secdogie/inference/lease/v1"
LEASE_REVOCATION = "secdogie/inference/lease-revocation/v1"

CANARY_PREFIX = "sdgc_"
CANARY = re.compile(r"sdgc_[0-9a-f]{32}")
MAX_REVOKED = 64
DEFAULT_TTL = 600.0
# The longest lease a broker may sign. It also bounds how long a revoked lease
# id must be remembered: a lease exists before its revocation, so it is dead
# by the revocation's time plus this much, whether or not it was ever seen.
MAX_LEASE_TTL = 3600.0
_ENVELOPE = ("lease_id", "signer", "sig")


def new_canary() -> str:
    return CANARY_PREFIX + secrets.token_hex(16)


def canary_hash(canary: str) -> str:
    return hashlib.sha256(canary.encode("ascii")).hexdigest()


class LeaseSecret:
    """A model node's opened lease: the public lease plus what was sealed.

    The secret and the canary are held in ``bytearray``s and zeroed by
    ``wipe()`` when the lease expires or is revoked. ``secret`` and ``canary``
    hand out a fresh ``str`` for the backend call and the output guard, and
    raise once wiped. CPython cannot zero those ``str`` copies, nor the one
    ``json.loads`` made while opening the grant: the honest promise is that the
    secret is referenced for as short a time as the code can manage, not that
    it is erased from the process.
    """

    __slots__ = ("lease", "_secret", "_canary")

    def __init__(self, lease: dict, secret: str, canary: str):
        self.lease = lease
        self._secret = bytearray(secret.encode("utf-8"))
        self._canary = bytearray(canary.encode("ascii"))

    @property
    def lease_id(self) -> str:
        return self.lease["lease_id"]

    @property
    def model(self) -> str:
        return self.lease["model"]

    @property
    def wiped(self) -> bool:
        return not self._secret

    @property
    def secret(self) -> str:
        if self.wiped:
            raise ValueError("the lease secret was wiped")
        return self._secret.decode("utf-8")

    @property
    def canary(self) -> str:
        if self.wiped:
            raise ValueError("the lease secret was wiped")
        return self._canary.decode("ascii")

    def wipe(self) -> None:
        for buf in (self._secret, self._canary):
            buf[:] = bytes(len(buf))
            del buf[:]

    def __repr__(self) -> str:
        return f"LeaseSecret({self.lease_id}, {self.model}, {'wiped' if self.wiped else 'held'})"


# -- the public lease -----------------------------------------------------------

def make_lease(broker, subject: str, model: str, *, key_version: int, canary: str,
               ttl: float = DEFAULT_TTL, clock=time.time) -> dict:
    now = float(clock())
    body = {
        "type": LEASE,
        "broker": broker.did,
        "subject": subject,
        "model": model,
        "key_version": int(key_version),
        "valid_from": now,
        "expires_at": now + float(ttl),
        "canary_hash": canary_hash(canary),
    }
    body["lease_id"] = digest(body)[:32]  # the random canary makes every lease distinct
    reason = lease_shape(body)
    if reason:
        raise ValueError(reason)
    return sign_payload(broker, body)


def lease_shape(obj: dict) -> str | None:
    if not is_did(obj.get("subject")) or not is_model(obj.get("model")):
        return "bad subject or model"
    if not is_int(obj.get("key_version")) or obj["key_version"] < 0:
        return "bad key_version"
    vf, exp = obj.get("valid_from"), obj.get("expires_at")
    if not is_num(vf) or not is_num(exp) or exp <= vf:
        return "bad validity window"
    if exp - vf > MAX_LEASE_TTL:
        return "lease longer than MAX_LEASE_TTL"
    if not is_hex64(obj.get("canary_hash")):
        return "bad canary_hash"
    body = {k: v for k, v in obj.items() if k not in _ENVELOPE}
    if not is_hex32(obj.get("lease_id")) or obj["lease_id"] != digest(body)[:32]:
        return "lease_id does not commit to the lease"
    return None


def check_lease(obj, trust) -> str | None:
    """Signed by a trusted broker, well-formed, for a mesh node. Says nothing
    about time or revocation; ``LeaseBook.check`` adds those."""
    reason = signed_by(obj, LEASE, "broker", trust.brokers) or lease_shape(obj)
    if reason:
        return reason
    if not trust.mesh.contains(obj["subject"]):
        return "the lease is for a node outside the mesh"
    return None


# -- the sealed grant -------------------------------------------------------------

def seal_grant(lease: dict, secret: str, canary: str, transport_public_key_b64: str) -> str:
    """Seal the secret and canary to the model node's X25519 transport key."""
    pk = PublicKey(base64.b64decode(transport_public_key_b64, validate=True))
    inner = canonical({"lease_id": lease["lease_id"], "secret": secret, "canary": canary})
    return base64.b64encode(SealedBox(pk).encrypt(inner)).decode("ascii")


def open_grant(lease: dict, sealed_b64: str, private_key: PrivateKey) -> LeaseSecret | None:
    """Open a grant with this node's transport private key. None unless it
    opens and binds to ``lease`` (lease id, and a canary matching its hash)."""
    try:
        inner = json.loads(SealedBox(private_key).decrypt(base64.b64decode(sealed_b64, validate=True)))
    except (CryptoError, ValueError, TypeError):
        return None
    if not isinstance(inner, dict) or inner.get("lease_id") != lease.get("lease_id"):
        return None
    secret, canary = inner.get("secret"), inner.get("canary")
    if not isinstance(secret, str) or not secret or not isinstance(canary, str):
        return None
    if not CANARY.fullmatch(canary) or canary_hash(canary) != lease.get("canary_hash"):
        return None
    return LeaseSecret(lease, secret, canary)


# -- revocation -------------------------------------------------------------------

def make_lease_revocation(broker, lease_ids, *, reason: str = "", clock=time.time) -> dict:
    ids = sorted(set(lease_ids))
    if not 1 <= len(ids) <= MAX_REVOKED:
        raise ValueError(f"a lease revocation names 1..{MAX_REVOKED} leases")
    body = {"type": LEASE_REVOCATION, "broker": broker.did, "lease_ids": ids, "reason": reason[:MAX_REASON],
            "issued_at": float(clock())}
    return sign_payload(broker, body)


def check_lease_revocation(obj, trust) -> str | None:
    reason = signed_by(obj, LEASE_REVOCATION, "broker", trust.brokers)
    if reason:
        return reason
    ids = obj.get("lease_ids")
    if (not isinstance(ids, list) or not 1 <= len(ids) <= MAX_REVOKED or sorted(set(ids)) != ids
            or not all(is_hex32(i) for i in ids)):
        return "bad lease_ids"
    if not isinstance(obj.get("reason"), str) or not is_num(obj.get("issued_at")):
        return "bad revocation"
    return None


class LeaseBook:
    """The verified public leases and lease revocations one participant knows
    about. Revocation is permanent (by lease id): a revoked id is remembered
    until the lease it names is dead anyway, then forgotten, so the book stays
    bounded."""

    def __init__(self, trust):
        self._trust = trust
        self._leases: dict[str, dict] = {}
        self._revoked: dict[str, float] = {}  # lease id -> when it may be forgotten

    def add(self, lease) -> bool:
        """Remember a public lease. True when it verified and was new."""
        if check_lease(lease, self._trust) is not None:
            return False
        lid = lease["lease_id"]
        if lid in self._revoked or lid in self._leases:
            return False
        self._leases[lid] = lease
        return True

    def revoke(self, obj) -> frozenset[str]:
        """Apply a broker-signed revocation; the lease ids it newly revoked."""
        if check_lease_revocation(obj, self._trust) is not None:
            return frozenset()
        newly = frozenset(obj["lease_ids"]) - self._revoked.keys()
        for lid in newly:
            known = self._leases.pop(lid, None)
            dead_by = known["expires_at"] if known is not None else obj["issued_at"] + MAX_LEASE_TTL
            self._revoked[lid] = dead_by + MAX_SKEW
        return newly

    def is_revoked(self, lease_id: str) -> bool:
        return lease_id in self._revoked

    def check(self, lease, *, provider: str, model: str, now: float, skew: float = 0.0) -> str | None:
        """Whether ``lease`` entitles ``provider`` to answer with ``model`` at
        ``now``: signed by a trusted broker, for them, unrevoked, in its window."""
        reason = check_lease(lease, self._trust)
        if reason:
            return reason
        if lease["subject"] != provider:
            return "the lease is for another node"
        if lease["model"] != model:
            return "the lease is for another model"
        if lease["lease_id"] in self._revoked:
            return "the lease is revoked"
        if not lease["valid_from"] - skew <= now < lease["expires_at"] + skew:
            return "the lease is outside its validity window"
        return None

    def eligible(self, models, now: float) -> list[tuple[str, str, dict]]:
        """``(provider, model, lease)`` for every live lease on one of ``models``,
        newest key version first, then by provider DID."""
        wanted = set(models)
        live = [lease for lease in self._leases.values()
                if lease["model"] in wanted and lease["valid_from"] <= now < lease["expires_at"]]
        live.sort(key=lambda x: (-x["key_version"], x["subject"], x["model"], -x["expires_at"]))
        out, seen = [], set()
        for lease in live:
            key = (lease["subject"], lease["model"])
            if key not in seen:
                seen.add(key)
                out.append((lease["subject"], lease["model"], lease))
        return out

    def live(self, now: float) -> list[dict]:
        return [x for x in self._leases.values() if x["valid_from"] <= now < x["expires_at"]]

    def prune(self, now: float) -> None:
        for lid in [lid for lid, x in self._leases.items() if x["expires_at"] <= now]:
            del self._leases[lid]
        for lid in [lid for lid, forget_at in self._revoked.items() if forget_at <= now]:
            del self._revoked[lid]

    def __len__(self) -> int:
        return len(self._leases)

    def revoked_count(self) -> int:
        return len(self._revoked)


# -- where secrets come from -------------------------------------------------------

class KeySource(ABC):
    """Where the broker's upstream credentials live, one per model."""

    @abstractmethod
    def current(self, model: str) -> tuple[int, str] | None:
        """``(key_version, secret)`` for ``model``, or None if it has none."""

    @abstractmethod
    def rotate(self, model: str) -> int | None:
        """Replace ``model``'s secret upstream; the new version, or None when
        this source cannot rotate (the broker then stops lending the exposed
        key until the operator installs a new one with ``set``)."""


class MemoryKeySource(KeySource):
    """Secrets held in memory -- read from the operator's config at start.
    ``mint(model) -> str`` is the hook to the upstream provider's
    key-management API that rotation calls; without it, rotation is refused."""

    def __init__(self, keys: dict[str, str], *, mint=None):
        self._keys: dict[str, tuple[int, str]] = {m: (1, s) for m, s in keys.items()}
        self._mint = mint

    def current(self, model: str) -> tuple[int, str] | None:
        return self._keys.get(model)

    def set(self, model: str, secret: str) -> int:
        """Install a new secret by hand; returns its version."""
        version = self._keys.get(model, (0, ""))[0] + 1
        self._keys[model] = (version, secret)
        return version

    def rotate(self, model: str) -> int | None:
        if self._mint is None or model not in self._keys:
            return None
        return self.set(model, self._mint(model))


__all__ = [
    "LEASE",
    "LEASE_REVOCATION",
    "CANARY",
    "CANARY_PREFIX",
    "DEFAULT_TTL",
    "MAX_LEASE_TTL",
    "LeaseSecret",
    "LeaseBook",
    "KeySource",
    "MemoryKeySource",
    "new_canary",
    "canary_hash",
    "make_lease",
    "check_lease",
    "lease_shape",
    "seal_grant",
    "open_grant",
    "make_lease_revocation",
    "check_lease_revocation",
]
