"""Wire objects of the inference mesh -- the "new arc".

One protocol covers the three operating states (see PROTOCOL.md):

  1. normal   -- a user's request enters a serving node (the "P2P+S" of the
     diagram), whose decision layer routes it over the P2P mesh to one or more
     model nodes ("M/API") that hold a key lease from the key authority.
  2. degraded -- failed and misbehaving nodes are routed around; when the mesh
     has no path left, the decider routes through the central coordinator.
  3. lockdown -- verified mutiny evidence takes the decision layer and the
     coordinator off the data path; a fresh forwarder carries requests the user
     pinned to two independent model nodes, and only agreeing answers pass.

Every object that may travel more than one hop, or be held against its author
later, is a DID-signed, domain-separated JSON payload (secdogie-identity's
``sign_payload`` / ``verify_payload``). The frames between neighbours are plain
JSON containers for those objects: the transport underneath already
authenticates the neighbour (``ChannelMux`` hands over the verified sender DID),
and nothing in a container is believed beyond what its signed contents prove.

No new crypto, no obfuscation. Pure and deterministic given a clock.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import time
from dataclasses import dataclass

from secdogie_identity import GRANTABLE_SCOPES, pubkey_from_did, require_trust, sign_payload, verify_payload
from secdogie_identity.signing import canonical

REQUEST = "secdogie/inference/request/v1"
DECISION = "secdogie/inference/decision/v1"
RESPONSE = "secdogie/inference/response/v1"
HEARTBEAT = "secdogie/inference/heartbeat/v1"
ADVISORY = "secdogie/inference/advisory/v1"
FAULT = "secdogie/inference/fault/v1"

CHANNEL = "inference/v1"  # the ChannelMux channel the frames ride

ROLE_SERVE = "serve"              # P2P+S: takes user requests and decides routes (the decision layer)
ROLE_FORWARD = "forward"          # P2P: carries frames and decides nothing; the standby pool ("new P2P")
ROLE_MODEL = "model"              # M/API: answers with a leased upstream key
ROLE_COORDINATOR = "coordinator"  # the central server: advisories, repairs, the fallback path
ROLE_BROKER = "broker"            # the API key authority
ROLES = frozenset({ROLE_SERVE, ROLE_FORWARD, ROLE_MODEL, ROLE_COORDINATOR, ROLE_BROKER})
TRANSIT_ROLES = frozenset({ROLE_SERVE, ROLE_FORWARD})  # who may sit inside a mesh path

MODE_NORMAL = "normal"
MODE_DEGRADED = "degraded"
MODE_LOCKDOWN = "lockdown"
MODES = frozenset({MODE_NORMAL, MODE_DEGRADED, MODE_LOCKDOWN})

VIA_MESH = "mesh"
VIA_CENTRAL = "central"

STATUS_OK = "ok"
STATUS_REFUSED = "refused"

FAULT_REASONS = frozenset({"timeout", "invalid", "divergent"})

# One frame is one datagram: v1 has no chunking, so text is bounded.
MAX_INPUT = 8000
MAX_OUTPUT = 8000
MAX_REASON = 200
MAX_MODELS = 4
MAX_QUORUM = 3
LOCKDOWN_QUORUM = 2
MAX_HOPS = 8          # nodes on one leg path, decider and provider included
MAX_ATTEMPTS = 3
MAX_TTL = 300.0
MAX_SKEW = 120.0
MAX_NEIGHBORS = 32
MAX_LEASES = 4        # leases a model node advertises in one heartbeat
MAX_LISTED = 64       # DIDs or pairs in one advisory list

_MODEL = re.compile(r"^[A-Za-z0-9._:/@+-]{1,128}$")
_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class AnyOf:
    """``contains`` over several trust sets: trusted by any one of them."""

    def __init__(self, *sets):
        self._sets = sets

    def contains(self, did: str) -> bool:
        return any(s.contains(did) for s in self._sets)


@dataclass(frozen=True)
class TrustRoots:
    """Whom each decision listens to. Every field is allowlist-shaped
    (``Allowlist``; a ``TrustPolicy``, so Master-signed revocation applies; or
    ``ALLOW_ANY`` for tests). ``None`` is refused, as everywhere in secdogie.

      * ``mesh``         -- every infrastructure node: serving, forwarding and
                            model nodes, the coordinator, the broker;
      * ``users``        -- who may submit requests;
      * ``deciders``     -- who may sign route decisions outside lockdown;
      * ``coordinators`` -- who may sign advisories and carry central legs;
      * ``brokers``      -- who may sign key leases and lease revocations;
      * ``operators``    -- who may sign breaker resets, the only way out of
                            lockdown.
    """

    mesh: object
    users: object
    deciders: object
    coordinators: object
    brokers: object
    operators: object

    def __post_init__(self):
        for name in ("mesh", "users", "deciders", "coordinators", "brokers", "operators"):
            require_trust(getattr(self, name), f"TrustRoots.{name}")

    @property
    def peers(self) -> AnyOf:
        """Who may hand this node a frame at all."""
        return AnyOf(self.mesh, self.users)


# -- helpers ------------------------------------------------------------------

def digest(obj) -> str:
    """SHA-256 over the canonical JSON of ``obj``: how signed objects refer to
    each other (a response names its request and decision by digest)."""
    return hashlib.sha256(canonical(obj)).hexdigest()


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_num(v) -> bool:
    # json.loads accepts NaN / Infinity, which would slip past every comparison.
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def is_did(v) -> bool:
    if not isinstance(v, str):
        return False
    try:
        pubkey_from_did(v)
    except ValueError:
        return False
    return True


def is_model(v) -> bool:
    return isinstance(v, str) and bool(_MODEL.match(v))


def is_hex64(v) -> bool:
    return isinstance(v, str) and bool(_HEX64.match(v))


def is_hex32(v) -> bool:
    return isinstance(v, str) and bool(_HEX32.match(v))


def _did_list(v, *, limit: int) -> bool:
    return (isinstance(v, list) and len(v) <= limit and all(is_did(d) for d in v)
            and sorted(set(v)) == v)


def _pair_list(v, *, limit: int) -> bool:
    return (isinstance(v, list) and len(v) <= limit
            and all(isinstance(p, list) and len(p) == 2 and is_did(p[0]) and is_did(p[1]) and p[0] != p[1]
                    for p in v))


def signed_by(obj, type_: str, field: str, trust) -> str | None:
    """None when ``obj`` is a ``type_`` object validly signed by a DID on
    ``trust`` that equals ``obj[field]``; otherwise why not."""
    if not isinstance(obj, dict) or obj.get("type") != type_:
        return "wrong or missing type"
    ok, signer = verify_payload(obj, trust)
    if not ok:
        return "signer not trusted" if signer else "invalid or missing signature"
    if obj.get(field) != signer:
        return f"{field} is not the signer"
    return None


# -- request: signed by the user -----------------------------------------------

def make_request(identity, models, text: str, *, scopes=(), quorum: int = 1, pins=(), ttl: float = 60.0,
                 clock=time.time) -> dict:
    """A user's signed request. ``models`` are the models whose answer the user
    accepts; ``scopes`` the capability scopes an answer may act within (empty:
    the answer is text, nothing acts on it); ``quorum`` how many distinct model
    nodes must agree; ``pins`` -- ``(provider DID, model)`` pairs -- fix the
    providers, as a user does in lockdown."""
    now = float(clock())
    body = {
        "type": REQUEST,
        "req_id": secrets.token_hex(16),
        "user": identity.did,
        "models": sorted(set(models)),
        "input": text,
        "scopes": sorted(set(scopes)),
        "quorum": quorum,
        "pins": [[p, m] for p, m in pins],
        "issued_at": now,
        "expires_at": now + float(ttl),
    }
    reason = request_shape(body)
    if reason:
        raise ValueError(reason)
    return sign_payload(identity, body)


def request_shape(obj: dict) -> str | None:
    if not is_hex32(obj.get("req_id")):
        return "bad req_id"
    models = obj.get("models")
    if not isinstance(models, list) or not 1 <= len(models) <= MAX_MODELS:
        return f"a request names 1..{MAX_MODELS} models"
    if not all(is_model(m) for m in models) or sorted(set(models)) != models:
        return "bad model list"
    if not isinstance(obj.get("input"), str) or len(obj["input"]) > MAX_INPUT:
        return "input missing or too long"
    scopes = obj.get("scopes")
    if (not isinstance(scopes, list) or any(not isinstance(s, str) for s in scopes)
            or sorted(set(scopes)) != scopes):
        return "bad scopes"
    if any(s not in GRANTABLE_SCOPES for s in scopes):
        return "a scope is not grantable"
    quorum = obj.get("quorum")
    if not is_int(quorum) or not 1 <= quorum <= MAX_QUORUM:
        return f"quorum must be 1..{MAX_QUORUM}"
    pins = obj.get("pins")
    if not isinstance(pins, list) or len(pins) > MAX_QUORUM:
        return "bad pins"
    pinned = set()
    for pin in pins:
        if not (isinstance(pin, list) and len(pin) == 2 and is_did(pin[0]) and pin[1] in models):
            return "a pin must be [provider DID, one of the request's models]"
        if pin[0] in pinned:
            return "a provider is pinned twice"
        pinned.add(pin[0])
    if pins and len(pins) < quorum:
        return "fewer pins than the quorum"
    iss, exp = obj.get("issued_at"), obj.get("expires_at")
    if not is_num(iss) or not is_num(exp) or not 0 < exp - iss <= MAX_TTL:
        return "bad validity window"
    return None


def request_evidence_ok(obj, trust) -> bool:
    """Signature and shape, without the clock: a request kept as evidence is
    judged on what it says, not on whether it has since expired."""
    return signed_by(obj, REQUEST, "user", trust.users) is None and request_shape(obj) is None


def check_request(obj, trust, *, now: float) -> str | None:
    reason = signed_by(obj, REQUEST, "user", trust.users) or request_shape(obj)
    if reason:
        return reason
    if obj["issued_at"] > now + MAX_SKEW:
        return "issued in the future"
    if now >= obj["expires_at"]:
        return "expired"
    return None


# -- decision: signed by whoever chose the route ------------------------------

def make_decision(identity, request: dict, legs, *, mode: str, epoch: int, attempt: int = 0,
                  clock=time.time) -> dict:
    """A signed route decision. ``legs`` are ``router.Leg`` (or their wire
    dicts): which provider answers with which model, along which path."""
    body = {
        "type": DECISION,
        "request_hash": digest(request),
        "decider": identity.did,
        "mode": mode,
        "epoch": int(epoch),
        "attempt": int(attempt),
        "legs": [leg if isinstance(leg, dict) else leg.to_wire() for leg in legs],
        "issued_at": float(clock()),
    }
    return sign_payload(identity, body)


def decision_shape(obj: dict, trust) -> str | None:
    """The structural rules every decision obeys. A decision that breaks one is
    rejected; the rules a decider could break *on purpose* -- against the
    user's request -- are ``decision_violations``."""
    if obj.get("mode") not in MODES:
        return "unknown mode"
    if not is_hex64(obj.get("request_hash")):
        return "bad request_hash"
    if not is_int(obj.get("epoch")) or obj["epoch"] < 0:
        return "bad epoch"
    if not is_int(obj.get("attempt")) or not 0 <= obj["attempt"] < MAX_ATTEMPTS:
        return "bad attempt"
    if not is_num(obj.get("issued_at")):
        return "bad issued_at"
    legs = obj.get("legs")
    if not isinstance(legs, list) or not 1 <= len(legs) <= MAX_QUORUM:
        return f"a decision has 1..{MAX_QUORUM} legs"
    providers = set()
    for leg in legs:
        if not isinstance(leg, dict) or not is_did(leg.get("provider")) or not is_model(leg.get("model")):
            return "bad leg"
        path, via = leg.get("path"), leg.get("via")
        if not (isinstance(path, list) and 2 <= len(path) <= MAX_HOPS and all(is_did(d) for d in path)):
            return "bad leg path"
        if len(set(path)) != len(path):
            return "a leg path visits a node twice"
        if path[0] != obj.get("decider") or path[-1] != leg["provider"]:
            return "a leg path must run from the decider to its provider"
        if via == VIA_CENTRAL:
            if len(path) != 3 or not trust.coordinators.contains(path[1]):
                return "a central leg is decider -> coordinator -> provider"
        elif via == VIA_MESH:
            if any(trust.coordinators.contains(d) for d in path[1:-1]):
                return "a mesh leg does not pass through the coordinator"
        else:
            return "unknown via"
        if leg["provider"] in providers:
            return "a provider appears on two legs"
        providers.add(leg["provider"])
    return None


