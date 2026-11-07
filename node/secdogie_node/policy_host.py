"""How rules reach a node: the ``policy/v1`` channel and the bundles in force.

An operator's App (Studio) signs a policy bundle (``secdogie_policy``) with the
operator key and offers it to the node on the ``policy/v1`` channel of the
node's ChannelMux. The transport has already authenticated the App. The bundle
is believed only for its own signatures, so the App is just the courier.

The node accepts a bundle only when all of these hold:

  * it verifies: well formed, tightens only, signed by whom its kind requires
    (an operator for ``gate-rules`` / ``ingest``, ``threshold`` masters for
    ``decision-model`` / ``allowlist``);
  * it is for this node's mesh, and its scope names this node or one of its
    roles (or is empty: the whole mesh);
  * it is the next version after the bundle in force for the same
    (kind, mesh, scope), naming it; and its ``not_before`` has passed.

An accepted bundle is appended to the node's signed journal (``policy`` event)
and takes effect at once. On restart the node reloads what it activated from
the journal, re-checking structure and signatures but not who is trusted
today. A revoked signer therefore cannot loosen the gates by making a bundle
vanish; replacing a bundle takes a newer version.

What takes effect now: ``gate-rules`` (both gates and the companion's
presence switches, via ``GateRules``). ``ingest``, ``decision-model`` (mesh
routing) and ``allowlist`` bundles are verified, journaled and reported, and
the node says plainly that it does not apply them yet.

Wire (JSON in both directions)::

    App -> node   {"op": "offer", "req": "<id>", "bundle": {...}}
                  {"op": "list",  "req": "<id>"}
    node -> App   {"req": "<id>", "ok": bool, "errors": [...], "note": "...", "active": [...]}
"""
from __future__ import annotations

import json
import logging
import secrets
import threading
import time

from secdogie_citadel.rules import GateRules
from secdogie_identity.signing import canonical
from secdogie_policy import check_bundle, check_successor, verify_bundle

log = logging.getLogger("secdogie_node.policy")

POLICY_CHANNEL = "policy/v1"
POLICY_EVENT = "policy"
APPLIED_KINDS = frozenset({"gate-rules"})
MAX_MESSAGE = 60_000


def _scope_key(scope) -> str:
    return canonical({"scope": scope}).decode("utf-8")


