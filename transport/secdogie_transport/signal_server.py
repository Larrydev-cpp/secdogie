"""A signaling gateway you run yourself: the same protocol as the Cloudflare
Worker in webrtc/signaling/worker.js, on a local socket.

Studio runs it so two of your machines on one network can find each other
without any outside service, and the tests run it so a browser and a Python
peer meet through real signaling. Like the Worker, it only relays the three
negotiation messages (offer, answer, ice-candidate) between the two peers of a
room, stamps `from` itself, rebuilds payloads from a whitelist, and stores
nothing. What the two peers then say goes over their own encrypted WebRTC
channel, never through here.

    server = await serve_signaling("127.0.0.1", 0)   # ws://127.0.0.1:<port>/ws?room=...
    server.url            # the address to hand to peers
    await server.close()

Wire protocol (JSON text frames):
    client -> gateway   {type: offer | answer | ice-candidate, to, payload}  /  {type: ping}
    gateway -> client   {type: welcome, peerId, peers}  /  {type: peer-joined | peer-left, peerId}
                        {type: offer | answer | ice-candidate, from, payload}  /  {type: error, code, message}
"""
from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

MAX_MESSAGE_BYTES = 16 * 1024
MAX_PEERS_PER_ROOM = 2
RATE_BURST = 40
RATE_REFILL_PER_SEC = 10
IDLE_TIMEOUT = 60.0
RELAY_TYPES = frozenset({"offer", "answer", "ice-candidate"})
ROOM = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
CLOSE_POLICY, CLOSE_TOO_BIG, CLOSE_UNSUPPORTED, CLOSE_ROOM_FULL, CLOSE_IDLE = 1008, 1009, 1003, 4001, 4002


def sanitize_payload(kind: str, payload):
    """The standard fields only (RTCSessionDescriptionInit / RTCIceCandidateInit), or None."""
    if not isinstance(payload, dict):
        return None
    if kind in ("offer", "answer"):
        if payload.get("type") != kind or not isinstance(payload.get("sdp"), str):
            return None
        return {"type": kind, "sdp": payload["sdp"]}
    if not isinstance(payload.get("candidate"), str):
        return None
    idx = payload.get("sdpMLineIndex")
    return {
        "candidate": payload["candidate"],
        "sdpMid": payload.get("sdpMid") if isinstance(payload.get("sdpMid"), str) else None,
        "sdpMLineIndex": idx if isinstance(idx, int) and not isinstance(idx, bool) else None,
        "usernameFragment": payload.get("usernameFragment") if isinstance(payload.get("usernameFragment"), str)
        else None,
    }


@dataclass
class _Peer:
    id: str
    ws: object
    tokens: float = RATE_BURST
    refilled: float = field(default_factory=time.monotonic)

    def take(self) -> bool:
        now = time.monotonic()
        self.tokens = min(RATE_BURST, self.tokens + (now - self.refilled) * RATE_REFILL_PER_SEC)
        self.refilled = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


class SignalingServer:
    def __init__(self):
        self.rooms: dict[str, dict[str, _Peer]] = {}
        self._server = None
        self.url = ""

    async def _send(self, peer: _Peer, msg: dict) -> None:
        try:
            await peer.ws.send(json.dumps(msg))
        except Exception:  # noqa: BLE001 - a closed socket is the peer's leaving, handled below
            pass

    async def _handler(self, ws) -> None:
        path = getattr(getattr(ws, "request", None), "path", "") or ""
        url = urlparse(path)
        room = (parse_qs(url.query).get("room") or [""])[0]
        if url.path != "/ws" or not ROOM.match(room):
            await ws.close(CLOSE_POLICY, "room must be 1-64 characters of [A-Za-z0-9_-]")
            return
        peers = self.rooms.setdefault(room, {})
        if len(peers) >= MAX_PEERS_PER_ROOM:
            await ws.send(json.dumps({"type": "error", "code": "room-full",
                                      "message": f"room already has {MAX_PEERS_PER_ROOM} peers"}))
            await ws.close(CLOSE_ROOM_FULL, "room full")
            return
        me = _Peer(secrets.token_hex(8), ws)
        others = list(peers)
        peers[me.id] = me
        try:
            await self._send(me, {"type": "welcome", "peerId": me.id, "peers": others})
            for other in others:
                await self._send(peers[other], {"type": "peer-joined", "peerId": me.id})
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), IDLE_TIMEOUT)
                except asyncio.TimeoutError:
                    await ws.close(CLOSE_IDLE, "idle")
                    return
                if not isinstance(raw, str):
                    await ws.close(CLOSE_UNSUPPORTED, "text frames only")
                    return
                if len(raw.encode("utf-8")) > MAX_MESSAGE_BYTES:
                    await ws.close(CLOSE_TOO_BIG, "message too big")
                    return
                if not me.take():
                    await ws.close(CLOSE_POLICY, "rate limit")
                    return
                await self._relay(me, peers, raw)
        except Exception:  # noqa: BLE001 - the socket closed; the peer is gone
            pass
        finally:
            peers.pop(me.id, None)
            if not peers:
                self.rooms.pop(room, None)
            for other in list(peers.values()):
                await self._send(other, {"type": "peer-left", "peerId": me.id})

    async def _relay(self, me: _Peer, peers: dict, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await self._send(me, {"type": "error", "code": "bad-json", "message": "not JSON"})
            return
        if not isinstance(msg, dict):
            return
        kind = msg.get("type")
        if kind == "ping":
            return
        if kind not in RELAY_TYPES:
            await self._send(me, {"type": "error", "code": "bad-type", "message": f"cannot relay {kind!r}"})
            return
        target = peers.get(msg.get("to")) if isinstance(msg.get("to"), str) else None
        payload = sanitize_payload(kind, msg.get("payload"))
        if target is None or target is me or payload is None:
            await self._send(me, {"type": "error", "code": "bad-message", "message": "no such peer, or bad payload"})
            return
        await self._send(target, {"type": kind, "from": me.id, "payload": payload})

    async def start(self, host: str, port: int) -> SignalingServer:
        from websockets.asyncio.server import serve

        self._server = await serve(self._handler, host, port, max_size=MAX_MESSAGE_BYTES * 2)
        sock = next(iter(self._server.sockets))
        bound = sock.getsockname()
        shown = f"[{bound[0]}]" if ":" in bound[0] else bound[0]
        self.url = f"ws://{shown}:{bound[1]}/ws"
        return self

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()


async def serve_signaling(host: str = "127.0.0.1", port: int = 0) -> SignalingServer:
    """Start a gateway on host:port (0 = any free port); `.url` is where peers connect."""
    return await SignalingServer().start(host, port)


def main(argv=None) -> int:
    """``python -m secdogie_transport.signal_server [--host H] [--port P]``: run a
    gateway until stopped; prints its address as one JSON line."""
    import argparse

    p = argparse.ArgumentParser(prog="secdogie-signal", description="A signaling gateway for your own devices.")
    p.add_argument("--host", default="127.0.0.1", help="address to listen on (default: this machine only)")
    p.add_argument("--port", type=int, default=0, help="port (default: any free one)")
    args = p.parse_args(argv)

    async def run():
        server = await serve_signaling(args.host, args.port)
        print(json.dumps({"url": server.url}), flush=True)
        await asyncio.Event().wait()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    return 0


__all__ = ["SignalingServer", "main", "sanitize_payload", "serve_signaling"]

if __name__ == "__main__":
    raise SystemExit(main())
