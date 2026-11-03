"""Operator admission: how a node that is not on any allowlist file -- a browser
node, above all -- joins the mesh, explicitly and revocably.

A browser cannot be put on every node's allowlist file by hand. Instead an
operator signs an **admission**:

    {"type": "secdogie/inference/admission/v1", "operator": DID, "subject": DID,
     "roles": ["forward"], "issued_at": t, "expires_at": t + ttl, "signer", "sig"}

  * only an operator on ``trust.operators`` can issue one;
  * the only admissible role is ``forward``: an admitted node carries frames
    and decides nothing -- it never serves (decides routes) and never answers as
    a model, so admission cannot hand anyone the decision layer or a lease;
  * it expires (at most ``MAX_ADMISSION_TTL`` after issue) and is never renewed
    implicitly: the operator signs a new one;
  * Master k-of-n revocation of the subject DID still applies on top
    (``RevocationFilter``), so a revoked browser is out at once.

``AdmissionBook`` holds the verified, unexpired admissions this participant has
seen -- at most ``capacity``, newest per subject -- and implements
``contains`` / ``dids``, so it slots into the mesh trust set next to the
allowlist file: ``AnyOf(mesh_allowlist, RevocationFilter(book, policy))``.
Admissions are flooded like other signed objects, so the whole mesh admits a
subject once any node publishes the admission (the WebRTC carrier does when a
browser presents it in its handshake).
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict

from secdogie_identity import sign_payload

from .protocol import MAX_SKEW, ROLE_FORWARD, is_did, is_num, signed_by

ADMISSION = "secdogie/inference/admission/v1"
ADMISSIBLE_ROLES = frozenset({ROLE_FORWARD})
MAX_ADMISSION_TTL = 30 * 86400.0
DEFAULT_ADMISSION_TTL = 86400.0


def make_admission(operator, subject: str, *, ttl: float = DEFAULT_ADMISSION_TTL, roles=(ROLE_FORWARD,),
                   clock=time.time) -> dict:
    now = float(clock())
    body = {"type": ADMISSION, "operator": operator.did, "subject": subject, "roles": sorted(set(roles)),
            "issued_at": now, "expires_at": now + float(ttl)}
    reason = admission_shape(body)
    if reason:
        raise ValueError(reason)
    return sign_payload(operator, body)


def admission_shape(obj: dict) -> str | None:
    if not is_did(obj.get("subject")) or obj.get("subject") == obj.get("operator"):
        return "bad subject"
    roles = obj.get("roles")
    if not isinstance(roles, list) or not roles or sorted(set(roles)) != roles or not set(roles) <= ADMISSIBLE_ROLES:
        return "an admission grants the forward role only"
    issued, expires = obj.get("issued_at"), obj.get("expires_at")
    if not is_num(issued) or not is_num(expires) or not issued < expires:
        return "bad validity window"
    if expires - issued > MAX_ADMISSION_TTL:
        return "admission longer than MAX_ADMISSION_TTL"
    return None


def check_admission(obj, trust, *, now: float) -> str | None:
    """None when ``obj`` is a valid, current admission by a trusted operator."""
    reason = signed_by(obj, ADMISSION, "operator", trust.operators) or admission_shape(obj)
    if reason:
        return reason
    if obj["issued_at"] > now + MAX_SKEW:
        return "issued in the future"
    if obj["expires_at"] <= now:
        return "expired"
    return None


class AdmissionBook:
    """The verified, unexpired admissions one participant holds."""

    def __init__(self, trust=None, *, capacity: int = 1024, clock=time.time):
        self.trust = trust                  # set by the owner once TrustRoots exists
        self.capacity = int(capacity)
        self._clock = clock
        self._by_subject: OrderedDict[str, dict] = OrderedDict()
        self._lock = threading.Lock()

    def add(self, obj, *, now: float | None = None) -> bool:
        """Verify and keep ``obj``; True when it admitted something new (and so
        should travel on)."""
        if self.trust is None:
            return False
        now = float(self._clock()) if now is None else now
        if check_admission(obj, self.trust, now=now) is not None:
            return False
        subject = obj["subject"]
        with self._lock:
            held = self._by_subject.get(subject)
            if held is not None and held["expires_at"] >= obj["expires_at"]:
                return False
            self._by_subject[subject] = obj
            self._by_subject.move_to_end(subject)
            while len(self._by_subject) > self.capacity:
                self._by_subject.popitem(last=False)
        return True

    def contains(self, did: str) -> bool:
        with self._lock:
            held = self._by_subject.get(did)
        return held is not None and held["expires_at"] > float(self._clock())

    def dids(self) -> set[str]:
        now = float(self._clock())
        with self._lock:
            return {d for d, a in self._by_subject.items() if a["expires_at"] > now}

    def signed(self) -> list[dict]:
        now = float(self._clock())
        with self._lock:
            return [a for a in self._by_subject.values() if a["expires_at"] > now]

    def prune(self, now: float) -> None:
        with self._lock:
            for d in [d for d, a in self._by_subject.items() if a["expires_at"] <= now]:
                del self._by_subject[d]

    def __len__(self) -> int:
        with self._lock:
            return len(self._by_subject)


class RevocationFilter:
    """``contains`` of ``inner`` minus the DIDs a revocation policy revoked."""

    def __init__(self, inner, policy):
        self._inner = inner
        self._policy = policy

    def contains(self, did: str) -> bool:
        return self._inner.contains(did) and not self._policy.is_revoked(did)

    def dids(self) -> set[str]:
        return {d for d in self._inner.dids() if not self._policy.is_revoked(d)}


__all__ = [
    "ADMISSION",
    "ADMISSIBLE_ROLES",
    "MAX_ADMISSION_TTL",
    "DEFAULT_ADMISSION_TTL",
    "make_admission",
    "admission_shape",
    "check_admission",
    "AdmissionBook",
    "RevocationFilter",
]