def check_decision(obj, trust, *, request: dict | None = None) -> str | None:
    """Signature, signer role and shape; with ``request``, also that the
    decision is for it. Outside lockdown only a decider may sign one; in
    lockdown the decision layer is suspended and the standby forwarder that
    carries the user's pins signs it instead."""
    mode = obj.get("mode") if isinstance(obj, dict) else None
    signers = trust.mesh if mode == MODE_LOCKDOWN else trust.deciders
    reason = signed_by(obj, DECISION, "decider", signers) or decision_shape(obj, trust)
    if reason:
        return reason
    if request is not None and obj["request_hash"] != digest(request):
        return "decision is for a different request"
    return None


def decision_violations(decision: dict, request: dict) -> list[str]:
    """What a (well-formed) decision does against the user's signed request.
    Each is something a decider signs knowingly, so each is evidence."""
    out = []
    legs = decision["legs"]
    need = request["quorum"]
    if decision["mode"] == MODE_LOCKDOWN:
        need = max(need, LOCKDOWN_QUORUM)
    if len(legs) < need:
        out.append("quorum-downgrade")
    if any(leg["model"] not in request["models"] for leg in legs):
        out.append("model-substitution")
    pins = {(p, m) for p, m in request["pins"]}
    if pins and {(leg["provider"], leg["model"]) for leg in legs} != pins:
        out.append("pin-violation")
    if decision["mode"] == MODE_LOCKDOWN and any(leg["via"] != VIA_MESH or len(leg["path"]) != 2
                                                for leg in legs):
        out.append("lockdown-shape")
    return out


