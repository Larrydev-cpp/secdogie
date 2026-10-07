"""A WebRTC data channel as a link under the node's transport (needs the
``[webrtc]`` extra: aiortc + websockets).

The node joins a room on the signaling gateway (``webrtc/signaling/worker.js``)
and keeps it: whoever joins second offers (``web_peer.js`` does the same), every
incoming offer gets a fresh peer connection, and only the offerer ever offers
again after a lost link. The gateway only relays offer / answer / ICE; it never
sees the data channel, and it is not trusted with anything: before a single
transport frame crosses a link, the two ends trade their W1 statements
(``secdogie_identity.linkauth``) as TEXT messages, through a ``LinkPolicy``
that decides whom the link belongs to. Only then do BINARY messages -- one
DID-signed ``secdogie/direct`` frame each, from the bound DID only -- reach the
transport, as datagrams from ``("@webrtc", link_id)`` (``composite.py``).

Data only, enforced: an offer or answer whose SDP has any media section other
than an SCTP data channel is refused before it is answered, and nothing here
ever adds a track or opens a camera or microphone (a static test scans this
file for media imports).

Threads: one event loop thread runs signaling and aiortc; policy callbacks run
on a small pool (they may verify, enroll, or wait for a person); received
frames are handed to the transport from one receive thread through a bounded
queue. ``send`` may be called from any thread.
"""
from __future__ import annotations

import asyncio
import collections
import concurrent.futures
import ipaddress
import json
import logging
import queue
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlencode, urlsplit

from secdogie_identity.linkauth import ROOM_PATTERN, fingerprints_from_sdp, sdp_is_data_only

log = logging.getLogger("secdogie_transport.webrtc")

DATA_CHANNEL_LABEL = "secdogie-data"
PING_INTERVAL = 20.0           # the gateway drops a socket idle for 60 s
NEGOTIATION_TIMEOUT = 30.0     # link created -> data channel open
BIND_TIMEOUT = 15.0            # data channel open -> bound to a DID (a policy may extend it)
MAX_RENEGOTIATIONS = 3         # the offerer re-offers after a lost link, 1 s, 2 s, 4 s apart
MAX_SIGNAL_BYTES = 16 * 1024   # the gateway's frame limit
MAX_CONTROL_TEXT = 8 * 1024    # one TEXT (statement) message
MAX_CONTROL_MESSAGES = 8       # TEXT messages per link, ever
MAX_DATAGRAM = 64 * 1024       # one BINARY (frame) message
MAX_UNBOUND_BINARY = 16        # BINARY messages tolerated (dropped) before binding
BUFFER_HIGH_WATER = 1024 * 1024
LINK_RATE_PER_MIN = 10         # new peer connections per minute
RECONNECT_MIN, RECONNECT_MAX = 1.0, 60.0
RECV_QUEUE = 1024
DEFAULT_ICE_SERVERS = ("stun:stun.cloudflare.com:3478",)
_FRAME_TYPES = frozenset({"secdogie/direct/v1", "secdogie/direct/v2"})
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _require():
    try:
        import aiortc  # noqa: F401
        import websockets  # noqa: F401
    except ImportError as e:  # pragma: no cover - depends on the install
        raise ImportError("the WebRTC link needs the [webrtc] extra: "
                          "pip install 'secdogie-transport[webrtc]'") from e


def check_signal_url(url: str) -> str:
    """``wss:`` anywhere, ``ws:`` only to this machine. Returns the URL."""
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    if parts.scheme == "wss" and host:
        return url
    if parts.scheme == "ws" and (host in _LOCAL_HOSTS or _is_loopback(host)):
        return url
    raise ValueError("the signaling URL must be wss:// (ws:// only to localhost)")


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class WebRTCConfig:
    signal_url: str
    room: str
    origin: str | None = None  # the Origin header the gateway's ALLOWED_ORIGINS checks
    ice_servers: tuple[str, ...] = DEFAULT_ICE_SERVERS
    bind_timeout: float = BIND_TIMEOUT

    def __post_init__(self):
        check_signal_url(self.signal_url)
        if not ROOM_PATTERN.match(self.room or ""):
            raise ValueError("room must be 1-64 characters of [A-Za-z0-9_-]")


