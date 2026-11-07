"""Policy bundles: signed, declarative, tighten-only configuration for a mesh.

A bundle is how an operator "injects" Socratic gate rules, a decision-model
configuration, ingestion limits or an allowlist change into the P2P mesh. It is
**data, never code**: every node interprets it with its own fixed code, so the
mesh can never become a channel for running someone else's program.

Wire form (canonical JSON, like every signed secdogie object)::

    {
      "type": "secdogie/policy-bundle/v1",
      "kind": "gate-rules" | "decision-model" | "ingest" | "allowlist",
      "mesh": "home-lab",
      "version": 2,                       # 1, 2, 3 ... per (kind, mesh, scope)
      "supersedes": "<bundle_id of v1>",  # "" exactly when version == 1
      "scope": {"roles": [...], "nodes": [...]},   # both empty: the whole mesh
      "not_before": 1767225600,           # integer seconds
      "body": {...},                      # closed schema per kind (schema.py)
      "bundle_id": "<sha256 hex of the canonical bundle without bundle_id/sigs>",
      "sigs": [{"signer": "did:key:...", "sig": "<base64 Ed25519>"}, ...]
    }

Every signature covers the same bytes: the canonical bundle without
``bundle_id`` and ``sigs``. ``bundle_id`` commits to those bytes, so it is the
bundle's name in a version chain and in heartbeats.

Who may sign is fixed per kind (docs/PLAN-STUDIO-INGEST.zh.md, decision 4):

  * ``gate-rules`` and ``ingest``: one signature from an operator DID;
  * ``decision-model`` and ``allowlist``: ``threshold`` distinct signatures
    from the mesh's ``MasterSet`` (the same k-of-n set that signs revocations),
    because a bad one affects every node.

Numbers are integers only: no floats anywhere in a bundle, so Python and the
TypeScript runtime cannot disagree about how one is encoded.
"""
from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass

from secdogie_identity import ALLOW_ANY, MasterSet, PublicIdentity
from secdogie_identity.signing import canonical

from . import schema
from .schema import (
    KIND_ALLOWLIST,
    KIND_DECISION_MODEL,
    KIND_GATE_RULES,
    KIND_INGEST,
    KINDS,
    Errors,
)

BUNDLE_TYPE = "secdogie/policy-bundle/v1"
OPERATOR_KINDS = frozenset({KIND_GATE_RULES, KIND_INGEST})
MASTER_KINDS = frozenset({KIND_DECISION_MODEL, KIND_ALLOWLIST})
MAX_SIGS = 16
MAX_VERSION = 2**31 - 1
MAX_NOT_BEFORE = 4_102_444_800  # 2100-01-01

_FIELDS = ("body", "bundle_id", "kind", "mesh", "not_before", "scope", "sigs", "supersedes", "type",
           "version")
_UNSIGNED_EXCLUDE = ("bundle_id", "sigs")


def unsigned(obj: dict) -> dict:
    """The bytes every signature covers, as a dict: the bundle without
    ``bundle_id`` and ``sigs``."""
    return {k: v for k, v in obj.items() if k not in _UNSIGNED_EXCLUDE}


def bundle_id(obj: dict) -> str:
    return hashlib.sha256(canonical(unsigned(obj))).hexdigest()


def check_bundle(obj) -> list[str]:
    """Every structural and schema error in ``obj``, sorted. Empty means the
    bundle is well formed and tightens only; signatures are not looked at."""
    errs = Errors()
    if not isinstance(obj, dict):
        errs.add("type", "$")
        return errs.sorted()
    schema.fields(obj, "", _FIELDS, _FIELDS, errs)
    if "type" in obj and obj["type"] != BUNDLE_TYPE:
        errs.add("type" if not isinstance(obj["type"], str) else "value", "type")
    kind = obj.get("kind")
    kind_ok = "kind" in obj and schema.enum(kind, "kind", KINDS, errs)
    if "mesh" in obj:
        schema.mesh_name(obj["mesh"], "mesh", errs)
    version_ok = "version" in obj and schema.int_in(obj["version"], "version", 1, MAX_VERSION, errs)
    supersedes_ok = False
    if "supersedes" in obj:
        s = obj["supersedes"]
        if not isinstance(s, str):
            errs.add("type", "supersedes")
        elif s != "":
            supersedes_ok = schema.hex64(s, "supersedes", errs)
        else:
            supersedes_ok = True
    if version_ok and supersedes_ok and (obj["version"] == 1) != (obj["supersedes"] == ""):
        errs.add("conflict", "supersedes")
    if "scope" in obj:
        schema.check_scope(obj["scope"], "scope", errs)
    if "not_before" in obj:
        schema.int_in(obj["not_before"], "not_before", 0, MAX_NOT_BEFORE, errs)
    if "body" in obj and kind_ok:
        schema.check_body(kind, obj["body"], errs)
    if "bundle_id" in obj:
        if not isinstance(obj["bundle_id"], str):
            errs.add("type", "bundle_id")
        elif obj["bundle_id"] != bundle_id(obj):
            errs.add("bundle-id", "bundle_id")
    if "sigs" in obj:
        if not isinstance(obj["sigs"], list):
            errs.add("type", "sigs")
        elif len(obj["sigs"]) > MAX_SIGS:
            errs.add("range", "sigs")
    return errs.sorted()