def routed_through(decision: dict, dids) -> list[str]:
    """The DIDs from ``dids`` that a decision's legs pass through (the decider
    itself excluded)."""
    banned = set(dids)
    hit = {d for leg in decision["legs"] for d in leg["path"][1:] if d in banned}
    return sorted(hit)


# -- response: signed by the model node ----------------------------------------

def make_response(identity, request: dict, decision: dict, model: str, lease: dict, *, output: str = "",
                  status: str = STATUS_OK, reason: str = "", clock=time.time) -> dict:
    body = {
        "type": RESPONSE,
        "request_hash": digest(request),
        "decision_hash": digest(decision),
        "provider": identity.did,
        "model": model,
        "lease": lease,
        "status": status,
        "output": output if status == STATUS_OK else "",
        "reason": reason[:MAX_REASON],
        "issued_at": float(clock()),
    }
    shape = response_shape(body)
    if shape:
        raise ValueError(shape)
    return sign_payload(identity, body)


def response_shape(obj: dict) -> str | None:
    if not is_hex64(obj.get("request_hash")) or not is_hex64(obj.get("decision_hash")):
        return "bad hashes"
    if not is_model(obj.get("model")) or not isinstance(obj.get("lease"), dict):
        return "bad model or lease"
    if obj.get("status") not in (STATUS_OK, STATUS_REFUSED):
        return "bad status"
    output, reason = obj.get("output"), obj.get("reason")
    if not isinstance(output, str) or len(output) > MAX_OUTPUT:
        return "output missing or too long"
    if obj["status"] == STATUS_REFUSED and output:
        return "a refusal carries no output"
    if not isinstance(reason, str) or len(reason) > MAX_REASON:
        return "bad reason"
    if not is_num(obj.get("issued_at")):
        return "bad issued_at"
    return None