class LinkPolicy(Protocol):
    """Decides whom a link belongs to. Called on the policy pool, one call at a
    time per link; may block (verify, enroll, wait for a person)."""

    def on_open(self, link: LinkHandle) -> None: ...

    def on_text(self, link: LinkHandle, msg: dict) -> None: ...


class LinkHandle:
    """What a policy sees of one link: its id, room and both fingerprint sets,
    and three thread-safe actions -- send a statement, bind the link to a DID,
    close it."""

    def __init__(self, channel: WebRTCChannel, link: _Link):
        self._channel = channel
        self._link = link
        self.link_id = link.link_id
        self.room = channel.config.room
        self.local_fingerprints: tuple[str, ...] = link.local_fps
        self.remote_fingerprints: tuple[str, ...] = link.remote_fps
        self.policy_lock = threading.Lock()
        self.pending: collections.deque = collections.deque()
        self.draining = False

    @property
    def bound_did(self) -> str | None:
        return self._link.bound_did

    @property
    def is_open(self) -> bool:
        return not self._link.closed

    def send_text(self, obj: dict) -> None:
        text = json.dumps(obj, separators=(",", ":"))
        if len(text.encode("utf-8")) > MAX_CONTROL_TEXT:
            raise ValueError("a link statement is at most 8 KiB")
        self._channel._call(self._channel._send_text, self._link, text)

    def bind(self, did: str) -> None:
        self._channel._call(self._channel._bind, self._link, did)

    def extend(self, seconds: float) -> None:
        """Give the policy more time before the unbound link is closed (a
        pairing waits for a person)."""
        self._channel._call(self._channel._arm_bind_deadline, self._link, float(seconds))

    def close(self, reason: str = "", *, delay: float = 0.0) -> None:
        """Close the link -- after ``delay`` seconds, so a statement just sent
        (a refusal, a receipt) goes out first."""
        self._channel._call(self._channel._close_link_later, self._link, reason, float(delay))


@dataclass
class _Link:
    link_id: int
    pc: object
    role: str  # "offerer" / "answerer"
    remote_id: str
    channel: object = None
    remote_set: bool = False
    pending_candidates: list = field(default_factory=list)
    local_fps: tuple[str, ...] = ()
    remote_fps: tuple[str, ...] = ()
    bound_did: str | None = None
    texts: int = 0
    unbound_binary: int = 0
    closed: bool = False
    handle: LinkHandle | None = None
    timers: list = field(default_factory=list)


