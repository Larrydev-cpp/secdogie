"""WebRTC DataChannel carrier for the inference mesh: ``RtcWire``.

Optional (``pip install 'secdogie-inference[webrtc]'``: aiortc + websockets).
It lets mesh nodes -- and, above all, browser nodes -- reach each other over
DTLS-encrypted DataChannels, using the existing Cloudflare Worker signaling
gateway (``webrtc/signaling/worker.js``) unchanged.

**Lobbies.** The gateway pairs exactly two peers per room, so each listening
node keeps one *lobby* room, named from its DID (``lobby_room``). A node that
wants a link joins the target's lobby and makes the offer; the listener
answers, completes the handshake, leaves the lobby and opens it again for the
next peer. Dialers always offer and listeners always answer, so there is no
glare.

**DID ↔ DTLS binding.** The gateway is not trusted: it could swap SDPs and sit
in the middle of the DTLS session. So once the channel opens, each side sends a
signed ``secdogie/inference/rtc-hello/v1``:

    {did, room, local_fp, remote_fp, issued_at [, admission]}

and accepts the peer only if the signature is the DID's own, the DID is on the
mesh/user trust set (or the hello carries a valid operator admission for it,
which is then published to the mesh), the room is this one, ``issued_at`` is
fresh, and **both fingerprints match the negotiated SDPs** -- the peer's
``local_fp`` is the DTLS fingerprint this side actually saw, and its
``remote_fp`` is this side's own. DTLS fingerprints are unique per connection,
so a hello cannot be replayed onto another one, and a gateway in the middle
would present different fingerprints than the ones the DIDs signed. Only after
that are frames delivered to the participant as coming from that DID.

**One connection per pair.** If both sides dial at once, both keep the
connection dialed by the lexicographically smaller DID and close the other,
so the two ends never disagree about which channel to use.

``FailoverWire`` puts this in front of the UDP ``MuxWire``: a frame goes over a
bound DataChannel when there is one, otherwise over UDP.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
import time
from urllib.parse import urlencode

from secdogie_identity import sign_payload, verify_payload

from .admission import check_admission
from .protocol import MAX_SKEW, is_did, is_num

log = logging.getLogger("secdogie_inference")

RTC_HELLO = "secdogie/inference/rtc-hello/v1"
CHANNEL_LABEL = "secdogie-inference"
HANDSHAKE_TIMEOUT = 20.0
REDIAL_FIRST, REDIAL_MAX = 2.0, 30.0   # backoff for a peer whose dial failed (busy lobby, peer offline)
_FINGERPRINT = re.compile(r"^a=fingerprint:(\S+)\s+([0-9A-Fa-f:]+)\s*$", re.M)


class _AnySigner:
    def contains(self, did: str) -> bool:
        return True


def lobby_room(did: str, salt: str = "") -> str:
    """The signaling room where ``did`` waits for peers: [a-z0-9-], 44 chars."""
    return "sdl-" + hashlib.sha256(f"lobby|{did}|{salt}".encode()).hexdigest()[:40]


def sdp_fingerprint(sdp: str) -> str:
    """``algorithm HEX:HEX…`` from an SDP's first ``a=fingerprint`` line, normalized."""
    m = _FINGERPRINT.search(sdp or "")
    if m is None:
        raise ValueError("SDP without a DTLS fingerprint")
    return f"{m.group(1).lower()} {m.group(2).upper()}"


def make_hello(identity, *, room: str, local_fp: str, remote_fp: str, admission: dict | None = None,
               clock=time.time) -> dict:
    body = {"type": RTC_HELLO, "did": identity.did, "room": room, "local_fp": local_fp, "remote_fp": remote_fp,
            "issued_at": float(clock())}
    if admission is not None:
        body["admission"] = admission
    return sign_payload(identity, body)


