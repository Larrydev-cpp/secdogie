"""The key authority -- "API key" in the diagram.

The broker holds the upstream credentials (``KeySource``) and lends them, one
short lease at a time, to the model nodes the operator assigned to each model.
It learns each node's X25519 transport key only from that node's signed DID
binding, and seals every grant to it (``lease.seal_grant``). In all three
states of the diagram it stays linked to the model nodes and the central
server; it never carries user traffic.

It renews leases before they lapse and reacts to mutiny on its own:

  * a latched **node** loses every lease it holds, and each model it held a key
    for is **rotated** -- the node had the secret, so the secret is assumed
    gone; every older-version lease of that model is revoked and fresh leases
    go to the nodes still in good standing;
  * a latched **model** loses all its leases; when the model itself echoed lease
    material (``model.self-reported``) it is rotated too;
  * a latched **decider** holds no keys; nothing to do.

If the key source cannot rotate (no upstream key API wired in), the exposed
key is never lent again: the model stays dark until the operator installs a
new key -- fail closed. After an operator reset, leases resume on the next tick.

Exposure is tracked by the key's **fingerprint**, not its version: a version is
only a counter in the ``KeySource`` (``MemoryKeySource`` counts from 1 again on
every start), so "version 2 is exposed" means nothing after a restart, while
"this secret is exposed" still does. A lease's ``key_version`` is the broker's
own counter, one per model: it goes up whenever the key it lends changes and on
every start, so a fresh lease always outranks one issued before a restart (model
nodes and deciders prefer the highest version).

A latched node is treated as holding every lease this broker signed for it --
the ones issued by this process and the ones it has seen in heartbeats since --
and the current key of every model it is assigned. So a node convicted after a
broker restart, holding leases from before it, still loses them and still has
its keys rotated.

Durability. Given a ``BrokerStore``, exposures and the version counters are
written down and replayed on start. Nothing in the store can lend a key: a
record can only darken a key or raise a counter, so a forged line fails closed.
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass

from secdogie_identity import RevocationStore, verify_binding

from .lease import (
    MAX_REVOKED,
    KeySource,
    make_lease,
    make_lease_revocation,
    new_canary,
    seal_grant,
)
from .mutiny import MODEL_SELF_REPORTED, model_target
from .participant import Config, Participant
from .protocol import ROLE_BROKER, is_hex64, is_int, is_model

log = logging.getLogger("secdogie_inference")

_FINGERPRINT = b"secdogie/inference/key-fingerprint/v1\x00"
MAX_VERSION = 2 ** 53  # a lease's key_version stays an exact JSON number everywhere


def key_fingerprint(secret: str) -> str:
    """Names a secret without holding it: SHA-256, domain-separated. Upstream
    keys are high-entropy, so the fingerprint does not give the key away."""
    return hashlib.sha256(_FINGERPRINT + secret.encode("utf-8")).hexdigest()


class BrokerStore(RevocationStore):
    """The broker's durable memory, one JSON record per line (the mechanics of
    identity's ``RevocationStore``: append + fsync, ``0600``, torn lines skipped)::

        {"t": "exposed", "model": m, "fp": <key_fingerprint>, "why": ...}
        {"t": "version", "model": m, "v": <the highest key_version leased>}

    Never the secrets themselves. A record can only darken a key or raise a
    counter, so nothing written here can make the broker lend a key."""


@dataclass(frozen=True)
class Assignment:
    transport_public_key: str   # base64 X25519, from the node's verified DID binding
    models: tuple[str, ...]


def assignments_from_bindings(bindings, models: dict[str, list[str]], trust, *, now=None) -> dict[str, Assignment]:
    """Map each model node to its transport key, taken only from a binding that
    verifies for a mesh DID. ``models`` is the operator's DID -> models plan."""
    out: dict[str, Assignment] = {}
    for binding in bindings:
        res = verify_binding(binding, allowlist=trust.mesh, now=now)
        if not res.ok or res.did not in models:
            continue
        wanted = tuple(sorted(set(models[res.did])))
        if not wanted or not all(is_model(m) for m in wanted):
            raise ValueError(f"bad model list for {res.did}")
        out[res.did] = Assignment(res.transport_public_key, wanted)
    return out