class WebRTCChannel:
    """One room, at most one link at a time, as a ``LinkChannel`` for
    ``CompositeChannel``. ``start(deliver)`` only records where frames go;
    ``open()`` joins the room (the node calls it once it is ready to serve)."""

    def __init__(self, config: WebRTCConfig, policy: LinkPolicy, *, clock=time.monotonic):
        _require()
        self.config = config
        self.policy = policy
        self._clock = clock
        self._deliver: Callable[[bytes, int], None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._recv_thread: threading.Thread | None = None
        self._recv: queue.Queue = queue.Queue(RECV_QUEUE)
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="webrtc-policy")
        self._closing = threading.Event()
        self._ws = None
        self._peer_id: str | None = None
        self._remote_id: str | None = None
        self._link: _Link | None = None
        self._next_link_id = 0
        self._retries = 0
        self._link_times: list[float] = []
        self._main: concurrent.futures.Future | None = None
        self.stats = {"links": 0, "bound": 0, "refused_sdp": 0, "dropped": 0, "signal_connects": 0}
        self.events: list[tuple[str, object]] = []  # recent link events, for tests and logs (bounded)

    # -- LinkChannel -------------------------------------------------------------------

    def start(self, deliver: Callable[[bytes, int], None]) -> None:
        self._deliver = deliver

    def open(self) -> None:
        if self._loop is not None:
            return
        self._loop = asyncio.new_event_loop()
        ready = threading.Event()

        def run_loop():
            asyncio.set_event_loop(self._loop)
            self._loop.call_soon(ready.set)
            self._loop.run_forever()

        self._loop_thread = threading.Thread(target=run_loop, daemon=True, name="webrtc-loop")
        self._loop_thread.start()
        ready.wait(5)
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True, name="webrtc-recv")
        self._recv_thread.start()
        self._main = asyncio.run_coroutine_threadsafe(self._signaling_main(), self._loop)

    def send(self, link_id: int, data: bytes) -> None:
        if len(data) > MAX_DATAGRAM:
            self.stats["dropped"] += 1
            return
        self._call(self._send_binary, link_id, bytes(data))

    def close(self) -> None:
        if self._closing.is_set():
            return
        self._closing.set()
        loop = self._loop
        if loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._aclose(), loop).result(5)
            except Exception:  # noqa: BLE001 - closing must finish whatever happened
                log.debug("webrtc close did not finish cleanly", exc_info=True)
            loop.call_soon_threadsafe(loop.stop)
            if self._loop_thread is not None:
                self._loop_thread.join(5)
        self._recv.put(None)
        if self._recv_thread is not None:
            self._recv_thread.join(5)
        self._pool.shutdown(wait=False, cancel_futures=True)

    # -- plumbing ------------------------------------------------------------------------

    def _call(self, fn, *args) -> None:
        loop = self._loop
        if loop is None or self._closing.is_set():
            return
        loop.call_soon_threadsafe(fn, *args)

    def _event(self, kind: str, detail: object = None) -> None:
        self.events.append((kind, detail))
        del self.events[:-200]

    def _recv_loop(self) -> None:
        while True:
            item = self._recv.get()
            if item is None:
                return
            data, link_id, did = item
            try:
                obj = json.loads(data)
            except (ValueError, UnicodeDecodeError):
                self.stats["dropped"] += 1
                continue
            # Only a direct frame from the DID this link is bound to reaches the
            # transport, which checks the signature, the allowlist and the counter.
            if not isinstance(obj, dict) or obj.get("t") not in _FRAME_TYPES or obj.get("from") != did:
                self.stats["dropped"] += 1
                continue
            deliver = self._deliver
            if deliver is None:
                continue
            try:
                deliver(data, link_id)
            except Exception:  # noqa: BLE001 - one bad frame must not stop the link
                log.exception("delivering a link frame failed")

    # -- signaling -------------------------------------------------------------------------

    async def _signaling_main(self) -> None:
        from websockets.asyncio.client import connect

        delay = RECONNECT_MIN
        url = self.config.signal_url + ("&" if "?" in self.config.signal_url else "?") + urlencode(
            {"room": self.config.room})
        while not self._closing.is_set():
            pinger = None
            try:
                async with connect(url, origin=self.config.origin, max_size=MAX_SIGNAL_BYTES, ping_interval=None,
                                   open_timeout=10, close_timeout=2) as ws:
                    self._ws = ws
                    self.stats["signal_connects"] += 1
                    self._event("signal-open")
                    log.info("joined the signaling room")
                    delay = RECONNECT_MIN
                    pinger = asyncio.ensure_future(self._ping(ws))
                    async for raw in ws:
                        if isinstance(raw, str):
                            await self._on_signal(raw)
                    code = ws.close_code
                    self._event("signal-closed", code)
                    log.info("signaling closed (%s)", code)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - any failure: back off and try again
                self._event("signal-error", type(e).__name__)
                log.info("signaling unavailable (%s); retrying", e)
            finally:
                self._ws = None
                if pinger is not None:
                    pinger.cancel()
            if self._closing.is_set():
                return
            # An open link does not need the gateway; a half-open one does.
            if self._link is not None and not self._link_open(self._link):
                await self._drop_link("signaling lost")
            self._remote_id = None
            jitter = delay * random.uniform(0.8, 1.2)
            await asyncio.sleep(jitter)
            delay = min(RECONNECT_MAX, delay * 2)

    async def _ping(self, ws) -> None:
        while True:
            await asyncio.sleep(PING_INTERVAL)
            await self._signal({"type": "ping"})

    async def _signal(self, msg: dict) -> bool:
        ws = self._ws
        if ws is None:
            return False
        text = json.dumps(msg, separators=(",", ":"))
        if len(text.encode("utf-8")) > MAX_SIGNAL_BYTES:
            log.warning("a %s is over the gateway's 16 KiB limit; not sent", msg.get("type"))
            return False
        try:
            await ws.send(text)
            return True
        except Exception:  # noqa: BLE001 - the reconnect loop notices a dead socket
            return False

    async def _on_signal(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        t = msg.get("type")
        if t == "welcome":
            self._peer_id = msg.get("peerId")
            peers = msg.get("peers") if isinstance(msg.get("peers"), list) else []
            if peers and isinstance(peers[0], str):
                self._remote_id = peers[0]  # we joined second: we offer
                self._retries = 0
                await self._offer()
        elif t == "peer-joined":
            if isinstance(msg.get("peerId"), str):
                self._remote_id = msg["peerId"]
                self._retries = 0
        elif t == "peer-left":
            if msg.get("peerId") == self._remote_id:
                self._remote_id = None
                link = self._link
                if link is not None and not self._link_open(link):
                    await self._drop_link("the other peer left")
        elif t == "offer":
            await self._on_offer(msg.get("from"), msg.get("payload"))
        elif t == "answer":
            await self._on_answer(msg.get("from"), msg.get("payload"))
        elif t == "ice-candidate":
            await self._on_candidate(msg.get("from"), msg.get("payload"))
        elif t == "error":
            self._event("gateway-error", msg.get("code"))
            log.info("gateway error %s: %s", msg.get("code"), msg.get("message"))

    # -- negotiation ------------------------------------------------------------------------

    def _rate_ok(self) -> bool:
        now = float(self._clock())
        self._link_times = [t for t in self._link_times if now - t < 60.0]
        if len(self._link_times) >= LINK_RATE_PER_MIN:
            return False
        self._link_times.append(now)
        return True

    async def _new_link(self, remote_id: str, role: str) -> _Link | None:
        from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection

        await self._drop_link("replaced")
        if not self._rate_ok():
            self._event("rate-limited")
            log.warning("too many new links this minute; ignoring")
            return None
        self._next_link_id += 1
        pc = RTCPeerConnection(RTCConfiguration(iceServers=[RTCIceServer(urls=u) for u in self.config.ice_servers]))
        link = _Link(self._next_link_id, pc, role, remote_id)
        self._link = link
        self.stats["links"] += 1

        @pc.on("datachannel")
        def on_datachannel(channel):
            if self._link is not link or link.channel is not None or channel.label != DATA_CHANNEL_LABEL:
                channel.close()  # exactly one channel, with the one label, opened by the offerer
                return
            self._attach(link, channel)

        @pc.on("connectionstatechange")
        async def on_state():
            if self._link is link and pc.connectionState in ("failed", "closed"):
                await self._link_lost(link, f"connection {pc.connectionState}")

        link.timers.append(self._loop.call_later(NEGOTIATION_TIMEOUT, self._timeout, link, "negotiation timed out"))
        return link

    async def _offer(self) -> None:
        remote = self._remote_id
        if remote is None:
            return
        link = await self._new_link(remote, "offerer")
        if link is None:
            return
        self._attach(link, link.pc.createDataChannel(DATA_CHANNEL_LABEL, ordered=True))
        try:
            await link.pc.setLocalDescription(await link.pc.createOffer())
        except Exception as e:  # noqa: BLE001
            await self._link_lost(link, f"could not offer: {e}")
            return
        if self._link is link:
            d = link.pc.localDescription
            await self._signal({"type": d.type, "to": remote, "payload": {"type": d.type, "sdp": d.sdp}})

    async def _on_offer(self, sender, payload) -> None:
        if not isinstance(sender, str) or not isinstance(payload, dict) or payload.get("type") != "offer":
            return
        sdp = payload.get("sdp")
        if not sdp_is_data_only(sdp):
            self.stats["refused_sdp"] += 1
            self._event("refused-sdp", "offer")
            log.warning("refused an offer that is not a data channel only")
            return
        from aiortc import RTCSessionDescription

        self._remote_id = sender
        link = await self._new_link(sender, "answerer")
        if link is None:
            return
        try:
            await link.pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="offer"))
            link.remote_set = True
            await self._flush_candidates(link)
            await link.pc.setLocalDescription(await link.pc.createAnswer())
        except Exception as e:  # noqa: BLE001
            await self._link_lost(link, f"could not answer: {e}")
            return
        if self._link is link:
            d = link.pc.localDescription
            await self._signal({"type": d.type, "to": sender, "payload": {"type": d.type, "sdp": d.sdp}})

    async def _on_answer(self, sender, payload) -> None:
        link = self._link
        if (link is None or link.role != "offerer" or link.remote_id != sender or not isinstance(payload, dict)
                or link.pc.signalingState != "have-local-offer"):
            return
        sdp = payload.get("sdp")
        if payload.get("type") != "answer" or not sdp_is_data_only(sdp):
            self.stats["refused_sdp"] += 1
            self._event("refused-sdp", "answer")
            await self._link_lost(link, "the answer is not a data channel only")
            return
        from aiortc import RTCSessionDescription

        try:
            await link.pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="answer"))
            link.remote_set = True
            await self._flush_candidates(link)
        except Exception as e:  # noqa: BLE001
            await self._link_lost(link, f"could not apply the answer: {e}")

    async def _on_candidate(self, sender, payload) -> None:
        link = self._link
        if link is None or link.remote_id != sender or not isinstance(payload, dict):
            return
        cand = payload.get("candidate")
        if not isinstance(cand, str) or not cand.strip():
            return  # end of candidates
        if not link.remote_set:
            if len(link.pending_candidates) < 64:
                link.pending_candidates.append(payload)
            return
        await self._add_candidate(link, payload)

    async def _flush_candidates(self, link: _Link) -> None:
        pending, link.pending_candidates = link.pending_candidates, []
        for p in pending:
            await self._add_candidate(link, p)

    async def _add_candidate(self, link: _Link, payload: dict) -> None:
        from aiortc.sdp import candidate_from_sdp

        try:
            text = payload["candidate"]
            c = candidate_from_sdp(text.split(":", 1)[1] if text.startswith("candidate:") else text)
            c.sdpMid = payload.get("sdpMid")
            c.sdpMLineIndex = payload.get("sdpMLineIndex")
            await link.pc.addIceCandidate(c)
        except Exception as e:  # noqa: BLE001 - a bad candidate is just skipped
            log.debug("ignored an ICE candidate: %s", e)

    # -- the data channel and W1 ------------------------------------------------------------

    def _attach(self, link: _Link, channel) -> None:
        link.channel = channel

        @channel.on("open")
        def on_open():
            if self._link is link:
                self._on_channel_open(link)

        @channel.on("close")
        def on_close():
            if self._link is link:
                asyncio.ensure_future(self._link_lost(link, "data channel closed"))

        @channel.on("message")
        def on_message(data):
            if self._link is link:
                self._on_message(link, data)

        if getattr(channel, "readyState", "") == "open":
            self._on_channel_open(link)

    def _on_channel_open(self, link: _Link) -> None:
        if link.handle is not None:
            return
        for t in link.timers:
            t.cancel()
        link.timers.clear()
        try:
            link.local_fps = fingerprints_from_sdp(link.pc.localDescription.sdp)
            link.remote_fps = fingerprints_from_sdp(link.pc.remoteDescription.sdp)
        except (ValueError, AttributeError) as e:
            asyncio.ensure_future(self._link_lost(link, f"no usable fingerprints: {e}"))
            return
        self._retries = 0
        link.handle = LinkHandle(self, link)
        self._arm_bind_deadline(link, self.config.bind_timeout)
        self._event("link-open", link.link_id)
        log.info("link %d open (%s); waiting for its statement", link.link_id, link.role)
        self._policy(link, self.policy.on_open, link.handle)

    def _arm_bind_deadline(self, link: _Link, seconds: float) -> None:
        if link.bound_did is not None or link.closed:
            return
        for t in link.timers:
            t.cancel()
        link.timers[:] = [self._loop.call_later(seconds, self._timeout, link, "not bound in time")]

    def _policy(self, link: _Link, fn, *args) -> None:
        """Run a policy callback on the pool -- in arrival order per link, one at
        a time."""
        handle = link.handle
        with handle.policy_lock:
            handle.pending.append((fn, args))
            if handle.draining:
                return
            handle.draining = True

        def drain():
            while True:
                with handle.policy_lock:
                    if not handle.pending:
                        handle.draining = False
                        return
                    fn, args = handle.pending.popleft()
                if link.closed:
                    continue
                try:
                    fn(*args)
                except Exception:  # noqa: BLE001 - a policy bug closes the link, never the node
                    log.exception("link policy failed; closing link %d", link.link_id)
                    handle.close("policy error")

        try:
            self._pool.submit(drain)
        except RuntimeError:  # the pool is shut down: we are closing
            pass

    def _on_message(self, link: _Link, data) -> None:
        if isinstance(data, str):
            link.texts += 1
            if link.texts > MAX_CONTROL_MESSAGES or len(data) > MAX_CONTROL_TEXT or link.handle is None:
                asyncio.ensure_future(self._link_lost(link, "too many or too large statements"))
                return
            try:
                msg = json.loads(data)
            except ValueError:
                msg = None
            if not isinstance(msg, dict):
                asyncio.ensure_future(self._link_lost(link, "a statement that is not a JSON object"))
                return
            self._policy(link, self.policy.on_text, link.handle, msg)
            return
        if link.bound_did is None:
            link.unbound_binary += 1
            self.stats["dropped"] += 1
            if link.unbound_binary > MAX_UNBOUND_BINARY:
                asyncio.ensure_future(self._link_lost(link, "frames before binding"))
            return
        if len(data) > MAX_DATAGRAM:
            self.stats["dropped"] += 1
            return
        try:
            self._recv.put_nowait((bytes(data), link.link_id, link.bound_did))
        except queue.Full:
            self.stats["dropped"] += 1

    def _bind(self, link: _Link, did: str) -> None:
        if self._link is not link or link.closed or link.bound_did is not None:
            return
        for t in link.timers:
            t.cancel()
        link.timers.clear()
        link.bound_did = did
        self.stats["bound"] += 1
        self._event("bound", (link.link_id, did))
        log.info("link %d bound to %s", link.link_id, did)

    def _send_text(self, link: _Link, text: str) -> None:
        if self._link is link and self._link_open(link):
            link.channel.send(text)

    def _send_binary(self, link_id: int, data: bytes) -> None:
        link = self._link
        if link is None or link.link_id != link_id or link.bound_did is None or not self._link_open(link):
            self.stats["dropped"] += 1
            return
        if (link.channel.bufferedAmount or 0) > BUFFER_HIGH_WATER:
            self.stats["dropped"] += 1  # the session layer retransmits what matters
            return
        link.channel.send(data)

    def _close_link_later(self, link: _Link, reason: str, delay: float) -> None:
        if delay > 0:
            self._loop.call_later(delay, self._close_link_later, link, reason, 0.0)
            return
        if self._link is link:
            asyncio.ensure_future(self._link_lost(link, reason or "closed by policy", reoffer=False))

    def _timeout(self, link: _Link, reason: str) -> None:
        if self._link is link:
            asyncio.ensure_future(self._link_lost(link, reason))

    @staticmethod
    def _link_open(link: _Link) -> bool:
        return link.channel is not None and getattr(link.channel, "readyState", "") == "open"

    # -- link lifecycle -----------------------------------------------------------------------

    async def _drop_link(self, reason: str) -> None:
        link, self._link = self._link, None
        if link is None:
            return
        link.closed = True
        for t in link.timers:
            t.cancel()
        self._event("link-closed", (link.link_id, reason))
        log.info("link %d closed: %s", link.link_id, reason)
        try:
            if link.channel is not None:
                link.channel.close()
            await link.pc.close()
        except Exception:  # noqa: BLE001
            log.debug("closing a peer connection failed", exc_info=True)

    async def _link_lost(self, link: _Link, reason: str, *, reoffer: bool = True) -> None:
        if self._link is not link:
            return
        await self._drop_link(reason)
        if (reoffer and link.role == "offerer" and self._remote_id == link.remote_id
                and self._retries < MAX_RENEGOTIATIONS and not self._closing.is_set()):
            delay = 2 ** self._retries
            self._retries += 1
            self._loop.call_later(delay, lambda: asyncio.ensure_future(self._reoffer()))

    async def _reoffer(self) -> None:
        if self._link is None and self._remote_id is not None and not self._closing.is_set():
            await self._offer()

    async def _aclose(self) -> None:
        await self._drop_link("closing")
        ws = self._ws
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
        if self._main is not None:
            self._main.cancel()


