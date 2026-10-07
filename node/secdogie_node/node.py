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
  * a worker thread that runs ready goals one at a time.

Zero trust throughout: ``apps``, ``operators`` and ``authorized`` are required
(``None`` refuses to start); ``unrestricted`` (no capability check) is an
explicit, test-and-development-only switch. One App session at a time: while
one is alive, another App is not accepted.

The operator is a page that comes and goes, the node is not: there is one
``OperatorBridge`` for the node's whole life. A step that needs the operator
while no App is reachable -- before the first one, or across a closed tab or a
refresh -- waits for one up to the challenge's or question's own expiry (still
a no when it passes); when an App says HELLO the node tells it what it is doing
(``status: idle`` / ``status: running`` / ``status: waiting for operator``,
naming the goal) and shows it every challenge and question still open.

Control requests from the App are answered with a status line starting
``accepted`` or ``refused``. After each goal the node tells the App how it
ended and offers any quarantined (S2) facts / preferences for confirmation.
An App on a link (a browser over WebRTC) gets no structural snapshots.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from secdogie_citadel.consolidate import confirm_and_promote, retract_memory
from secdogie_citadel.journal import Journal
from secdogie_citadel.lessons import MemoryClass
from secdogie_citadel.supervisor import MemoryConfig, Supervisor, agent_run_task
from secdogie_dialogue.agent_bridge import OperatorBridge
from secdogie_dialogue.dialogue import system_status
from secdogie_dialogue.protocol import ControlOp, MemoryCandidatePacket
from secdogie_dialogue.publisher import SnapshotPublisher
from secdogie_dialogue.session import DialogueSession, SessionRouter
from secdogie_identity import AllowlistWatcher, require_trust
from secdogie_transport import (
    WEBRTC_HOST,
    ChannelMux,
    CompositeChannel,
    DirectUDPTransport,
    Endpoint,
    FailoverTransport,
    PeerIdentity,
    RendezvousLink,
    Session,
    UDPChannel,
    is_link_host,
)

log = logging.getLogger("secdogie_node")

OFFERED_CLASSES = (MemoryClass.FACT, MemoryClass.PREFERENCE)  # cautions are promoted on evidence


@dataclass
class NodeConfig:
    identity: object  # this node's Identity
    apps: object  # operator App session keys: who may open a dialogue and confirm memory
    operators: object  # operator keys whose Gate 2 signatures authorize destructive steps
    authorized: object  # journal authors whose events this node accepts
    issuers: object = None  # capability grant issuers; None -> every mutating action is refused
    unrestricted: bool = False  # INSECURE: no capability check (tests / local development)
    journal_path: str = ":memory:"
    candidates_path: str = ":memory:"
    listen: tuple[str, int] = ("127.0.0.1", 0)
    transport_key: object = None
    app_bindings: list = field(default_factory=list)  # signed DID -> transport-key bindings of the Apps
    relay_records: list = field(default_factory=list)  # relays' self-signed records: the fallback path
    rendezvous_records: list = field(default_factory=list)  # where this node registers, so Apps find it by DID
    run_task: Callable = agent_run_task
    webrtc: object = None  # a secdogie_transport.webrtc.WebRTCConfig: the browser link (needs the [webrtc] extra)
    # The files apps / operators were read from: followed while the node runs (``pair``,
    # another process, appends to them), each change journaled.
    apps_file: str | None = None
    operators_file: str | None = None
    challenge_ttl: float = 120.0
    probe_ttl: float = 300.0
    idle_poll: float = 1.0


class _AppLink:
    def __init__(self, session: DialogueSession, bridge: OperatorBridge, publisher: SnapshotPublisher | None):
        self.session, self.bridge, self.publisher = session, bridge, publisher
        self.offered: set[str] = set()


