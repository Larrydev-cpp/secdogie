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
version is never lent again: the model stays dark until the operator installs a
new key -- fail closed. After an operator reset, leases resume on the next tick.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from secdogie_identity import verify_binding

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
from .protocol import ROLE_BROKER, is_model


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
                 config: Config | None = None, clock=time.time):
        super().__init__(identity, wire, trust, config=config, clock=clock)
        if not trust.brokers.contains(self.did):
            raise ValueError("a broker must be on the brokers list")
        self.keys = keys
        self.assignments = dict(assignments)
        self._issued: dict[str, dict] = {}     # lease id -> public lease, unrevoked
        self._dark: dict[str, int] = {}        # model -> exposed key version not to lend again
        for did in sorted(self.assignments):
            self.connect(did)

    # -- lending ----------------------------------------------------------------------

    def on_tick(self, now: float) -> None:
        for lid in [lid for lid, x in self._issued.items() if x["expires_at"] <= now]:
            del self._issued[lid]
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
                version, secret = current
                if self._dark.get(model, -1) >= version:
                    continue
                fresh = any(x["subject"] == did and x["model"] == model and x["key_version"] == version
                            and x["expires_at"] - now > ttl * self.config.lease_renew_at
                            for x in self._issued.values())
                if not fresh:
                    self._grant(did, assignment, model, version, secret)
                    sent += 1
        return sent

    def _grant(self, did: str, assignment: Assignment, model: str, version: int, secret: str) -> None:
        canary = new_canary()
        lease = make_lease(self.identity, did, model, key_version=version, canary=canary,
                           ttl=self.config.lease_ttl, clock=self.clock)
        sealed = seal_grant(lease, secret, canary, assignment.transport_public_key)
        self._issued[lease["lease_id"]] = lease
        self.leases.add(lease)
        self.send(did, "grant", lease=lease, sealed=sealed)
        self.note("lease", node=did[-8:], model=model, version=version)

    # -- reacting to mutiny ---------------------------------------------------------------

    def on_incident(self, incident) -> None:
        now = float(self.clock())
        revoke: set[str] = set()
        rotate: set[str] = set()
        for target in incident.targets:
            kind, _, ref = target.partition(":")
            if kind == "node":
                held = [x for x in self._issued.values() if x["subject"] == ref]
                revoke |= {x["lease_id"] for x in held}
                rotate |= {x["model"] for x in held}
            elif kind == "model":
                revoke |= {x["lease_id"] for x in self._issued.values() if x["model"] == ref}
                if incident.kind == MODEL_SELF_REPORTED:
                    rotate.add(ref)
        for model in sorted(rotate):
            old = self.keys.current(model)
            new = self.keys.rotate(model)
            if new is None and old is not None:
                self._dark[model] = old[0]  # cannot rotate: never lend the exposed key again
            self.note("rotate", model=model, version=new)
            revoke |= {x["lease_id"] for x in self._issued.values()
                       if x["model"] == model and (new is None or x["key_version"] < new)}
        self._revoke(revoke, f"incident {incident.incident_id[:16]}")
        self.issue_due(now)

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


__all__ = ["KeyBroker", "Assignment", "assignments_from_bindings"]