class KeyBroker(Participant):
    roles = frozenset({ROLE_BROKER})

    def __init__(self, identity, wire, trust, keys: KeySource, *, assignments: dict[str, Assignment],
                 config: Config | None = None, clock=time.time, ledger_store=None,
                 broker_store: BrokerStore | None = None):
        """``broker_store``: where exposed-key fingerprints and lease version
        counters are kept across restarts (see the module docstring)."""
        super().__init__(identity, wire, trust, config=config, clock=clock, ledger_store=ledger_store)
        if not trust.brokers.contains(self.did):
            raise ValueError("a broker must be on the brokers list")
        self.keys = keys
        self.assignments = dict(assignments)
        self._issued: dict[str, dict] = {}       # lease id -> public lease, unrevoked
        self._granted_fp: dict[str, str] = {}    # lease id -> fingerprint of the key it carried
        self._exposed: dict[str, str] = {}       # key fingerprint -> model: never lend again
        self._versions: dict[str, int] = {}      # model -> highest lease key_version issued
        self._top: dict[str, str] = {}           # model -> fingerprint that version belongs to
        self._store = broker_store
        self.store_errors = 0
        if broker_store is not None:
            for rec in broker_store.load():
                self._restore(rec)
        for did in sorted(self.assignments):
            self.connect(did)

    # -- lending ----------------------------------------------------------------------

    def on_tick(self, now: float) -> None:
        for lid in [lid for lid, x in self._issued.items() if x["expires_at"] <= now]:
            del self._issued[lid]
            self._granted_fp.pop(lid, None)
        self.issue_due(now)

    def issue_due(self, now: float) -> int:
        """Grant every assigned (node, model) that has no fresh lease. Returns
        how many grants went out."""
        ttl = self.config.lease_ttl
        sent = 0
        for did, assignment in sorted(self.assignments.items()):
            if self.ledger.excludes_node(did):
                continue
            for model in assignment.models:
                current = self.keys.current(model)
                if current is None or self.ledger.is_latched(model_target(model)):
                    continue
                fp = key_fingerprint(current[1])
                if fp in self._exposed:
                    continue  # whatever version the source calls it now
                version = self._lease_version(model, fp)
                fresh = any(x["subject"] == did and x["model"] == model and x["key_version"] == version
                            and x["expires_at"] - now > ttl * self.config.lease_renew_at
                            for x in self._issued.values())
                if not fresh:
                    self._grant(did, assignment, model, version, current[1], fp)
                    sent += 1
        return sent

    def _lease_version(self, model: str, fp: str) -> int:
        """The key_version to lease ``model``'s current key under: unchanged
        while the key is, one higher whenever it changes -- and once per start,
        so nothing issued before a restart outranks what is issued after it.
        Written down before the first lease carries it."""
        if self._top.get(model) != fp:
            self._versions[model] = self._versions.get(model, 0) + 1
            self._top[model] = fp
            self._persist({"t": "version", "model": model, "v": self._versions[model]})
        return self._versions[model]

    def _grant(self, did: str, assignment: Assignment, model: str, version: int, secret: str, fp: str) -> None:
        canary = new_canary()
        lease = make_lease(self.identity, did, model, key_version=version, canary=canary,
                           ttl=self.config.lease_ttl, clock=self.clock)
        sealed = seal_grant(lease, secret, canary, assignment.transport_public_key)
        self._issued[lease["lease_id"]] = lease
        self._granted_fp[lease["lease_id"]] = fp
        self.leases.add(lease)
        self.send(did, "grant", lease=lease, sealed=sealed)
        self.note("lease", node=did[-8:], model=model, version=version)

    def exposed(self) -> dict[str, str]:
        """Fingerprints of keys that will never be lent again -> their model.
        For the operator: a model stays dark until a new key is installed."""
        return dict(self._exposed)

    # -- reacting to mutiny ---------------------------------------------------------------

    def _signed_leases(self, now: float) -> dict[str, dict]:
        """Every unrevoked, unexpired lease this broker signed that it knows of:
        issued by this process, or seen in a heartbeat (issued before a restart)."""
        out = dict(self._issued)
        for lease in self.leases.live(now):
            if lease["broker"] == self.did:
                out.setdefault(lease["lease_id"], lease)
        return out

    def on_incident(self, incident) -> None:
        now = float(self.clock())
        why = f"incident {incident.incident_id[:16]}"
        signed = self._signed_leases(now)
        revoke: set[str] = set()
        rotate: set[str] = set()
        expose: set[str] = set()      # models whose current key is exposed
        for target in incident.targets:
            kind, _, ref = target.partition(":")
            if kind == "node":
                held = [x for x in signed.values() if x["subject"] == ref]
                revoke |= {x["lease_id"] for x in held}
                # What it may hold: every model it has a lease for, and every model it
                # is assigned -- the broker lent those on every tick until the latch.
                models = {x["model"] for x in held} | set(getattr(self.assignments.get(ref), "models", ()))
                rotate |= models
                expose |= models
                for x in held:
                    if x["lease_id"] in self._granted_fp:
                        self._expose(self._granted_fp[x["lease_id"]], x["model"], why)
            elif kind == "model":
                revoke |= {x["lease_id"] for x in signed.values() if x["model"] == ref}
                if incident.kind == MODEL_SELF_REPORTED:
                    rotate.add(ref)
                    expose.add(ref)
        for model in sorted(expose):
            current = self.keys.current(model)
            if current is not None:
                self._expose(key_fingerprint(current[1]), model, why)  # before rotating: crash-safe
        for model in sorted(rotate):
            new = self.keys.rotate(model)
            self.note("rotate", model=model, version=new)
            # Every lease of the model predates the rotation, or the key could not
            # rotate and is now exposed: either way none of them stands.
            revoke |= {x["lease_id"] for x in signed.values() if x["model"] == model}
        self._revoke(revoke, why)
        self.issue_due(now)

    def _expose(self, fp: str, model: str, why: str) -> None:
        if fp in self._exposed:
            return
        self._exposed[fp] = model
        self._persist({"t": "exposed", "model": model, "fp": fp, "why": why})

    # -- durability --------------------------------------------------------------------

    def _restore(self, rec) -> None:
        if not isinstance(rec, dict) or not is_model(rec.get("model")):
            return
        model = rec["model"]
        if rec.get("t") == "exposed" and is_hex64(rec.get("fp")):
            self._exposed[rec["fp"]] = model
        elif rec.get("t") == "version" and is_int(rec.get("v")) and 0 <= rec["v"] < MAX_VERSION:
            self._versions[model] = max(self._versions.get(model, 0), rec["v"])

    def _persist(self, rec: dict) -> None:
        if self._store is None:
            return
        try:
            self._store.append(rec)
        except OSError as exc:
            # Memory still holds it; only its survival of a restart is at risk.
            self.store_errors += 1
            log.error("key broker: could not persist %s for %s: %s", rec["t"], rec["model"], exc)

    def on_reset(self, unlatched: frozenset) -> None:
        self.issue_due(float(self.clock()))

    def _revoke(self, lease_ids: set[str], reason: str) -> None:
        ids = sorted(lease_ids)
        for i in range(0, len(ids), MAX_REVOKED):
            chunk = ids[i:i + MAX_REVOKED]
            revocation = make_lease_revocation(self.identity, chunk, reason=reason, clock=self.clock)
            for lid in chunk:
                self._issued.pop(lid, None)
            self.publish(revocation)
            self.note("revoke", leases=len(chunk), reason=reason)


__all__ = ["KeyBroker", "BrokerStore", "Assignment", "assignments_from_bindings", "key_fingerprint"]