class BindingPolicy:
    """W1 in both directions: each end proves, with its DID, the fingerprints
    it sees; the link is bound to the peer's DID only when the peer's statement
    verifies against ``trust`` and this end's own view.

    ``speak_first`` is the node's side: it states first, and answers a
    statement from a DID that is not on ``trust`` with a signed
    ``not-enrolled`` refusal, or ``admit(did)``'s reason (``busy``). The other
    side -- a page, or a Python App -- reveals nothing until the node's
    statement verifies, and takes a node-signed refusal as final for the link
    (``refusal`` records it). ``refresh()``, when given, is called once before a
    statement from an unknown DID is refused -- the node re-reads its allowlist
    files, so a browser that ``pair`` enrolled a moment ago is not turned away."""

    def __init__(self, identity, trust, *, speak_first: bool, admit: Callable[[str], str | None] | None = None,
                 node_did: str | None = None, refresh: Callable[[], object] | None = None, clock=time.time):
        from secdogie_identity import linkauth

        if not speak_first and not node_did:
            raise ValueError("the listening side must know which node it expects")
        self._la = linkauth
        self.identity = identity
        self.trust = trust
        self.speak_first = speak_first
        self.admit = admit
        self.node_did = node_did
        self.refresh = refresh
        self._clock = clock
        self.refusal: str | None = None
        self.last_failure: str | None = None

    def _statement(self, link: LinkHandle) -> dict:
        return self._la.create_link_binding(self.identity, room=link.room, local=link.local_fingerprints,
                                            remote=link.remote_fingerprints, clock=self._clock)

    def _refuse(self, link: LinkHandle, reason: str) -> None:
        link.send_text(self._la.create_refusal(self.identity, reason=reason, local=link.local_fingerprints,
                                               remote=link.remote_fingerprints, clock=self._clock))
        link.close(reason, delay=0.5)

    def on_open(self, link: LinkHandle) -> None:
        if self.speak_first:
            link.send_text(self._statement(link))

    def on_text(self, link: LinkHandle, msg: dict) -> None:
        la = self._la
        if msg.get("type") == la.REFUSED_TYPE and not self.speak_first:
            r = la.verify_refusal(msg, node_did=self.node_did, observed_local=link.local_fingerprints,
                                  observed_remote=link.remote_fingerprints, clock=self._clock)
            if r.ok:
                self.refusal = r.refusal
                link.close(f"refused: {r.refusal}")
            return
        if link.bound_did is not None:
            return  # nothing is said after binding
        def check():
            return la.verify_link_binding(msg, trust=self.trust, room=link.room, observed_local=link.local_fingerprints,
                                          observed_remote=link.remote_fingerprints, clock=self._clock)

        r = check()
        if not r.ok and r.reason == "signer not trusted" and self.refresh is not None:
            # A browser paired a moment ago by another process: look at the allowlist again
            # before calling it a stranger.
            self.refresh()
            r = check()
        if not r.ok:
            self.last_failure = r.reason
            log.warning("link %d: the peer's statement did not verify: %s", link.link_id, r.reason)
            if self.speak_first and r.reason == "signer not trusted":
                self._refuse(link, "not-enrolled")
            else:
                link.close(r.reason or "statement refused")
            return
        why = self.admit(r.did) if self.admit is not None else None
        if why:
            self._refuse(link, why)
            return
        if not self.speak_first:
            link.send_text(self._statement(link))
        link.bind(r.did)


__all__ = ["WebRTCChannel", "WebRTCConfig", "LinkHandle", "LinkPolicy", "BindingPolicy", "check_signal_url",
           "DATA_CHANNEL_LABEL", "DEFAULT_ICE_SERVERS", "BIND_TIMEOUT"]
