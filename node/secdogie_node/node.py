"""A running secdogie node: the network and the signed journal, assembled.

Everything here already exists as a tested library; this module only puts the
pieces together into one process:

  * one UDP port (`DirectUDPTransport`; encrypted v2 frames when a transport key
    is configured) carrying two protocols, multiplexed as {"p": "rep"|"mem", "m": ...}:
      - "rep": journal anti-entropy (`ReplicationPeer`, size-bounded replies), so
        goals, run records, grants and Socratic records converge on every node;
      - "mem": membership gossip (`MembershipView`), so a node learns where the
        other nodes are without a central directory;
  * bindings travel in the journal: a node with a transport key writes its own
    signed DID -> transport-key binding as a `transport_binding` event, and every
    node imports the bindings it has replicated (each still verified: signature,
    validity window, allowlist, key version). Only the bootstrap peers' bindings
    need to be configured by hand.

Boundaries: only allowlisted DIDs are heard (frames are signed and allowlist-
gated, every journal event is verified again on merge, membership records too).
The node does NOT run goals -- execution stays with `secdogie-citadel run`, with
its capability checks, Socratic step and high-risk confirmation. It installs no
service and no autostart; the operator runs it.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass

from secdogie_citadel.journal import Journal
from secdogie_citadel.replication import ReplicationPeer
from secdogie_identity import Allowlist, Identity
from secdogie_identity.binding import create_binding
from secdogie_transport import Endpoint, MembershipView, PeerIdentity, Session
from secdogie_transport.membership import announce
from secdogie_transport.sealed import load_transport_key, public_key_b64
from secdogie_transport.udp import DirectUDPTransport, UDPChannel

from .config import NodeConfig

log = logging.getLogger("secdogie_node")

BINDING_KIND = "transport_binding"
REP, MEM = "rep", "mem"
_RECORDS_PER_MESSAGE = 40  # membership records are ~0.5 KB; stays far below a datagram
# Re-publish our own binding when the one in the journal has less than this left.
_BINDING_REFRESH = 30 * 24 * 3600.0


@dataclass
class NodeStatus:
    did: str
    listen: tuple[str, int]
    encrypted: bool
    peers: list[str]
    reachable: list[str]
    keys: list[str]
    events: int
    heads: dict[str, int]


class Node:
    def __init__(self, cfg: NodeConfig, *, clock=time.time):
        self.cfg = cfg
        self._clock = clock
        self._lock = threading.RLock()
        self.identity = Identity.load(cfg.identity)
        self.did = self.identity.did
        self.allowlist = Allowlist.load(cfg.authorized)
        self.journal = Journal(cfg.journal, identity=self.identity, allowlist=self.allowlist)
        self._tkey = load_transport_key(cfg.transport_key) if cfg.transport_key else None
        self.channel = UDPChannel(cfg.listen_host, cfg.listen_port)
        self.transport = DirectUDPTransport(
            self.identity, self.channel, allowlist=self.allowlist, transport_key=self._tkey,
        )
        self.view = MembershipView(allowlist=self.allowlist)
        self.replication = ReplicationPeer(self.journal, lambda to, m: self._send(REP, to, m))
        self._bootstrap: set[str] = set()
        self._imported: set[tuple[str, int]] = set()

        host, port = self.channel.address
        self.transport.register(
            Session("self-" + self.did[-8:], PeerIdentity(self.did, "self"),
                    active=Endpoint("local", host, port)),
            self._on_message,
        )
        for did, phost, pport in cfg.peers:
            self.add_peer(did, phost, pport)
        for path in cfg.bindings:
            self.add_binding_file(path)
        if self._tkey is not None:
            self._publish_own_binding()

    # -- setup ---------------------------------------------------------------

    @property
    def address(self) -> tuple[str, int]:
        return self.channel.address

    @property
    def encrypted(self) -> bool:
        return self._tkey is not None

    def add_peer(self, did: str, host: str, port: int) -> None:
        """A bootstrap peer: contacted every round even before gossip knows it."""
        self._bootstrap.add(did)
        self.transport.set_peer_endpoint(did, host, port)

    def add_binding_file(self, path: str) -> bool:
        with open(path, encoding="utf-8") as f:
            ok = self.transport.add_peer_binding(json.load(f))
        if not ok:
            log.warning("binding %s did not verify (signature / validity / allowlist / version)", path)
        return ok

    def _publish_own_binding(self) -> None:
        pub = public_key_b64(self._tkey)
        now = self._clock()
        for e in self.journal.events():
            body = e.get("body") or {}
            if (e.get("kind") == BINDING_KIND and e.get("author") == self.did
                    and body.get("transport_public_key") == pub
                    and body.get("key_version") == self.cfg.key_version
                    and float(body.get("expires_at", 0)) - now > _BINDING_REFRESH):
                return  # a current one is already in the journal
        self.journal.append(BINDING_KIND, create_binding(self.identity, pub, key_version=self.cfg.key_version))

    # -- wire ----------------------------------------------------------------

    def _send(self, proto: str, to_did: str, message: dict) -> bool:
        data = json.dumps({"p": proto, "m": message}).encode("utf-8")
        return self.transport.route(self.did, to_did, data)

    def _on_message(self, from_did: str, data: bytes) -> None:
        try:
            envelope = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if not isinstance(envelope, dict) or not isinstance(envelope.get("m"), dict):
            return
        proto, message = envelope.get("p"), envelope["m"]
        try:
            if proto == REP:
                self.replication.on_message(from_did, message)
            elif proto == MEM:
                self._on_membership(from_did, message)
        except Exception:  # noqa: BLE001 -- one bad message must not stop the node
            log.exception("error handling %s message from %s", proto, from_did)

    # -- membership gossip ---------------------------------------------------

    def _endpoints_to_announce(self) -> list[Endpoint]:
        host, port = self.address
        if self.cfg.announce_host:
            return [Endpoint("public", self.cfg.announce_host, port)]
        if host in ("0.0.0.0", "::", ""):
            return []  # no usable address to claim; peers learn it from our traffic
        return [Endpoint("local", host, port)]

    def _digest_message(self, *, reply: bool) -> dict:
        with self._lock:
            return {"kind": "digest", "digest": self.view.digest(), "reply": reply}

    def _on_membership(self, from_did: str, message: dict) -> None:
        kind = message.get("kind")
        if kind == "digest":
            remote = message.get("digest") if isinstance(message.get("digest"), dict) else {}
            with self._lock:
                records = self.view.records_for(remote)
            for i in range(0, len(records), _RECORDS_PER_MESSAGE):
                self._send(MEM, from_did, {"kind": "records", "records": records[i:i + _RECORDS_PER_MESSAGE]})
            if not message.get("reply"):
                self._send(MEM, from_did, self._digest_message(reply=True))
        elif kind == "records":
            records = message.get("records") if isinstance(message.get("records"), list) else []
            with self._lock:
                self.view.apply_records(records, now=self._clock())
            self._learn_endpoints()

    def _learn_endpoints(self) -> None:
        """Use gossiped endpoints for peers we have no address for yet. An address
        a peer's own traffic arrived from (the transport's roaming rule) wins."""
        with self._lock:
            known = [(did, self.view.endpoints_for(did)) for did in self.view.known()]
        for did, endpoints in known:
            if did == self.did or endpoints is None or self.transport.peer_endpoint(did) is not None:
                continue
            best = endpoints.best()
            if best is not None:
                self.transport.set_peer_endpoint(did, best.host, best.port)

    # -- bindings from the journal -------------------------------------------

    def _import_bindings(self) -> int:
        added = 0
        for e in self.journal.events():
            if e.get("kind") != BINDING_KIND or e.get("author") == self.did:
                continue
            key = (e.get("author"), int(e.get("seq", 0)))
            if key in self._imported:
                continue
            self._imported.add(key)
            if self.transport.add_peer_binding(e.get("body") or {}):
                added += 1
        return added

    # -- the round -----------------------------------------------------------

    def peers(self) -> list[str]:
        with self._lock:
            gossiped = set(self.view.known())
        return sorted((gossiped | self._bootstrap) - {self.did})

    def tick(self) -> int:
        """One round: announce ourselves, import bindings, then offer membership
        and journal digests to every peer we can reach. Returns how many peers
        were contacted."""
        now = self._clock()
        with self._lock:
            announce(self.identity, self._endpoints_to_announce(), last_seen=now, view=self.view, now=now)
        self._import_bindings()
        self._learn_endpoints()
        contacted = 0
        for did in self.peers():
            if self._send(MEM, did, self._digest_message(reply=False)):
                contacted += 1
                self.replication.initiate(did)
        return contacted

    def run(self, stop: threading.Event) -> None:
        log.info("node %s listening on %s:%d (%s)", self.did, *self.address,
                 "encrypted" if self.encrypted else "signed, unencrypted")
        while not stop.is_set():
            try:
                n = self.tick()
                log.debug("round: contacted %d peer(s)", n)
            except Exception:  # noqa: BLE001 -- keep the node up; the next round retries
                log.exception("round failed")
            stop.wait(self.cfg.sync_interval)

    def status(self) -> NodeStatus:
        peers = self.peers()
        return NodeStatus(
            did=self.did,
            listen=self.address,
            encrypted=self.encrypted,
            peers=peers,
            reachable=[d for d in peers if self.transport.peer_endpoint(d) is not None],
            keys=self.transport.peer_keys(),
            events=len(self.journal.events()),
            heads=self.journal.heads(),
        )

    def close(self) -> None:
        self.channel.close()
        self.journal.close()


def offline_status(cfg: NodeConfig) -> dict:
    """What a node holds, read from its files without opening the network."""
    identity = Identity.load(cfg.identity)
    journal = Journal(cfg.journal)
    try:
        events = journal.events()
        bindings = sorted({e["author"] for e in events if e.get("kind") == BINDING_KIND})
        return {
            "did": identity.did,
            "listen": f"{cfg.listen_host}:{cfg.listen_port}",
            "encrypted": cfg.transport_key is not None,
            "events": len(events),
            "heads": journal.heads(),
            "bindings_in_journal": bindings,
            "bootstrap_peers": [did for did, _h, _p in cfg.peers],
        }
    finally:
        journal.close()


__all__ = ["BINDING_KIND", "Node", "NodeStatus", "offline_status"]
