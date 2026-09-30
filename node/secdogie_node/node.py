"""The resident node: one process that puts the pieces together.

``Node`` assembles what the other packages already provide -- no new protocol:

  * a DID-authenticated UDP transport (``DirectUDPTransport``) that hears only
    the operator Apps on ``apps``, with the dialogue channel on a ``ChannelMux``
    -- and, given relay records, a ``FailoverTransport``: direct first, the
    relay whenever the App has not been heard directly of late; given
    rendezvous records, it registers there (and keeps renewing), so an App
    finds it by DID alone;
  * per App, a ``DialogueSession`` + ``OperatorBridge`` (Gate 2 challenges,
    Socratic probes, control requests) + ``SnapshotPublisher`` (the structural
    view), handed to the Supervisor as its ``OperatorHooks``;
  * a signed ``Journal`` (authors on ``authorized``) and a ``Supervisor`` with
    staged memory, whose capability gate trusts ``issuers`` -- none means every
    mutating action is refused;
  * a worker thread that runs ready goals one at a time;
  * the mesh (stage 3): the same transport also hears the other nodes on
    ``mesh``. Membership records are gossiped with them (``MembershipGossip``)
    and the journal is replicated with them (``ReplicationPeer``, in
    datagram-sized batches). What this node learns from a peer -- a caution
    earned by the peer's failing runs -- reaches its Gate 1; a peer's goals
    reach its journal but are never run here (the Supervisor runs only its own).

Zero trust throughout: ``apps``, ``operators`` and ``authorized`` are required
(``None`` refuses to start); ``unrestricted`` (no capability check) is an
explicit, test-and-development-only switch. One App session at a time: while
one is alive, another App is not accepted. While no App is reachable the
Supervisor holds no operator hooks, so a step that needs the operator is
refused at once (no answer is a no); they come back with the App.

Control requests from the App are answered with a status line starting
``accepted`` or ``refused``. After each goal the node tells the App how it
ended and offers any quarantined (S2) facts / preferences for confirmation.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from secdogie_citadel.consolidate import confirm_and_promote, retract_memory
from secdogie_citadel.journal import Journal
from secdogie_citadel.lessons import MemoryClass
from secdogie_citadel.replication import ReplicationPeer
from secdogie_citadel.supervisor import MemoryConfig, Supervisor, agent_run_task
from secdogie_dialogue.agent_bridge import OperatorBridge
from secdogie_dialogue.dialogue import system_status
from secdogie_dialogue.protocol import ControlOp, MemoryCandidatePacket
from secdogie_dialogue.publisher import SnapshotPublisher
from secdogie_dialogue.session import DialogueSession, SessionRouter
from secdogie_identity import AnyOf, require_trust
from secdogie_transport import (
    ChannelMux,
    DirectUDPTransport,
    Endpoint,
    FailoverTransport,
    MembershipGossip,
    MembershipView,
    PeerIdentity,
    RendezvousLink,
    Session,
    UDPChannel,
)
from secdogie_transport.membership import sign_record

log = logging.getLogger("secdogie_node")

OFFERED_CLASSES = (MemoryClass.FACT, MemoryClass.PREFERENCE)  # cautions are promoted on evidence
REPLICATION_CHANNEL = "replication/v1"
REPLICATION_MAX_BYTES = 24_000  # events per message: one datagram, even through a relay


@dataclass
class NodeConfig:
    identity: object  # this node's Identity
    apps: object  # operator App session keys: who may open a dialogue and confirm memory
    operators: object  # operator keys whose Gate 2 signatures authorize destructive steps
    authorized: object  # journal authors whose events this node accepts
    mesh: object  # the other nodes: who gossips membership and replicates the journal with this one
    issuers: object = None  # capability grant issuers; None -> every mutating action is refused
    unrestricted: bool = False  # INSECURE: no capability check (tests / local development)
    journal_path: str = ":memory:"
    candidates_path: str = ":memory:"
    listen: tuple[str, int] = ("127.0.0.1", 0)
    transport_key: object = None
    app_bindings: list = field(default_factory=list)  # signed DID -> transport-key bindings of the Apps
    relay_records: list = field(default_factory=list)  # relays' self-signed records: the fallback path
    rendezvous_records: list = field(default_factory=list)  # where this node registers, so Apps find it by DID
    bootstrap_records: list = field(default_factory=list)  # mesh nodes' self-signed records to start from
    mesh_every: float = 5.0  # seconds between gossip / replication rounds
    run_task: Callable = agent_run_task
    challenge_ttl: float = 120.0
    probe_ttl: float = 300.0
    idle_poll: float = 1.0


class _AppLink:
    def __init__(self, session: DialogueSession, bridge: OperatorBridge, publisher: SnapshotPublisher):
        self.session, self.bridge, self.publisher = session, bridge, publisher
        self.offered: set[str] = set()


class Node:
    def __init__(self, cfg: NodeConfig):
        self.cfg = cfg
        self.identity = cfg.identity
        self.apps = require_trust(cfg.apps, "the node's App allowlist")
        self.operators = require_trust(cfg.operators, "the node's operator allowlist")
        authorized = require_trust(cfg.authorized, "the node's journal allowlist")
        self.mesh = require_trust(cfg.mesh, "the node's mesh allowlist")
        if hasattr(self.mesh, "dids") and hasattr(authorized, "dids"):
            outside = sorted(self.mesh.dids() - authorized.dids())
            if outside:  # their replicated events would be dropped without a word
                raise ValueError(f"every mesh node must also be a journal author (--authorized): {outside[0]}")
        self.journal = Journal(cfg.journal_path, identity=cfg.identity, allowlist=authorized)
        self.supervisor = Supervisor(
            self.journal, cfg.run_task, issuers=cfg.issuers, unrestricted=cfg.unrestricted,
            memory=MemoryConfig(candidates_path=cfg.candidates_path, confirmers=self.apps),
        )
        self._lock = threading.RLock()
        self._link: _AppLink | None = None
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._worker: threading.Thread | None = None
        self._refused: set[str] = set()
        self.channel = UDPChannel(*cfg.listen)
        try:
            self.transport = DirectUDPTransport(cfg.identity, self.channel, allowlist=AnyOf(self.apps, self.mesh),
                                                transport_key=cfg.transport_key)
            for binding in cfg.app_bindings:
                if not self.transport.add_peer_binding(binding):
                    raise ValueError(f"an App binding did not verify: {binding.get('did', '?')}")
            self.link = None
            carrier = self.transport
            if cfg.relay_records:
                self.link = carrier = FailoverTransport.from_records(self.transport, cfg.relay_records)
            self.rendezvous = None
            if cfg.rendezvous_records:
                self.rendezvous = RendezvousLink.from_records(self.transport, cfg.rendezvous_records)
            self.mux = ChannelMux(carrier, Session("node", PeerIdentity(cfg.identity.did, ""),
                                                   active=Endpoint("local", *self.channel.address)))
            self.router = SessionRouter(self.mux, accept=self._accept)
            self.view = MembershipView(allowlist=self.mesh)
            for record in cfg.bootstrap_records:
                if not self.view.merge_record(record, now=time.time()):
                    raise ValueError("a bootstrap record must be a valid, self-signed record of a mesh node")
            self.gossip = MembershipGossip(self.mux, self.view, peers=self.mesh, self_record=self.record,
                                           on_learn=self._learn_peer)
            self.replica = ReplicationPeer(self.journal, self._send_replication, max_bytes=REPLICATION_MAX_BYTES)
            self.mux.channel(REPLICATION_CHANNEL, self._on_replication)
            for did in self.view.known():
                self._learn_peer(self.view.get(did))
        except Exception:
            self.channel.close()
            raise
        self._mesh_thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        return self.channel.address

    # -- lifecycle ------------------------------------------------------------------

    def start(self) -> None:
        requeued = self.supervisor.recover()
        if requeued:
            log.info("resumed %d interrupted goal(s): %s", len(requeued), ", ".join(requeued))
        self._worker = threading.Thread(target=self._work, daemon=True, name="secdogie-node-worker")
        self._worker.start()
        if self.link is not None:
            self.link.start()
        if self.rendezvous is not None:
            self.rendezvous.start(self._own_endpoints)
        self._mesh_thread = threading.Thread(target=self._mesh_loop, daemon=True, name="secdogie-node-mesh")
        self._mesh_thread.start()

    def _own_endpoints(self) -> list[Endpoint]:
        # What this node can say about itself; a rendezvous adds the address it
        # sees the node's packets come from (the one that works across NAT).
        host, port = self.channel.address
        return [] if host in ("", "0.0.0.0") else [Endpoint("local", host, port)]

    # -- the mesh -------------------------------------------------------------------

    def record(self) -> dict:
        """This node's self-signed membership record, fresh: where it can be
        reached (its own address, and any address a rendezvous saw it at). Other
        nodes can start from it (``--bootstrap-record``)."""
        endpoints = self._own_endpoints()
        if self.rendezvous is not None:
            endpoints += [Endpoint("observed", e.host, e.port) for e in self.rendezvous.reflexive.values()]
        return sign_record(self.identity, endpoints, last_seen=time.time())

    def _learn_peer(self, rec) -> None:
        # A peer found through gossip becomes reachable; an address already
        # known (set, or learned from the peer's own newest frame) is kept.
        if rec is None or rec.did == self.identity.did or self.transport.peer_endpoint(rec.did) is not None:
            return
        best = rec.endpoints.best()
        if best is not None:
            self.transport.set_peer_endpoint(rec.did, best.host, best.port)

    def peers(self) -> list[str]:
        """The mesh nodes this node currently knows."""
        return self.gossip.targets()

    def replicate_now(self) -> None:
        """Offer this node's journal heads to every known peer (they answer
        with what this node lacks, and ask for what they lack)."""
        for did in self.peers():
            self.replica.initiate(did)

    def _send_replication(self, to_did: str, payload: dict) -> None:
        self.mux.send(to_did, REPLICATION_CHANNEL, json.dumps(payload, separators=(",", ":")).encode())

    def _on_replication(self, from_did: str, payload: bytes) -> None:
        if not self.mesh.contains(from_did):
            return  # an App shares the transport; only mesh nodes replicate
        try:
            msg = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            return
        merged = self.replica.on_message(from_did, msg)
        if merged:
            log.info("replicated %d event(s) from %s", merged, from_did)

    def _mesh_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                self.gossip.tick()
                self.replicate_now()
            except Exception:  # noqa: BLE001 - a failed round retries on the next one
                log.exception("mesh round failed")
            self._stopping.wait(self.cfg.mesh_every)

    def stop(self, timeout: float = 10.0) -> None:
        """Stop taking work, stop the running goal, say goodbye, close."""
        self._stopping.set()
        self.supervisor.halt("the node is shutting down")
        self._wake.set()
        if self._worker is not None:
            self._worker.join(timeout)
        with self._lock:
            link, self._link = self._link, None
        if link is not None:
            link.session.close()
        if self.link is not None:
            self.link.close()
        if self.rendezvous is not None:
            self.rendezvous.close()
        if self._mesh_thread is not None:
            self._mesh_thread.join(timeout)
        self.gossip.close()
        self.channel.close()

    # -- the App ----------------------------------------------------------------------

    def _accept(self, did: str) -> DialogueSession | None:
        if not self.apps.contains(did) or self._stopping.is_set():
            return None
        with self._lock:
            cur = self._link
            if cur is not None and cur.session.peer_did != did and cur.session.alive:
                if did not in self._refused:
                    self._refused.add(did)
                    log.warning("refused a session from %s: %s is connected", did, cur.session.peer_did)
                return None
            if cur is not None:
                self.router.remove(cur.session.peer_did)
                cur.session.close()
            session = DialogueSession(self.identity, did, self.router.sender_for(did), trust=self.apps)
            publisher = SnapshotPublisher(session.send)
            bridge = OperatorBridge(self.identity, session, operators=self.operators,
                                    challenge_ttl=self.cfg.challenge_ttl, probe_ttl=self.cfg.probe_ttl,
                                    on_control=self.on_control, publisher=publisher)
            link = self._link = _AppLink(session, bridge, publisher)
            self.supervisor.set_operator_hooks(bridge.hooks())

        def down():
            bridge.on_peer_down()  # every pending challenge and probe fails now
            with self._lock:
                if self._link is link:
                    self.supervisor.set_operator_hooks(None)
            log.warning("operator App %s is unreachable; steps that need it are refused", did)

        def up():
            with self._lock:
                if self._link is link:
                    self.supervisor.set_operator_hooks(bridge.hooks())
            log.info("operator App %s is reachable again", did)

        session.on_peer_down, session.on_peer_up = down, up
        session.start(0.05)
        log.info("operator App connected: %s", did)
        self._offer_memories()
        return session

    def on_control(self, pkt, signer: str) -> str:
        """One operator request (already authenticated as ``signer``, an App on
        the allowlist). Returns the status line sent back."""
        op = pkt.op
        if op is ControlOp.ADD_GOAL:
            self.supervisor.add_goal(pkt.goal_id, title=pkt.title)
            self._wake.set()
            return f"accepted: goal {pkt.goal_id} queued"
        if op is ControlOp.STOP:
            self.supervisor.request_stop(pkt.goal_id)
            return f"accepted: stop requested for {pkt.goal_id}"
        if op is ControlOp.PAUSE:
            self.supervisor.request_pause(pkt.goal_id)
            return f"accepted: pause requested for {pkt.goal_id}"
        if op is ControlOp.RESUME:
            self.supervisor.resume(pkt.goal_id)
            self._wake.set()
            return f"accepted: {pkt.goal_id} resumed"
        if op is ControlOp.CONFIRM_MEMORY:
            try:
                confirm_and_promote(self.journal, self.supervisor._candidates, pkt.memory_id, pkt.confirmation,
                                    confirmers=self.apps)
            except (KeyError, ValueError) as e:
                return f"refused: {e}"
            return f"accepted: remembered {pkt.memory_id[:12]}"
        if op is ControlOp.RETRACT_MEMORY:
            if pkt.memory_id not in self.supervisor.memory_view().records:
                return f"refused: no active memory {pkt.memory_id[:12]}"
            retract_memory(self.journal, pkt.memory_id, reason=f"retracted by the operator ({signer})")
            return f"accepted: retracted {pkt.memory_id[:12]}"
        return f"refused: {op.value} is not supported"

    def _send(self, packet) -> None:
        with self._lock:
            link = self._link
        if link is not None and link.session.alive:
            link.session.send(packet)

    def _offer_memories(self) -> None:
        with self._lock:
            link = self._link
        if link is None:
            return
        store = self.supervisor._candidates
        for c in store.items():
            if c.mclass not in OFFERED_CLASSES or c.candidate_id in link.offered:
                continue
            link.offered.add(c.candidate_id)
            link.session.send(MemoryCandidatePacket(c.candidate_id, c.mclass.value, c.scope, c.key, c.value,
                                                    c.source))

    # -- the work -----------------------------------------------------------------------

    def _work(self) -> None:
        while not self._stopping.is_set():
            try:
                ran = self.run_ready_once()
            except Exception:  # noqa: BLE001 - keep serving; the journal records what the goal did
                log.exception("the worker hit an error; continuing")
                ran = False
            if not ran:
                self._wake.wait(self.cfg.idle_poll)
                self._wake.clear()

    def run_ready_once(self) -> bool:
        """Run the lowest ready goal, if any; tell the App how it ended."""
        if self.supervisor.halted or self._stopping.is_set():
            return False
        ready = sorted(self.supervisor.pending_ready())
        if not ready:
            return False
        gid = ready[0]
        code, summary = self.supervisor.run_goal(gid)
        log.info("goal %s: exit %s -- %s", gid, code, summary)
        self._send(system_status(f"goal {gid} finished: exit {code} -- {summary}"))
        self._offer_memories()
        self.replicate_now()  # what this goal taught the node goes out now, not at the next round
        return True


__all__ = ["Node", "NodeConfig", "OFFERED_CLASSES", "REPLICATION_CHANNEL"]