class Node:
    def __init__(self, cfg: NodeConfig):
        self.cfg = cfg
        self.identity = cfg.identity
        self.apps = require_trust(cfg.apps, "the node's App allowlist")
        self.operators = require_trust(cfg.operators, "the node's operator allowlist")
        self.journal = Journal(cfg.journal_path, identity=cfg.identity,
                               allowlist=require_trust(cfg.authorized, "the node's journal allowlist"))
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
        self._running: str | None = None  # the goal the worker is running now
        self.bridge = OperatorBridge(cfg.identity, None, operators=self.operators,
                                     challenge_ttl=cfg.challenge_ttl, probe_ttl=cfg.probe_ttl,
                                     on_control=self.on_control, on_hello=self._report_status,
                                     hold_on_disconnect=True)
        self.supervisor.set_operator_hooks(self.bridge.hooks())
        self._watchers = [AllowlistWatcher(path, target, on_change=self._allowlist_changed(name))
                           for name, path, target in (("apps", cfg.apps_file, self.apps),
                                                      ("operators", cfg.operators_file, self.operators)) if path]
        self._watch_stops: list = []
        self.webrtc = None
        self.channel = UDPChannel(*cfg.listen)
        try:
            if cfg.webrtc is not None:
                from secdogie_transport.webrtc import BindingPolicy, WebRTCChannel

                # The browser link: W1 against --apps, a signed refusal for a stranger or while busy.
                self.webrtc = WebRTCChannel(cfg.webrtc, BindingPolicy(cfg.identity, self.apps, speak_first=True,
                                                                      admit=self._admit_link,
                                                                      refresh=self.refresh_allowlists))
                self.channel = CompositeChannel(self.channel, {WEBRTC_HOST: self.webrtc})
            self.transport = DirectUDPTransport(cfg.identity, self.channel, allowlist=self.apps,
                                                transport_key=cfg.transport_key,
                                                bound_link_hosts=frozenset({WEBRTC_HOST}) if self.webrtc else frozenset())
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
        except Exception:
            self.channel.close()
            raise

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
        self._watch_stops = [w.start() for w in self._watchers]
        if self.webrtc is not None:
            self.webrtc.open()

    def _own_endpoints(self) -> list[Endpoint]:
        # What this node can say about itself; a rendezvous adds the address it
        # sees the node's packets come from (the one that works across NAT).
        host, port = self.channel.address
        return [] if host in ("", "0.0.0.0") else [Endpoint("local", host, port)]

    def stop(self, timeout: float = 10.0) -> None:
        """Stop taking work, stop the running goal, say goodbye, close."""
        self._stopping.set()
        for s in self._watch_stops:
            s.set()
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
            self._refused.clear()
            session = DialogueSession(self.identity, did, self.router.sender_for(did), trust=self.apps)
            on_link = is_link_host(self.transport.endpoint_host(did))
            publisher = None if on_link else SnapshotPublisher(session.send)
            self.bridge.rebind(session, publisher=publisher)
            self._link = _AppLink(session, self.bridge, publisher)
            self.supervisor.set_operator_hooks(self.bridge.hooks())

        def down():
            log.warning("operator App %s is unreachable; steps that need it wait for it until they expire", did)

        def up():
            log.info("operator App %s is reachable again", did)

        session.on_peer_down, session.on_peer_up = down, up
        session.start(0.05)
        log.info("operator App connected: %s%s", did, " (over a link)" if on_link else "")
        self._offer_memories()
        return session

    def refresh_allowlists(self) -> None:
        """Re-read the allowlist files now (each is cheap when unchanged)."""
        for w in self._watchers:
            try:
                w.refresh()
            except Exception:  # noqa: BLE001 - the periodic watcher tries again
                log.exception("re-reading an allowlist failed")

    def _allowlist_changed(self, name: str):
        def changed(added, removed) -> None:
            log.warning("%s changed on disk: added %s, removed %s", name, sorted(added) or "none",
                        sorted(removed) or "none")
            try:
                self.journal.append("enrollment", {"op": "allowlist-reload", "list": name,
                                                   "added": sorted(added), "removed": sorted(removed)})
            except Exception:  # noqa: BLE001 - the change already applied; a journal hiccup must not undo it
                log.exception("could not journal the %s change", name)

        return changed

    def _admit_link(self, did: str) -> str | None:
        """W1 passed for ``did`` on the browser link: refuse (signed) while
        another App is connected."""
        with self._lock:
            cur = self._link
            if cur is not None and cur.session.peer_did != did and cur.session.alive:
                return "busy"
        return None

    def current_status(self):
        """CURRENT_STATUS: what the node is doing, as a status line naming the
        goal (never a step's detail)."""
        running = self._running
        if running is None:
            return system_status("status: idle")
        if self.bridge.waiting():
            return system_status("status: waiting for operator", about=running)
        return system_status("status: running", about=running)

    def _report_status(self) -> None:
        self._send(self.current_status())

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
        self._running = gid
        self._report_status()
        try:
            code, summary = self.supervisor.run_goal(gid)
        finally:
            self._running = None
        log.info("goal %s: exit %s -- %s", gid, code, summary)
        self._send(system_status(f"goal {gid} finished: exit {code} -- {summary}"))
        self._report_status()
        self._offer_memories()
        return True


__all__ = ["Node", "NodeConfig", "OFFERED_CLASSES"]