def check_response(obj, trust, *, request: dict | None = None, decision: dict | None = None) -> str | None:
    reason = signed_by(obj, RESPONSE, "provider", trust.mesh) or response_shape(obj)
    if reason:
        return reason
    if request is not None and obj["request_hash"] != digest(request):
        return "response is for a different request"
    if decision is not None:
        if obj["decision_hash"] != digest(decision) or obj["request_hash"] != decision["request_hash"]:
            return "response is for a different decision"
        if not any(leg["provider"] == obj["provider"] and leg["model"] == obj["model"]
                   for leg in decision["legs"]):
            return "the decision gave this provider no leg for this model"
    return None


# -- heartbeat: a node's signed link-state advertisement ------------------------

def make_heartbeat(identity, *, roles, seq: int, neighbors, load: float = 0.0, leases=(),
                   clock=time.time) -> dict:
    body = {
        "type": HEARTBEAT,
        "node": identity.did,
        "roles": sorted(set(roles)),
        "seq": int(seq),
        "neighbors": sorted(set(neighbors) - {identity.did})[:MAX_NEIGHBORS],
        "load": min(max(float(load), 0.0), 1.0),
        "leases": list(leases)[:MAX_LEASES],
        "issued_at": float(clock()),
    }
    return sign_payload(identity, body)