def check_hello(obj, trust, *, room: str, my_fp: str, peer_fp: str, now: float) -> tuple[str, dict | None]:
    """Verify a peer's hello against this connection. Returns ``(did, admission)``
    where ``admission`` is the operator admission that made an otherwise unknown
    DID acceptable (to be published), or raises ``ValueError``."""
    if not isinstance(obj, dict) or obj.get("type") != RTC_HELLO:
        raise ValueError("not an rtc-hello")
    ok, signer = verify_payload(obj, _AnySigner())
    did = obj.get("did")
    if not ok or not is_did(did) or signer != did:
        raise ValueError("bad hello signature")
    if obj.get("room") != room:
        raise ValueError("hello for another room")
    if obj.get("local_fp") != peer_fp or obj.get("remote_fp") != my_fp:
        raise ValueError("hello does not match this connection's DTLS fingerprints")
    if not is_num(obj.get("issued_at")) or abs(obj["issued_at"] - now) > MAX_SKEW:
        raise ValueError("stale or future hello")
    if trust.peers.contains(did):
        return did, None
    admission = obj.get("admission")
    if isinstance(admission, dict) and admission.get("subject") == did \
            and check_admission(admission, trust, now=now) is None:
        return did, admission
    raise ValueError("peer not trusted and not admitted")


class _Link:
    def __init__(self, pc, channel, dialer: str):
        self.pc, self.channel, self.dialer = pc, channel, dialer


