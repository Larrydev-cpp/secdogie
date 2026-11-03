"""A running inference-mesh node over real UDP: what ``secdogie-inference run`` starts.

The library classes (``MeshNode``, ``Coordinator``, ``KeyBroker``) are transport-
agnostic and driven by ``tick()``. ``Runtime`` gives one of them a process:

    UDPChannel → DirectUDPTransport (DID-signed frames; sealed with --transport-key)
               → FailoverTransport (when relay records are given)
               → ChannelMux → MuxWire → the participant

plus a tick thread, registration with rendezvous servers (so other nodes can
find this one by DID), a ``Resolver`` that turns a DID into an endpoint without
blocking the participant, and ``Discovery`` (``discovery.py``), which keeps the
node linked to enough peers.

Nothing here widens trust: the transport admits only DIDs on ``trust.peers``,
every participant check stays in force, and discovery dials only DIDs already on
the mesh trust list.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from .broker import KeyBroker
from .coordinator import Coordinator
from .discovery import Discovery, DiscoveryConfig
from .node import MeshNode
from .participant import Config
from .protocol import ROLE_BROKER, ROLE_COORDINATOR, TrustRoots
from .wire import MuxWire

log = logging.getLogger("secdogie_inference")

MAX_LOOKUPS = 8        # concurrent rendezvous lookups
RETRY_LOOKUP = 5.0     # seconds before the same DID is looked up again


@dataclass
class RuntimeConfig:
    identity: object
    trust: TrustRoots
    roles: frozenset
    listen: tuple[str, int] = ("0.0.0.0", 0)
    public_host: str | None = None                    # the address announced to rendezvous servers
    peers: dict[str, tuple[str, int]] = field(default_factory=dict)  # static DID -> (host, port)
    bootstrap: list[dict] = field(default_factory=list)   # rendezvous records (secdogie-relay --rendezvous)
    relays: list[dict] = field(default_factory=list)      # relay records, for NAT fallback
    transport_key: object | None = None              # X25519: seal frames (needs peer bindings)
    peer_bindings: list[dict] = field(default_factory=list)
    lease_key: object | None = None                  # X25519: open lease grants (model role)
    backend: Callable | None = None                  # model role: (model, input, secret) -> output
    standby: list[str] = field(default_factory=list)      # coordinator role; a client's standby forwarders
    entries: list[str] = field(default_factory=list)      # client: serving nodes to submit to
    key_source: object | None = None                 # broker role
    assignments: dict = field(default_factory=dict)       # broker role
    admissions: object | None = None                 # an AdmissionBook the mesh trust set also consults
    preload: list[dict] = field(default_factory=list)     # admissions to publish at start
    config: Config = field(default_factory=Config)
    tick: float = 0.2
    discovery: DiscoveryConfig | None = field(default_factory=DiscoveryConfig)


class Resolver:
    """Where a DID can be reached: a static peer entry, else a rendezvous lookup
    on a background thread. ``resolve`` never blocks: the participant calls it
    under its own lock, and frames sent before the answer are simply lost, as
    UDP datagrams may be; the next heartbeat goes through."""

    def __init__(self, transport, peers: dict[str, tuple[str, int]], rendezvous=None, *,
                 retry_after: float = RETRY_LOOKUP, clock=time.monotonic):
        self._transport = transport
        self._peers = dict(peers)
        self._rendezvous = rendezvous
        self._retry_after = float(retry_after)
        self._clock = clock
        self._lock = threading.Lock()
        self._pending: set[str] = set()
        self._asked: dict[str, float] = {}
        self._slots = threading.BoundedSemaphore(MAX_LOOKUPS)
        self.found: dict[str, tuple[str, int]] = {}

    def resolve(self, did: str) -> None:
        if did in self._peers:
            self._transport.set_peer_endpoint(did, *self._peers[did])
            return
        if self._rendezvous is None:
            return
        now = self._clock()
        with self._lock:
            if did in self._pending or now - self._asked.get(did, float("-inf")) < self._retry_after:
                return
            if not self._slots.acquire(blocking=False):
                return  # busy: the next dial attempt tries again
            self._pending.add(did)
            self._asked[did] = now
        threading.Thread(target=self._lookup, args=(did,), daemon=True, name="inference-lookup").start()

    def _lookup(self, did: str) -> None:
        try:
            found = self._rendezvous.lookup(did)
            best = found.best() if found is not None else None
            if best is not None:
                self._transport.set_peer_endpoint(did, best.host, best.port)
                with self._lock:
                    self.found[did] = (best.host, best.port)
        except Exception:  # noqa: BLE001 - a failed lookup is retried on a later dial
            log.debug("lookup of %s failed", did, exc_info=True)
        finally:
            with self._lock:
                self._pending.discard(did)
            self._slots.release()


ROLE_CLIENT = "client"   # runtime-only: a UserClient, not a mesh role


class ResolvingWire(MuxWire):
    """A ``MuxWire`` that asks the resolver again whenever a send finds no way to
    the peer. A lookup made before the peer registered with rendezvous comes
    back empty; without this, nothing would ever ask again and every later
    frame to that peer -- a lease grant, say -- would go nowhere."""

    def send(self, to_did: str, payload: bytes) -> bool:
        ok = super().send(to_did, payload)
        if not ok and self._resolve is not None:
            self._resolve(to_did)  # rate-limited by the resolver
        return ok


def build_participant(cfg: RuntimeConfig, wire):
    roles = frozenset(cfg.roles)
    if roles == {ROLE_CLIENT}:
        from .client import UserClient

        return UserClient(cfg.identity, wire, cfg.trust, entries=cfg.entries, standby=cfg.standby,
                          config=cfg.config)
    if roles == {ROLE_COORDINATOR}:
        return Coordinator(cfg.identity, wire, cfg.trust, standby=cfg.standby, config=cfg.config)
    if roles == {ROLE_BROKER}:
        if cfg.key_source is None:
            raise ValueError("a key broker needs a key source")
        return KeyBroker(cfg.identity, wire, cfg.trust, cfg.key_source, assignments=cfg.assignments,
                         config=cfg.config)
    if roles & {ROLE_COORDINATOR, ROLE_BROKER}:
        raise ValueError("the coordinator and the key broker run alone, without other roles")
    return MeshNode(cfg.identity, wire, cfg.trust, roles=roles, backend=cfg.backend,
                    transport_key=cfg.lease_key, config=cfg.config)


class Runtime:
    def __init__(self, cfg: RuntimeConfig):
        from secdogie_transport import (
            ChannelMux,
            DirectUDPTransport,
            Endpoint,
            FailoverTransport,
            PeerIdentity,
            RendezvousLink,
            Session,
            UDPChannel,
        )

        self.cfg = cfg
        self._Endpoint = Endpoint
        self.channel = UDPChannel(*cfg.listen)
        try:
            self.transport = DirectUDPTransport(cfg.identity, self.channel, allowlist=cfg.trust.peers,
                                                transport_key=cfg.transport_key)
            for binding in cfg.peer_bindings:
                if not self.transport.add_peer_binding(binding):
                    raise ValueError(f"a peer binding did not verify: {binding.get('did', '?')}")
            carrier = self.transport
            self.link = None
            if cfg.relays:
                self.link = carrier = FailoverTransport.from_records(self.transport, cfg.relays)
            self.rendezvous = RendezvousLink.from_records(self.transport, cfg.bootstrap) if cfg.bootstrap else None
            me = Session("inference", PeerIdentity(cfg.identity.did, ""),
                         active=Endpoint("local", *self.channel.address))
            self.mux = ChannelMux(carrier, me)
            self.resolver = Resolver(self.transport, cfg.peers, self.rendezvous)
            self.wire = ResolvingWire(self.mux, resolve=self.resolver.resolve)
            self.participant = build_participant(cfg, self.wire)
            if cfg.admissions is not None:
                self.participant.attach_admissions(cfg.admissions)
            self.wire.attach(self.participant)
            self.discovery = (Discovery(self.participant, config=cfg.discovery, hints=self._hints)
                              if cfg.discovery is not None else None)
        except Exception:
            self.channel.close()
            raise
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        return self.channel.address

    @property
    def did(self) -> str:
        return self.cfg.identity.did

    def _hints(self) -> Iterable[str]:
        """DIDs to try before any heartbeat has arrived: the static peers, then
        every DID on the mesh trust list that can be enumerated."""
        out = list(self.cfg.peers)
        dids = getattr(self.cfg.trust.mesh, "dids", None)
        if callable(dids):
            out.extend(sorted(dids()))
        return out

    def _own_endpoints(self) -> list:
        host, port = self.channel.address
        if self.cfg.public_host:
            return [self._Endpoint("public", self.cfg.public_host, port)]
        return [] if host in ("", "0.0.0.0") else [self._Endpoint("local", host, port)]

    def start(self) -> None:
        if self.link is not None:
            self.link.start()
        if self.rendezvous is not None:
            self.rendezvous.start(self._own_endpoints)
        for did in self.cfg.peers:
            self.participant.connect(did)
        for admission in self.cfg.preload:
            if not self.participant.publish(admission):
                log.warning("an admission did not verify or is not new: %s", admission.get("subject", "?"))
        self._thread = threading.Thread(target=self._run, daemon=True, name="inference-tick")
        self._thread.start()

    def _run(self) -> None:
        next_discovery = 0.0
        while not self._stop.wait(self.cfg.tick):
            try:
                self.participant.tick()
                now = time.time()
                if self.discovery is not None and now >= next_discovery:
                    next_discovery = now + self.discovery.config.every
                    self.discovery.step()
            except Exception:  # noqa: BLE001 - one bad round must not stop the node
                log.exception("tick failed")

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        if self.rendezvous is not None:
            self.rendezvous.close()
        if self.link is not None:
            self.link.close()
        self.channel.close()

    def stats(self) -> dict:
        p = self.participant
        now = time.time()
        links = p.lsdb.adjacency(now, exclude=p.excluded(now)).get(p.did, set())
        out = {"mode": p.mode(now), "neighbors": len(p.neighbors), "links": len(links),
               "degree": len(self.discovery.linked(now)) if self.discovery is not None else len(links),
               "known": len(p.lsdb.known()), "resolved": len(self.resolver.found),
               "leases": len(p.leases.live(now)), "alive": sum(1 for d in p.lsdb.known() if p.lsdb.alive(d, now))}
        sessions = getattr(p, "sessions", None)
        if sessions is not None:
            out["sessions"] = len(sessions)
        return out


__all__ = ["Runtime", "RuntimeConfig", "Resolver", "ResolvingWire", "build_participant", "ROLE_CLIENT"]
