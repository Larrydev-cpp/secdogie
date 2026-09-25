"""A mesh node: the transport pieces assembled into one running peer (P2P mesh, 2B).

Until now the pieces existed side by side -- `DirectUDPTransport`, rendezvous,
`DirectUpgrader`, membership gossip -- but nothing composed them. `MeshNode` does:

  * One UDP socket. Direct frames go to the transport; everything else on the
    socket (rendezvous REGISTER / LOOKUP and their replies) reaches the node via
    the transport's `on_other` hook and is verified by the rendezvous code.
  * Per peer, a `Session` + `DirectUpgrader`: every peer starts on the relay (if
    one is configured), is upgraded to a direct path once a signed round trip
    proves one, and falls back to the relay when round trips stop.
  * Channels (`mux.py`) over whichever path a peer is on: `member` carries the
    membership gossip built into the node; upper layers add their own with
    `add_protocol` (e.g. `secdogie_citadel.replication.attach` adds journal
    replication). Messages are fragmented to stay under the path MTU.
  * Roles, not servers. Any node can also `serve_rendezvous`; nothing here needs
    a dedicated host or a VPS. (Serving as a network relay is the next slice;
    today `relay` is any `Transport` the caller supplies.)

Concurrency model: the socket's receive thread and any relay only *enqueue*
inbound datagrams; all protocol work happens in `process()` / `tick()` under the
node's own lock, on the caller's thread or the node's thread (`start()`). A node
never calls into another node's state, so there are no cross-node lock cycles
even with an in-process relay.

Security is inherited, not reimplemented: direct frames are DID-signed,
timestamped and replay-checked (udp.py / freshness.py), rendezvous frames are
signed and freshness-checked (rendezvous.py), membership records are self-signed
and allowlist-gated (membership.py). No new crypto, no traffic obfuscation, no
detection-evasion.
"""
from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from secdogie_identity import Identity

from . import mux
from .endpoint import Endpoint, EndpointSet
from .membership import MembershipView, announce
from .peer import PeerIdentity
from .rendezvous import LOOKUP, LOOKUP_RESULT, REGISTER, REGISTER_ACK, RendezvousClient, RendezvousServer
from .session import Session
from .transport import Transport
from .udp import DirectUDPTransport, UDPChannel, _frame_type
from .upgrade import (
    CONNECT,
    DEFAULT_DEAD_AFTER,
    DEFAULT_PROBE_TIMEOUT,
    PROBING,
    DirectUpgrader,
    _endpoints_from,
    decode_upgrade,
)

MEMBER = "member"  # the built-in membership gossip channel

MessageFn = Callable[[str, object], None]  # (from_did, body) -> None
SyncFn = Callable[[str], None]              # (peer_did) -> None


@dataclass
class _Protocol:
    on_message: MessageFn
    on_sync: SyncFn | None = None


@dataclass
class _Peer:
    did: str
    session: Session
    upgrader: DirectUpgrader
    candidates: EndpointSet
    last_attempt: float = float("-inf")
    last_keepalive: float = float("-inf")
    last_gossip: float = float("-inf")
    last_sync: float = float("-inf")


def _default_endpoints(channel: UDPChannel) -> list[Endpoint]:
    host, port = channel.address[0], channel.address[1]
    if host in ("", "0.0.0.0", "::"):
        return []  # a wildcard bind says nothing about how to reach us
    return [Endpoint("local", host, port)]


