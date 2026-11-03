"""A local stand-in for the Cloudflare Worker signaling gateway
(webrtc/signaling/worker.js), speaking the same protocol: rooms of at most two
peers, `welcome {peerId, peers}`, `peer-joined`, `peer-left`, relayed
`offer` / `answer` / `ice-candidate` with `from` stamped by the server, `ping`
ignored, `error` for anything else. Tests use it instead of `wrangler dev`."""
from __future__ import annotations

import asyncio
import json
import threading
import uuid
from urllib.parse import parse_qs, urlparse

import websockets

RELAY = {"offer", "answer", "ice-candidate"}


class SignalingStub:
    def __init__(self, host: str = "127.0.0.1"):
        self.host = host
        self.rooms: dict[str, dict[str, object]] = {}
        self.relayed = 0
        self._conns: set = set()
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="signaling-stub")
        self._thread.start()
        self._ready.wait(10)

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}/ws"

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)

        async def start():
            self._server = await websockets.serve(self._handle, self.host, 0)
            self.port = self._server.sockets[0].getsockname()[1]
            self._ready.set()

        self._loop.run_until_complete(start())
        self._loop.run_forever()

    async def _handle(self, ws) -> None:
        path = ws.request.path if hasattr(ws, "request") else ws.path
        room = (parse_qs(urlparse(path).query).get("room") or [""])[0]
        peers = self.rooms.setdefault(room, {})
        if len(peers) >= 2:
            await ws.send(json.dumps({"type": "error", "code": "room-full", "message": "room full"}))
            await ws.close(4001, "room full")
            return
        me = str(uuid.uuid4())
        others = list(peers)
        peers[me] = ws
        self._conns.add(ws)
        await ws.send(json.dumps({"type": "welcome", "peerId": me, "peers": others}))
        for other in others:
            await self._send(peers.get(other), {"type": "peer-joined", "peerId": me})
        try:
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("type") == "ping":
                    continue
                target = peers.get(msg.get("to"))
                if msg.get("type") not in RELAY or target is None or target is ws:
                    await ws.send(json.dumps({"type": "error", "code": "bad-message", "message": "?"}))
                    continue
                self.relayed += 1
                await self._send(target, {"type": msg["type"], "from": me, "payload": msg.get("payload")})
        except websockets.ConnectionClosed:
            pass
        finally:
            self._conns.discard(ws)
            peers.pop(me, None)
            for other in list(peers.values()):
                await self._send(other, {"type": "peer-left", "peerId": me})
            if not peers:
                self.rooms.pop(room, None)

    @staticmethod
    async def _send(ws, msg) -> None:
        if ws is not None:
            try:
                await ws.send(json.dumps(msg))
            except websockets.ConnectionClosed:
                pass

    def close(self) -> None:
        async def stop():
            for ws in list(self._conns):
                await ws.close()
            self._server.close()
            await asyncio.wait_for(self._server.wait_closed(), 5)

        asyncio.run_coroutine_threadsafe(stop(), self._loop).result(10)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(5)