class PolicyHost:
    """The policy bundles in force on one node."""

    def __init__(self, journal, *, mesh: str, operators, masters=None, node_did: str, roles=(),
                 clock=time.time):
        self.journal = journal
        self.mesh = mesh
        self.operators = operators
        self.masters = masters
        self.node_did = node_did
        self.roles = frozenset(roles)
        self._clock = clock
        self._lock = threading.Lock()
        self._active: dict[tuple[str, str, str], dict] = {}
        self._load()

    # -- the journal ---------------------------------------------------------------

    def _load(self) -> None:
        for event in self.journal.events():
            if event.get("kind") != POLICY_EVENT:
                continue
            body = event.get("body") or {}
            bundle = body.get("bundle")
            if body.get("op") != "activate" or check_bundle(bundle):
                continue
            key = (bundle["kind"], bundle["mesh"], _scope_key(bundle["scope"]))
            if not check_successor(self._active.get(key), bundle):
                self._active[key] = bundle

    # -- what applies ----------------------------------------------------------------

    def applies(self, bundle: dict) -> bool:
        scope = bundle.get("scope") or {}
        nodes, roles = scope.get("nodes") or [], scope.get("roles") or []
        if not nodes and not roles:
            return True
        return self.node_did in nodes or bool(self.roles & set(roles))

    def active(self, kind: str | None = None) -> list[dict]:
        with self._lock:
            found = [b for (k, _, _), b in self._active.items() if kind is None or k == kind]
        return sorted(found, key=lambda b: (b["kind"], _scope_key(b["scope"])))

    def gate_rules(self) -> GateRules:
        return GateRules.from_bodies((b["bundle_id"], b["body"]) for b in self.active("gate-rules"))

    def summary(self) -> list[dict]:
        return [{"kind": b["kind"], "version": b["version"], "bundle_id": b["bundle_id"], "scope": b["scope"],
                 "applied": b["kind"] in APPLIED_KINDS} for b in self.active()]

    # -- offers ----------------------------------------------------------------------

    def offer(self, bundle) -> dict:
        verdict = verify_bundle(bundle, operators=self.operators, masters=self.masters)
        if not verdict.ok:
            return {"ok": False, "errors": list(verdict.errors)}
        if bundle["mesh"] != self.mesh:
            return {"ok": False, "errors": ["mesh:mesh"], "note": f"this node is in mesh {self.mesh!r}"}
        if not self.applies(bundle):
            return {"ok": False, "errors": ["scope:scope"], "note": "the bundle's scope does not name this node"}
        if bundle["not_before"] > self._clock():
            return {"ok": False, "errors": ["not-yet:not_before"]}
        key = (bundle["kind"], bundle["mesh"], _scope_key(bundle["scope"]))
        with self._lock:
            current = self._active.get(key)
            if current is not None and current["bundle_id"] == bundle["bundle_id"]:
                return {"ok": True, "errors": [], "note": "already in force"}
            errors = check_successor(current, bundle)
            if errors:
                return {"ok": False, "errors": errors}
            self.journal.append(POLICY_EVENT, {"op": "activate", "bundle": bundle,
                                               "signers": list(verdict.signers)})
            self._active[key] = bundle
        applied = bundle["kind"] in APPLIED_KINDS
        log.info("policy %s v%d in force (%s)", bundle["kind"], bundle["version"], bundle["bundle_id"][:12])
        note = "in force" if applied else f"recorded; this node does not apply {bundle['kind']} bundles yet"
        return {"ok": True, "errors": [], "note": note}

    # -- the channel -------------------------------------------------------------------

    def attach(self, mux, *, may_send=None) -> None:
        """Serve ``policy/v1`` on ``mux``. ``may_send(did)``: who may use the
        channel (the node's App allowlist); the transport already drops
        everyone else, this is the second door."""

        def handle(sender: str, payload: bytes) -> None:
            if may_send is not None and not may_send(sender):
                return
            try:
                msg = json.loads(payload)
            except (ValueError, UnicodeDecodeError):
                return
            if not isinstance(msg, dict) or not isinstance(msg.get("req"), str):
                return
            if msg.get("op") == "offer":
                reply = self.offer(msg.get("bundle"))
            elif msg.get("op") == "list":
                reply = {"ok": True, "errors": []}
            else:
                reply = {"ok": False, "errors": ["op:op"]}
            reply = {"req": msg["req"][:64], "mesh": self.mesh, "active": self.summary(), **reply}
            mux.send(sender, POLICY_CHANNEL, json.dumps(reply, ensure_ascii=False).encode("utf-8"))

        mux.channel(POLICY_CHANNEL, handle)


class ChannelClient:
    """An App's side of a request/reply channel to one node (``policy/v1``,
    ``admin/v1``): send a request and wait for the answer naming it,
    re-sending (the channel is plain datagrams) until it comes."""

    def __init__(self, mux, node_did: str, channel: str):
        self.mux = mux
        self.node_did = node_did
        self.channel = channel
        self._cond = threading.Condition()
        self._replies: dict[str, dict] = {}
        mux.channel(channel, self._on_reply)

    def _on_reply(self, sender: str, payload: bytes) -> None:
        if sender != self.node_did:
            return
        try:
            msg = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            return
        if isinstance(msg, dict) and isinstance(msg.get("req"), str):
            with self._cond:
                self._replies[msg["req"]] = msg
                self._cond.notify_all()

    def request(self, op: str, *, timeout: float = 10.0, resend_every: float = 0.5, **fields) -> dict | None:
        req = secrets.token_hex(8)
        msg = {"op": op, "req": req, **{k: v for k, v in fields.items() if v is not None}}
        payload = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        if len(payload) > MAX_MESSAGE:
            raise ValueError("the request is too large for one datagram")
        deadline = time.monotonic() + timeout
        with self._cond:
            while req not in self._replies:
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                self.mux.send(self.node_did, self.channel, payload)
                self._cond.wait(min(left, resend_every))
            return self._replies.pop(req)


class PolicyClient(ChannelClient):
    """An App's side of ``policy/v1``: offer a bundle to one node and wait for
    the verdict."""

    def __init__(self, mux, node_did: str):
        super().__init__(mux, node_did, POLICY_CHANNEL)

    def offer(self, bundle: dict, *, timeout: float = 10.0) -> dict | None:
        return self.request("offer", bundle=bundle, timeout=timeout)

    def list(self, *, timeout: float = 10.0) -> dict | None:
        return self.request("list", timeout=timeout)


__all__ = ["APPLIED_KINDS", "POLICY_CHANNEL", "ChannelClient", "PolicyClient", "PolicyHost"]