def check_heartbeat(obj, trust, *, now: float | None = None) -> str | None:
    reason = signed_by(obj, HEARTBEAT, "node", trust.mesh)
    if reason:
        return reason
    roles = obj.get("roles")
    if not isinstance(roles, list) or sorted(set(roles)) != roles or not set(roles) <= ROLES:
        return "bad roles"
    if not is_int(obj.get("seq")) or obj["seq"] < 0:
        return "bad seq"
    if not _did_list(obj.get("neighbors"), limit=MAX_NEIGHBORS) or obj["node"] in obj["neighbors"]:
        return "bad neighbours"
    if not is_num(obj.get("load")) or not 0.0 <= obj["load"] <= 1.0:
        return "bad load"
    leases = obj.get("leases")
    if not isinstance(leases, list) or len(leases) > MAX_LEASES or any(not isinstance(x, dict) for x in leases):
        return "bad leases"
    if not is_num(obj.get("issued_at")):
        return "bad issued_at"
    if now is not None and obj["issued_at"] > now + MAX_SKEW:
        return "issued in the future"
    return None


# -- advisory: the coordinator's signed view of failures and repairs ----------

def make_advisory(identity, *, epoch: int, failed=(), quarantined=(), bypass=(), recruit=(),
                  clock=time.time) -> dict:
    body = {
        "type": ADVISORY,
        "coordinator": identity.did,
        "epoch": int(epoch),
        "failed": sorted(set(failed))[:MAX_LISTED],
        "quarantined": sorted(set(quarantined))[:MAX_LISTED],
        "bypass": [list(p) for p in bypass][:MAX_LISTED],
        "recruit": [list(p) for p in recruit][:MAX_LISTED],
        "issued_at": float(clock()),
    }
    return sign_payload(identity, body)


def check_advisory(obj, trust, *, now: float | None = None) -> str | None:
    reason = signed_by(obj, ADVISORY, "coordinator", trust.coordinators)
    if reason:
        return reason
    if not is_int(obj.get("epoch")) or obj["epoch"] < 0:
        return "bad epoch"
    for key in ("failed", "quarantined"):
        if not _did_list(obj.get(key), limit=MAX_LISTED):
            return f"bad {key} list"
    for key in ("bypass", "recruit"):
        if not _pair_list(obj.get(key), limit=MAX_LISTED):
            return f"bad {key} list"
    if not is_num(obj.get("issued_at")):
        return "bad issued_at"
    if now is not None and obj["issued_at"] > now + MAX_SKEW:
        return "issued in the future"
    return None


# -- fault report: a node's claim against another, for the coordinator ----------

def make_fault(identity, accused: str, reason: str, *, request_hash: str = "", clock=time.time) -> dict:
    """A claim, not proof: the coordinator acts on one only when enough
    distinct reporters make it (``health.FaultTracker``)."""
    if reason not in FAULT_REASONS:
        raise ValueError(f"unknown fault reason {reason!r}")
    body = {"type": FAULT, "reporter": identity.did, "accused": accused, "reason": reason,
            "request_hash": request_hash, "issued_at": float(clock())}
    return sign_payload(identity, body)


def check_fault(obj, trust, *, now: float) -> str | None:
    reason = signed_by(obj, FAULT, "reporter", trust.mesh)
    if reason:
        return reason
    if not is_did(obj.get("accused")) or obj["accused"] == obj["reporter"]:
        return "bad accused"
    if obj.get("reason") not in FAULT_REASONS:
        return "unknown reason"
    if obj.get("request_hash") != "" and not is_hex64(obj.get("request_hash")):
        return "bad request_hash"
    if not is_num(obj.get("issued_at")) or abs(obj["issued_at"] - now) > MAX_SKEW:
        return "stale or future report"
    return None


# -- frames between neighbours ------------------------------------------------

FRAME_KINDS = frozenset({
    "submit",   # user -> entry:            {request}
    "req",      # toward the provider:      {request, decision, leg, hop}
    "resp",     # back toward the decider:  {decision, response, leg, hop}
    "nack",     # back toward the decider:  {decision, leg, hop, at, unreachable, reason}
    "result",   # decider -> user:          {request_hash, items: [[decision, response], ...]}
    "refused",  # entry -> user:            {request_hash, reason}
    "flood",    # to every neighbour:       {obj} -- heartbeat, advisory, incident, reset, lease revocation
    "fault",    # node -> coordinator:      {report}
    "grant",    # broker -> model node:     {lease, sealed}
    "dir?",     # anyone -> a node:         {}
    "dir",      # the answer:               {leases, incidents, resets}
})


def frame(kind: str, **fields) -> bytes:
    if kind not in FRAME_KINDS:
        raise ValueError(f"unknown frame kind {kind!r}")
    return json.dumps({"k": kind, **fields}, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def parse_frame(payload: bytes) -> dict | None:
    try:
        obj = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return None
    if not isinstance(obj, dict) or obj.get("k") not in FRAME_KINDS:
        return None
    return obj
