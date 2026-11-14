"""A WebRTC data channel from Python: the counterpart of webrtc/client/web_peer.js.

One `RtcPeer` joins one room on a signaling gateway (the Cloudflare Worker,
or signal_server.py) and brings up one reliable, ordered data channel
("secdogie-data") with the other peer in it. It speaks exactly the protocol
of web_peer.js, so a Python peer and a browser meet in the same room:

  * the peer that joins second makes the offer; the other answers;
  * offer / answer / ice-candidate go through the gateway with `to`, and come
    back stamped with `from`;
  * the data channel carries text (JSON) messages.

    peer = RtcPeer(signal_url, room)
    await peer.open(timeout=30)          # joined, negotiated, channel open
    peer.fingerprints()                  # (local, remote) DTLS fingerprints, for the bind / hello
    await peer.send('{"t": "..."}')
    text = await peer.recv(timeout=10)
    await peer.close()

Like the browser side, this authenticates nothing: DTLS keeps the channel
private, and the pairing bind / link hello (pairing.py, meshlink.py) prove
who is at each end, over the fingerprints this channel really uses.

Needs the optional dependencies `aiortc` and `websockets`
(pip install 'secdogie-transport[webrtc]').
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re

from .pairing import normalize_fingerprint

log = logging.getLogger("secdogie.transport.rtc")

DATA_CHANNEL_LABEL = "secdogie-data"
DEFAULT_ICE_SERVERS = ("stun:stun.l.google.com:19302",)
PING_INTERVAL = 20.0
_FP_LINE = re.compile(r"^a=fingerprint:(sha-256 [0-9A-Fa-f:]+)\s*$", re.MULTILINE)
_ROOM = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class RtcError(RuntimeError):
    """The channel could not be brought up, or went away."""


def fingerprint_from_sdp(sdp: str | None) -> str:
    """The SHA-256 DTLS fingerprint an SDP carries, normalized ('' if none)."""
    m = _FP_LINE.search(sdp or "")
    return normalize_fingerprint(m.group(1)) if m else ""


def _outgoing_sdp(sdp: str) -> str:
    """The SDP as sent: aiortc lists sha-256, sha-384 and sha-512 fingerprints
    of its certificate, and a browser keeps only the strongest one. The bind and
    the hello sign the sha-256 one, so that is the only one sent: the browser
    then checks the DTLS certificate against exactly what was signed."""
    lines = sdp.split("\r\n")
    kept = [ln for ln in lines if not ln.startswith("a=fingerprint:") or ln.startswith("a=fingerprint:sha-256 ")]
    return "\r\n".join(kept)


def _endpoint(signal_url: str, room: str) -> str:
    if not signal_url.startswith(("ws://", "wss://")):
        raise ValueError("the signaling address is a ws:// or wss:// URL")
    if not _ROOM.match(room):
        raise ValueError("the room is 1-64 letters, digits, - or _")
    sep = "&" if "?" in signal_url else "?"
    return f"{signal_url}{sep}room={room}"


class RtcPeer:
    def __init__(self, signal_url: str, room: str, *, ice_servers=DEFAULT_ICE_SERVERS):
        self.url = _endpoint(signal_url, room)
        self.room = room
        self.ice_servers = tuple(ice_servers)
        self._ws = None
        self._pc = None
        self._channel = None
        self._remote_id: str | None = None
        self._open = asyncio.Event()
        self._closed = asyncio.Event()
        self._inbox: asyncio.Queue = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []
        self._pending_candidates: list[dict] = []
        self._error: str | None = None

    # ---- what callers use -----------------------------------------------------------

    async def open(self, timeout: float = 45.0) -> RtcPeer:
        """Join the room and wait until the data channel is open."""
        from websockets.asyncio.client import connect

        try:
            self._ws = await asyncio.wait_for(connect(self.url, max_size=64 * 1024), timeout)
        except Exception as e:  # noqa: BLE001 - reported as one kind of failure
            raise RtcError(f"cannot reach the signaling gateway: {e}") from e
        self._tasks.append(asyncio.create_task(self._signal_loop()))
        self._tasks.append(asyncio.create_task(self._ping_loop()))
        opened = asyncio.create_task(self._open.wait())
        closed = asyncio.create_task(self._closed.wait())
        done, _ = await asyncio.wait({opened, closed}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        for t in (opened, closed):
            t.cancel()
        if not self._open.is_set():
            reason = self._error or ("the other device did not come" if not done else "the link closed")
            await self.close()
            raise RtcError(reason)
        return self

    @property
    def is_open(self) -> bool:
        return self._open.is_set() and not self._closed.is_set()

    def fingerprints(self) -> tuple[str, str]:
        """(local, remote) SHA-256 DTLS fingerprints of this connection."""
        pc = self._pc
        if pc is None or pc.localDescription is None or pc.remoteDescription is None:
            return "", ""
        return fingerprint_from_sdp(pc.localDescription.sdp), fingerprint_from_sdp(pc.remoteDescription.sdp)

    async def send(self, text: str) -> None:
        if not self.is_open or self._channel is None or self._channel.readyState != "open":
            raise RtcError("no open data channel")
        self._channel.send(text)

    async def recv(self, timeout: float | None = None) -> str:
        """The next text message; raises RtcError when the link is gone."""
        if self._closed.is_set() and self._inbox.empty():
            raise RtcError(self._error or "the link closed")
        get = asyncio.create_task(self._inbox.get())
        closed = asyncio.create_task(self._closed.wait())
        done, _ = await asyncio.wait({get, closed}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        if get in done:
            closed.cancel()
            return get.result()
        get.cancel()
        closed.cancel()
        if closed in done:
            raise RtcError(self._error or "the link closed")
        raise asyncio.TimeoutError()

    async def close(self, linger: float = 1.0) -> None:
        """Close, after giving what was already sent up to `linger` seconds to
        leave: a last message (a cancel, a receipt) must not die in the buffer."""
        channel = self._channel
        if channel is not None and channel.readyState == "open" and not self._closed.is_set() and linger > 0:
            end = asyncio.get_running_loop().time() + linger
            with contextlib.suppress(Exception):
                while channel.bufferedAmount > 0 and asyncio.get_running_loop().time() < end:
                    await asyncio.sleep(0.02)
                await asyncio.sleep(min(0.3, linger))  # out of the channel is not yet on the wire
        self._closed.set()
        for t in self._tasks:
            t.cancel()
        with contextlib.suppress(Exception):
            if self._channel is not None:
                self._channel.close()
        with contextlib.suppress(Exception):
            if self._pc is not None:
                await self._pc.close()
        with contextlib.suppress(Exception):
            if self._ws is not None:
                await self._ws.close()

    # ---- signaling ------------------------------------------------------------------

    async def _signal(self, msg: dict) -> None:
        with contextlib.suppress(Exception):
            await self._ws.send(json.dumps(msg))

    async def _ping_loop(self) -> None:
        while True:
            await asyncio.sleep(PING_INTERVAL)
            await self._signal({"type": "ping"})

    async def _signal_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue
                await self._handle(msg if isinstance(msg, dict) else {})
        except Exception as e:  # noqa: BLE001
            log.debug("signaling ended: %s", e)
        finally:
            # an open data channel no longer needs the gateway
            if not self._open.is_set():
                self._error = self._error or "the signaling gateway closed"
                self._closed.set()

    async def _handle(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "welcome":
            peers = msg.get("peers") if isinstance(msg.get("peers"), list) else []
            if peers:
                self._remote_id = peers[0]  # we joined second: we offer
                await self._offer()
        elif kind == "peer-joined":
            self._remote_id = msg.get("peerId")
        elif kind == "peer-left":
            if msg.get("peerId") == self._remote_id and not self._open.is_set():
                self._error = "the other device left"
        elif kind == "offer":
            self._remote_id = msg.get("from")
            await self._answer(msg.get("payload") or {})
        elif kind == "answer" and self._pc is not None and msg.get("from") == self._remote_id:
            from aiortc import RTCSessionDescription

            p = msg.get("payload") or {}
            with contextlib.suppress(Exception):
                await self._pc.setRemoteDescription(RTCSessionDescription(sdp=p.get("sdp", ""), type="answer"))
                await self._flush_candidates()
        elif kind == "ice-candidate" and msg.get("from") == self._remote_id:
            self._pending_candidates.append(msg.get("payload") or {})
            await self._flush_candidates()
        elif kind == "error":
            self._error = f"gateway: {msg.get('message', msg.get('code'))}"
            if msg.get("code") == "room-full":
                self._closed.set()

    async def _flush_candidates(self) -> None:
        from aiortc.sdp import candidate_from_sdp

        pc = self._pc
        if pc is None or pc.remoteDescription is None:
            return
        while self._pending_candidates:
            c = self._pending_candidates.pop(0)
            text = c.get("candidate") or ""
            if not text:
                continue  # end of candidates
            try:
                cand = candidate_from_sdp(text.split(":", 1)[1] if text.startswith("candidate:") else text)
                cand.sdpMid = c.get("sdpMid")
                cand.sdpMLineIndex = c.get("sdpMLineIndex")
                await pc.addIceCandidate(cand)
            except Exception as e:  # noqa: BLE001 - one bad candidate is not the link
                log.debug("ignored a candidate: %s", e)

    # ---- negotiation ----------------------------------------------------------------

    def _new_pc(self):
        from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection

        cfg = RTCConfiguration(iceServers=[RTCIceServer(urls=u) for u in self.ice_servers])
        pc = RTCPeerConnection(configuration=cfg)
        self._pc = pc

        @pc.on("datachannel")
        def _on_channel(channel):
            if self._channel is None:
                self._attach(channel)

        @pc.on("connectionstatechange")
        async def _on_state():
            if pc.connectionState in ("failed", "closed"):
                self._error = self._error or f"connection {pc.connectionState}"
                self._closed.set()

        return pc

    def _attach(self, channel) -> None:
        self._channel = channel

        def opened():
            self._open.set()

        if channel.readyState == "open":
            opened()
        else:
            channel.on("open", opened)

        @channel.on("message")
        def _on_message(message):
            if isinstance(message, str):
                self._inbox.put_nowait(message)

        @channel.on("close")
        def _on_close():
            self._error = self._error or "the data channel closed"
            self._closed.set()

    async def _offer(self) -> None:
        pc = self._new_pc()
        self._attach(pc.createDataChannel(DATA_CHANNEL_LABEL, ordered=True))
        await pc.setLocalDescription(await pc.createOffer())  # aiortc gathers every candidate first
        await self._signal({"type": "offer", "to": self._remote_id,
                            "payload": {"type": "offer", "sdp": _outgoing_sdp(pc.localDescription.sdp)}})

    async def _answer(self, payload: dict) -> None:
        from aiortc import RTCSessionDescription

        if self._pc is not None:
            with contextlib.suppress(Exception):
                await self._pc.close()
            self._channel = None
        pc = self._new_pc()
        await pc.setRemoteDescription(RTCSessionDescription(sdp=payload.get("sdp", ""), type="offer"))
        await self._flush_candidates()
        await pc.setLocalDescription(await pc.createAnswer())
        await self._signal({"type": "answer", "to": self._remote_id,
                            "payload": {"type": "answer", "sdp": _outgoing_sdp(pc.localDescription.sdp)}})


__all__ = ["DATA_CHANNEL_LABEL", "RtcError", "RtcPeer", "fingerprint_from_sdp"]