class MeshNode:
    """One authorized node of the mesh. See the module docstring."""

    def __init__(
        self,
        identity: Identity,
        channel: UDPChannel,
        *,
        allowlist=None,
        relay: Transport | None = None,
        transport_key=None,
        advertise=None,
        clock=time.time,
        keepalive_interval: float = 10.0,
        dead_after: float = DEFAULT_DEAD_AFTER,
        probe_timeout: float = DEFAULT_PROBE_TIMEOUT,
        upgrade_retry: float = 30.0,
        gossip_interval: float = 15.0,
        sync_interval: float = 30.0,
        serve_rendezvous: bool = False,
        max_message: int = mux.DEFAULT_MAX_MESSAGE,
        fragment_size: int = mux.DEFAULT_FRAGMENT_SIZE,
    ):
        self.identity = identity
        self.did = identity.did
        self.allowlist = allowlist
        self.relay = relay
        self._clock = clock
        self.keepalive_interval = keepalive_interval
        self.dead_after = dead_after
        self.probe_timeout = probe_timeout
        self.upgrade_retry = upgrade_retry
        self.gossip_interval = gossip_interval
        self.sync_interval = sync_interval
        self.max_message = max_message
        self.fragment_size = fragment_size

        self._lock = threading.RLock()
        self._inbox: queue.Queue = queue.Queue()
        self._peers: dict[str, _Peer] = {}
        self._protocols: dict[str, _Protocol] = {}
        self._reasm = mux.Reassembler(clock=clock)
        self.membership = MembershipView(allowlist=allowlist)
        self.endpoints = EndpointSet(_default_endpoints(channel) if advertise is None else advertise)
        self._last_announce = float("-inf")
        self._announced_endpoints: list[Endpoint] = []

        self.rendezvous = RendezvousServer(identity, allowlist=allowlist, clock=clock) if serve_rendezvous else None
        self._rv_clients: dict[str, tuple[RendezvousClient, tuple[str, int]]] = {}
        self._last_register = float("-inf")

        self.direct = DirectUDPTransport(
            identity, channel, allowlist=allowlist, transport_key=transport_key, clock=clock,
            on_other=lambda raw, addr: self._inbox.put(("raw", raw, addr)),
        )
        me = PeerIdentity(self.did, "")
        self.direct.register(Session(f"self-{self.did[-8:]}", me),
                             lambda frm, data: self._inbox.put(("direct", frm, data)))
        if relay is not None:
            relay.register(Session(f"relay-{self.did[-8:]}", me),
                           lambda frm, data: self._inbox.put(("relay", frm, data)))

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # -- peers -----------------------------------------------------------------

    def _allowed(self, did) -> bool:
        return (isinstance(did, str) and did != self.did
                and (self.allowlist is None or self.allowlist.contains(did)))

    def _peer(self, did: str) -> _Peer | None:
        if not self._allowed(did):
            return None
        peer = self._peers.get(did)
        if peer is None:
            session = Session(f"{self.did[-6:]}-{did[-6:]}", PeerIdentity(did, ""))
            upgrader = DirectUpgrader(self.direct, session, relay=self.relay,
                                      probe_timeout=self.probe_timeout, clock=self._clock)
            peer = self._peers[did] = _Peer(did, session, upgrader, EndpointSet())
        return peer

    def add_peer(self, did: str, endpoints=(), *, binding: dict | None = None) -> bool:
        """Tell the node about an authorized peer and any endpoints known for it
        (e.g. a bootstrap list). With encryption on, `binding` is the peer's signed
        DID -> transport-key binding. Returns False for an unauthorized DID."""
        with self._lock:
            peer = self._peer(did)
            if peer is None:
                return False
            if binding is not None and not self.direct.add_peer_binding(binding):
                return False
            for ep in endpoints:
                peer.candidates.add(ep)
            peer.last_attempt = float("-inf")  # try the new endpoints on the next tick
            return True

    def peers(self) -> list[str]:
        with self._lock:
            return sorted(self._peers)

    def session_for(self, did: str) -> Session | None:
        peer = self._peers.get(did)
        return peer.session if peer is not None else None

    def path(self, did: str) -> str:
        """"direct", "relay" (a relay is configured) or "none"."""
        peer = self._peers.get(did)
        if peer is not None and peer.upgrader.state_for(did).is_direct:
            return "direct"
        return "relay" if self.relay is not None else "none"

    # -- channels ----------------------------------------------------------------

    def add_protocol(self, channel: str, on_message: MessageFn, *, on_sync: SyncFn | None = None) -> None:
        """Handle `channel` messages with `on_message(from_did, body)`; `on_sync(did)`
        (optional) runs for every reachable peer each `sync_interval`."""
        if channel == MEMBER:
            raise ValueError(f"channel {MEMBER!r} is reserved for membership gossip")
        with self._lock:
            self._protocols[channel] = _Protocol(on_message, on_sync)

    def send(self, peer_did: str, channel: str, body) -> str:
        """Send `body` on `channel` to `peer_did` over the direct path when the
        peer is DIRECT, else the relay. Returns "direct", "relay", "unreachable"
        or "unauthorized"."""
        with self._lock:
            peer = self._peer(peer_did)
            if peer is None:
                return "unauthorized"
            path = "unreachable"
            for dgram in mux.encode(channel, body, max_message=self.max_message,
                                    fragment_size=self.fragment_size):
                path = peer.upgrader.send(peer_did, dgram)
                if path == "unreachable":
                    break
            return path

    # -- rendezvous ------------------------------------------------------------

    def use_rendezvous(self, server_did: str, host: str, port: int) -> None:
        """Register with a peer that serves rendezvous (pinned by its DID). Its
        signed ack tells us our reflexive (public) endpoint; re-registration
        happens every `gossip_interval`."""
        with self._lock:
            client = RendezvousClient(self.identity, server_did, clock=self._clock)
            self._rv_clients[server_did] = (client, (host, int(port)))
            self._send_raw((host, int(port)), client.register_frame(self.endpoints))

    def lookup(self, peer_did: str) -> None:
        """Ask every rendezvous we use for `peer_did`'s endpoints; results arrive
        through `process()` and become upgrade candidates."""
        with self._lock:
            for client, addr in self._rv_clients.values():
                self._send_raw(addr, client.lookup_frame(peer_did))

    def _send_raw(self, addr: tuple[str, int], raw: bytes) -> None:
        try:
            self.direct.channel.send(addr[0], addr[1], raw)
        except OSError:
            pass  # unreachable rendezvous: retried on the next registration

    # -- inbound -----------------------------------------------------------------

    def process(self, max_items: int | None = None) -> int:
        """Handle queued inbound datagrams (non-blocking). Returns how many."""
        n = 0
        while max_items is None or n < max_items:
            try:
                item = self._inbox.get_nowait()
            except queue.Empty:
                break
            with self._lock:
                self._dispatch(item)
            n += 1
        return n

    def _dispatch(self, item) -> None:
        kind = item[0]
        if kind == "direct":
            self._on_direct(item[1], item[2])
        elif kind == "relay":
            self._on_relay(item[1], item[2])
        elif kind == "raw":
            self._on_raw(item[1], item[2])

    def _on_direct(self, frm: str, data: bytes) -> None:
        peer = self._peer(frm)
        if peer is None:
            return
        src = self.direct.peer_endpoint(frm)
        if src is not None and not peer.upgrader.state_for(frm).is_direct:
            # A verified frame reached us from `src`: dial it back (the "ping back"
            # half of a hole-punch). It stays a candidate until a round trip proves it.
            seen = Endpoint("observed", src[0], src[1])
            if seen not in peer.candidates.all():
                peer.candidates.add(seen)
                peer.last_attempt = float("-inf")
        reply = peer.upgrader.handle(frm, data)
        if reply is not None:
            self.direct.route(self.did, frm, reply)
            return
        if decode_upgrade(data) is None:
            self._on_payload(frm, data)

    def _on_relay(self, frm: str, data: bytes) -> None:
        peer = self._peer(frm)
        if peer is None:
            return
        msg = decode_upgrade(data)
        if msg is not None and msg["t"] == CONNECT:
            for ep in _endpoints_from(msg):
                peer.candidates.add(ep)
        if not peer.upgrader.handle_relay(frm, data, my_endpoints=self.endpoints.all()):
            self._on_payload(frm, data)

    def _on_payload(self, frm: str, data: bytes) -> None:
        got = self._reasm.feed(frm, data)
        if got is None:
            return
        channel, body = got
        if channel == MEMBER:
            self._on_member(frm, body)
            return
        proto = self._protocols.get(channel)
        if proto is not None:
            proto.on_message(frm, body)

    def _on_raw(self, raw: bytes, addr: tuple) -> None:
        t = _frame_type(raw)
        if t == REGISTER and self.rendezvous is not None:
            reply = self.rendezvous.on_register(raw, (addr[0], addr[1]))
            if reply is not None:
                self._send_raw((addr[0], addr[1]), reply)
        elif t == LOOKUP and self.rendezvous is not None:
            reply = self.rendezvous.on_lookup(raw)
            if reply is not None:
                self._send_raw((addr[0], addr[1]), reply)
        elif t == REGISTER_ACK:
            for client, _addr in self._rv_clients.values():
                reflexive = client.handle_register_ack(raw)
                if reflexive is not None:
                    self.endpoints.add(reflexive)
                    break
        elif t == LOOKUP_RESULT:
            for client, _addr in self._rv_clients.values():
                res = client.handle_lookup_result(raw)
                if res is None:
                    continue
                target, endpoints = res
                peer = self._peer(target)
                if peer is not None and len(endpoints):
                    for ep in endpoints.all():
                        peer.candidates.add(ep)
                    peer.last_attempt = float("-inf")
                break
        # anything else on the socket is not ours: dropped

    # -- membership gossip -------------------------------------------------------

    def _announce(self, now: float) -> None:
        announce(self.identity, self.endpoints, last_seen=now, view=self.membership, now=now)
        self._last_announce = now
        self._announced_endpoints = self.endpoints.all()

    def _learn(self, did: str) -> None:
        peer = self._peer(did)
        endpoints = self.membership.endpoints_for(did)
        if peer is None or endpoints is None:
            return
        before = len(peer.candidates)
        for ep in endpoints.all():
            peer.candidates.add(ep)
        if len(peer.candidates) != before:
            peer.last_attempt = float("-inf")  # new endpoints: worth a fresh attempt

    def _on_member(self, frm: str, body) -> None:
        if not isinstance(body, dict):
            return
        op = body.get("op")
        now = self._clock()
        if op == "digest" and isinstance(body.get("digest"), dict):
            digest = {d: v for d, v in body["digest"].items()
                      if isinstance(d, str) and isinstance(v, (int, float)) and not isinstance(v, bool)}
            records = self.membership.records_for(digest)
            if records:
                self.send(frm, MEMBER, {"op": "records", "records": records})
            if not body.get("reply"):
                self.send(frm, MEMBER, {"op": "digest", "digest": self.membership.digest(), "reply": True})
        elif op == "records" and isinstance(body.get("records"), list):
            for rec in body["records"]:
                if self.membership.merge_record(rec, now=now) and isinstance(rec, dict):
                    self._learn(rec.get("did"))

    # -- periodic work -----------------------------------------------------------

    def tick(self, now: float | None = None) -> None:
        """Drain the inbox, then run everything periodic: announce our record,
        re-register with rendezvous, attempt upgrades, keep direct paths alive,
        sweep dead ones, gossip membership and run protocol syncs."""
        self.process()
        with self._lock:
            t = self._clock() if now is None else float(now)
            if (t - self._last_announce >= self.gossip_interval
                    or self.endpoints.all() != self._announced_endpoints):
                self._announce(t)
            if self._rv_clients and t - self._last_register >= self.gossip_interval:
                self._last_register = t
                for client, addr in self._rv_clients.values():
                    self._send_raw(addr, client.register_frame(self.endpoints))
            for peer in list(self._peers.values()):
                self._tick_peer(peer, t)
            self._reasm.expire(t)

    def _tick_peer(self, peer: _Peer, t: float) -> None:
        did, up = peer.did, peer.upgrader
        st = up.state_for(did)
        if st.is_direct:
            if t - peer.last_keepalive >= self.keepalive_interval:
                peer.last_keepalive = t
                up.keepalive(did)
        elif st.state != PROBING and t - peer.last_attempt >= self.upgrade_retry:
            peer.last_attempt = t
            for ep in peer.candidates.all():
                up.probe(did, ep)
            if self.relay is not None:
                up.connect(did, self.endpoints.all())  # the peer dials us back too
        up.sweep(now=t, dead_after=self.dead_after)
        if not (up.state_for(did).is_direct or self.relay is not None):
            return  # no path to this peer yet
        if t - peer.last_gossip >= self.gossip_interval:
            peer.last_gossip = t
            self.send(did, MEMBER, {"op": "digest", "digest": self.membership.digest(), "reply": False})
        if t - peer.last_sync >= self.sync_interval:
            peer.last_sync = t
            for proto in list(self._protocols.values()):
                if proto.on_sync is not None:
                    proto.on_sync(did)

    # -- lifecycle ---------------------------------------------------------------

    def start(self, *, tick_every: float = 1.0) -> None:
        """Run `process()` / `tick()` on a background thread."""
        if self._thread is not None:
            return
        self._stop.clear()

        def loop():
            last = float("-inf")
            while not self._stop.is_set():
                try:
                    item = self._inbox.get(timeout=0.05)
                except queue.Empty:
                    item = None
                if item is not None:
                    with self._lock:
                        self._dispatch(item)
                if time.monotonic() - last >= tick_every:
                    last = time.monotonic()
                    self.tick()

        self._thread = threading.Thread(target=loop, daemon=True, name=f"mesh-{self.did[-6:]}")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def close(self) -> None:
        self.stop()
        self.direct.channel.close()


__all__ = ["MEMBER", "MeshNode"]