class RtcWire:
    """A ``Wire`` over WebRTC DataChannels. Thread-safe: it runs its own asyncio
    loop on a daemon thread; ``send`` / ``connect`` may be called from any
    thread."""

    def __init__(self, identity, trust, signaling_url: str, *, salt: str = "", listen: bool = True,
                 ice_servers=(), admission: dict | None = None, on_admission=None, clock=time.time):
        import aiortc  # noqa: F401 - fail at construction when the extra is not installed

        self.identity = identity
        self.did = identity.did
        self.trust = trust
        self.url = signaling_url
        self.salt = salt
        self.listen = listen
        self.ice_servers = list(ice_servers)
        self.admission = admission          # this node's own admission, presented in its hellos
        self.on_admission = on_admission    # called with an admission a peer presented; True to accept
        self.clock = clock
        self._participant = None
        self._links: dict[str, _Link] = {}
        self._dialing: set[str] = set()
        self._redial: dict[str, tuple[float, float]] = {}  # did -> (not before, next delay)
        self._lock = threading.Lock()
        self._closed = False
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True, name="rtc-wire")
        self._thread.start()
        self._tasks: set = set()
        self._closing: set = set()   # pc.close() tasks: awaited, never cancelled, at shutdown
        if listen:
            self._spawn(self._listen_forever())

    # -- the Wire interface ------------------------------------------------------------

    def attach(self, participant) -> None:
        self._participant = participant

    def send(self, to_did: str, payload: bytes) -> bool:
        with self._lock:
            link = self._links.get(to_did)
        if link is None or link.channel.readyState != "open":
            self._maybe_redial(to_did)  # the participant still wants this peer: try again, with backoff
            return False
        self._loop.call_soon_threadsafe(self._send_now, link, bytes(payload))
        return True

    def connect(self, did: str) -> None:
        if did == self.did or self._closed:
            return
        with self._lock:
            if did in self._links or did in self._dialing:
                return
            self._dialing.add(did)
        self._spawn(self._dial(did))

    def _maybe_redial(self, did: str) -> None:
        with self._lock:
            not_before, _delay = self._redial.get(did, (0.0, REDIAL_FIRST))
        if time.monotonic() >= not_before:
            self.connect(did)

    def peers(self) -> set[str]:
        with self._lock:
            return {d for d, link in self._links.items() if link.channel.readyState == "open"}

    def close(self) -> None:
        self._closed = True
        fut = asyncio.run_coroutine_threadsafe(self._close_all(), self._loop)
        try:
            fut.result(10)
        except Exception:  # noqa: BLE001 - best effort on shutdown
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(5)

    # -- internals ----------------------------------------------------------------------

    def _spawn(self, coro) -> None:
        def start():
            task = self._loop.create_task(coro)
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

        self._loop.call_soon_threadsafe(start)

    @staticmethod
    def _send_now(link: _Link, payload: bytes) -> None:
        if link.channel.readyState == "open":
            link.channel.send(payload)

    def _close_later(self, pc) -> None:
        task = self._loop.create_task(pc.close())
        self._closing.add(task)
        task.add_done_callback(self._closing.discard)

    async def _close_all(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)  # let each close its socket and connection
        if self._closing:
            await asyncio.wait(list(self._closing), timeout=5)
        with self._lock:
            links = list(self._links.values())
            self._links.clear()
        for link in links:
            await link.pc.close()

    def _pc(self):
        from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection

        return RTCPeerConnection(RTCConfiguration(iceServers=[RTCIceServer(u) for u in self.ice_servers]))

    async def _signal(self, room: str):
        import websockets

        sep = "&" if "?" in self.url else "?"
        return await websockets.connect(f"{self.url}{sep}{urlencode({'room': room})}", max_size=1 << 16)

    async def _dial(self, did: str) -> None:
        room = lobby_room(did, self.salt)
        made: list = []

        async def attempt() -> None:
            from aiortc import RTCSessionDescription

            ws = await self._signal(room)
            try:
                peer_id = await self._await_peer(ws)
                pc = self._pc()
                made.append(pc)
                channel = pc.createDataChannel(CHANNEL_LABEL, ordered=True)
                self._buffer(channel)
                await pc.setLocalDescription(await pc.createOffer())
                await ws.send(json.dumps({"type": "offer", "to": peer_id, "payload": {
                    "type": "offer", "sdp": pc.localDescription.sdp}}))
                answer = await self._await_relay(ws, pc, "answer")
                await pc.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type="answer"))
                await self._await_open(channel)
            finally:
                await ws.close()
            peer = await self._handshake(pc, channel, room)
            if peer != did:
                raise ValueError("the lobby was answered by another DID")
            self._bind(peer, pc, channel, dialer=self.did)
            made.clear()

        try:
            await asyncio.wait_for(attempt(), HANDSHAKE_TIMEOUT)
            with self._lock:
                self._redial.pop(did, None)
        except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001 - a failed dial is retried later
            log.info("rtc dial %s failed: %s", did[-8:], exc)
            with self._lock:
                _not_before, delay = self._redial.get(did, (0.0, REDIAL_FIRST))
                self._redial[did] = (time.monotonic() + delay, min(delay * 2, REDIAL_MAX))
        finally:
            with self._lock:
                self._dialing.discard(did)
            for pc in made:
                await pc.close()

    async def _listen_forever(self) -> None:
        room = lobby_room(self.did, self.salt)
        backoff = 1.0
        while not self._closed:
            try:
                await self._accept_one(room)
                backoff = 1.0
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001 - keep the lobby open
                log.info("rtc lobby: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _accept_one(self, room: str) -> None:
        from aiortc import RTCSessionDescription

        ws = await self._signal(room)
        pc = None
        try:
            offer = None
            while offer is None:  # wait (indefinitely) for a dialer, keeping the socket alive
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), 20.0))
                except asyncio.TimeoutError:
                    await ws.send(json.dumps({"type": "ping"}))
                    continue
                if msg.get("type") == "offer":
                    offer = msg
            pc = self._pc()

            async def answer() -> None:
                opened: asyncio.Future = asyncio.get_running_loop().create_future()

                @pc.on("datachannel")
                def on_channel(channel):
                    if channel.label == CHANNEL_LABEL and not opened.done():
                        self._buffer(channel)
                        opened.set_result(channel)

                await pc.setRemoteDescription(RTCSessionDescription(sdp=offer["payload"]["sdp"], type="offer"))
                await pc.setLocalDescription(await pc.createAnswer())
                await ws.send(json.dumps({"type": "answer", "to": offer["from"], "payload": {
                    "type": "answer", "sdp": pc.localDescription.sdp}}))
                channel = await opened
                await self._await_open(channel)
                await ws.close()
                peer = await self._handshake(pc, channel, room)
                self._bind(peer, pc, channel, dialer=peer)

            await asyncio.wait_for(answer(), HANDSHAKE_TIMEOUT)
            pc = None
        finally:
            await ws.close()
            if pc is not None:
                await pc.close()

    async def _await_peer(self, ws) -> str:
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("type") == "welcome" and msg.get("peers"):
                return msg["peers"][0]
            if msg.get("type") == "peer-joined":
                return msg["peerId"]
            if msg.get("type") == "error":
                raise ValueError(f"signaling: {msg.get('code')}")

    async def _await_relay(self, ws, pc, kind: str) -> dict:
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("type") == kind:
                return msg["payload"]
            if msg.get("type") == "ice-candidate":
                await self._add_candidate(pc, msg.get("payload") or {})
            if msg.get("type") in ("peer-left", "error"):
                raise ValueError(f"signaling: {msg.get('type')}")

    @staticmethod
    async def _add_candidate(pc, payload: dict) -> None:
        from aiortc.sdp import candidate_from_sdp

        cand = payload.get("candidate") or ""
        if not cand:
            return
        candidate = candidate_from_sdp(cand.split(":", 1)[1] if cand.startswith("candidate:") else cand)
        candidate.sdpMid = payload.get("sdpMid")
        candidate.sdpMLineIndex = payload.get("sdpMLineIndex")
        await pc.addIceCandidate(candidate)

    @staticmethod
    def _buffer(channel) -> None:
        """Queue every message from the moment the channel exists: the peer's
        hello (or its first frames) may arrive before this side is ready."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        channel._sd_queue = queue

        @channel.on("message")
        def enqueue(message):
            if not queue.full():
                queue.put_nowait(message)

    @staticmethod
    async def _await_open(channel) -> None:
        if channel.readyState == "open":
            return
        opened = asyncio.get_running_loop().create_future()
        channel.on("open", lambda: opened.done() or opened.set_result(True))
        await opened

    async def _handshake(self, pc, channel, room: str) -> str:
        my_fp = sdp_fingerprint(pc.localDescription.sdp)
        peer_fp = sdp_fingerprint(pc.remoteDescription.sdp)
        hello = make_hello(self.identity, room=room, local_fp=my_fp, remote_fp=peer_fp, admission=self.admission,
                           clock=self.clock)
        channel.send(json.dumps(hello))
        raw = await asyncio.wait_for(channel._sd_queue.get(), HANDSHAKE_TIMEOUT)
        obj = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
        did, admission = check_hello(obj, self.trust, room=room, my_fp=my_fp, peer_fp=peer_fp, now=float(self.clock()))
        if admission is not None and not (self.on_admission is not None and self.on_admission(admission)):
            if not self.trust.peers.contains(did):
                raise ValueError("the peer's admission was not accepted")
        return did

    def _bind(self, did: str, pc, channel, *, dialer: str) -> None:
        keep_dialer = min(self.did, did)  # both ends keep the connection the smaller DID dialed
        with self._lock:
            held = self._links.get(did)
            if held is not None and held.channel.readyState == "open" and held.dialer == keep_dialer:
                self._close_later(pc)
                return
            self._links[did] = _Link(pc, channel, dialer)
        if held is not None:
            self._close_later(held.pc)

        channel.remove_all_listeners("message")

        def on_message(message):
            data = message.encode("utf-8") if isinstance(message, str) else bytes(message)
            participant = self._participant
            if participant is not None:
                try:
                    participant.handle(did, data)
                except Exception:  # noqa: BLE001 - one bad frame must not close the link
                    log.exception("frame from %s failed", did[-8:])

        queued = getattr(channel, "_sd_queue", None)
        while queued is not None and not queued.empty():
            on_message(queued.get_nowait())  # frames that arrived during the handshake
        channel.on("message", on_message)

        @channel.on("close")
        def on_close():
            with self._lock:
                if self._links.get(did) is not None and self._links[did].channel is channel:
                    del self._links[did]

        log.info("rtc link to %s bound (dialer %s)", did[-8:], dialer[-8:])


class FailoverWire:
    """Send over ``primary`` (WebRTC) when it has a link, else over ``secondary``
    (UDP). Both deliver inbound frames to the same participant."""

    def __init__(self, primary, secondary):
        self.primary, self.secondary = primary, secondary

    def attach(self, participant) -> None:
        self.primary.attach(participant)
        self.secondary.attach(participant)

    def send(self, to_did: str, payload: bytes) -> bool:
        return self.primary.send(to_did, payload) or self.secondary.send(to_did, payload)

    def connect(self, did: str) -> None:
        self.secondary.connect(did)
        self.primary.connect(did)


__all__ = ["RtcWire", "FailoverWire", "lobby_room", "sdp_fingerprint", "make_hello", "check_hello", "RTC_HELLO"]