def _good_signers(obj: dict, trusted) -> list[str]:
    message = canonical(unsigned(obj))
    good: list[str] = []
    for entry in obj.get("sigs", []):
        if not isinstance(entry, dict):
            continue
        signer, sig_b64 = entry.get("signer"), entry.get("sig")
        if not isinstance(signer, str) or not isinstance(sig_b64, str):
            continue
        if signer in good or not trusted(signer):
            continue
        try:
            sig = base64.b64decode(sig_b64, validate=True)
            ok = PublicIdentity.from_did(signer).verify(message, sig)
        except (ValueError, TypeError):
            ok = False
        if ok:
            good.append(signer)
    return sorted(good)


@dataclass(frozen=True)
class Verdict:
    ok: bool
    errors: tuple[str, ...]
    signers: tuple[str, ...]  # the valid, authorized signers that counted

    def to_json(self) -> dict:
        return {"ok": self.ok, "errors": list(self.errors), "signers": list(self.signers)}


def verify_bundle(obj, *, operators=None, masters: MasterSet | None = None) -> Verdict:
    """Accept a bundle only if it is well formed, tightens only, and is signed
    by whom its kind requires. ``operators`` (an Allowlist / TrustPolicy, any
    ``.contains``) authorizes operator kinds; ``masters`` (a ``MasterSet``) the
    master kinds. A missing trust set authorizes nothing -- fail closed.
    ``ALLOW_ANY`` is refused outright: "a gate rule from anyone" is never meant."""
    if operators is ALLOW_ANY:
        raise ValueError("policy bundles never accept ALLOW_ANY operators")
    errors = check_bundle(obj)
    if errors:
        return Verdict(False, tuple(errors), ())
    kind = obj["kind"]
    if kind in OPERATOR_KINDS:
        signers = _good_signers(obj, operators.contains) if operators is not None else []
        if not signers:
            return Verdict(False, ("unauthorized:sigs",), ())
        return Verdict(True, (), tuple(signers))
    if masters is None:
        return Verdict(False, ("unauthorized:sigs",), ())
    signers = _good_signers(obj, masters.contains)
    if len(signers) < masters.threshold:
        return Verdict(False, ("threshold:sigs",), tuple(signers))
    return Verdict(True, (), tuple(signers))


def check_successor(current: dict | None, candidate: dict) -> list[str]:
    """Errors in ``candidate`` as the next bundle after ``current`` (None: there
    is none yet). Both are assumed individually verified. The chain is linear:
    the same kind, mesh and scope, the next version, naming its predecessor.
    Rolling back is publishing the old content as the next version."""
    errs = Errors()
    if current is None:
        if candidate["version"] != 1:
            errs.add("chain", "version")
        return errs.sorted()
    for k in ("kind", "mesh", "scope"):
        if canonical({"v": candidate[k]}) != canonical({"v": current[k]}):
            errs.add("chain", k)
    if candidate["version"] != current["version"] + 1:
        errs.add("chain", "version")
    if candidate["supersedes"] != current["bundle_id"]:
        errs.add("chain", "supersedes")
    return errs.sorted()


def assemble(kind: str, *, mesh: str, version: int, body: dict, scope: dict | None = None,
             supersedes: str = "", not_before: int = 0) -> dict:
    """An unsigned bundle, not validated (see `create_bundle`)."""
    b = {
        "type": BUNDLE_TYPE,
        "kind": kind,
        "mesh": mesh,
        "version": version,
        "supersedes": supersedes,
        "scope": scope if scope is not None else {"nodes": [], "roles": []},
        "not_before": not_before,
        "body": body,
    }
    return {**b, "bundle_id": bundle_id(b), "sigs": []}


def create_bundle(kind: str, **kw) -> dict:
    """An unsigned, validated bundle; raises ValueError listing every error."""
    b = assemble(kind, **kw)
    errors = check_bundle(b)
    if errors:
        raise ValueError("invalid policy bundle: " + ", ".join(errors))
    return b


def sign_bundle(identity, obj: dict) -> dict:
    """A copy of ``obj`` with ``identity``'s signature added (idempotent). The
    ``bundle_id`` is recomputed, so it always names what was signed."""
    body = unsigned(obj)
    sigs = [dict(s) for s in obj.get("sigs", []) if isinstance(s, dict)]
    if not any(s.get("signer") == identity.did for s in sigs):
        sig = base64.b64encode(identity.sign(canonical(body))).decode("ascii")
        sigs.append({"signer": identity.did, "sig": sig})
    return {**body, "bundle_id": bundle_id(body), "sigs": sigs}
