"""An in-process stand-in for the signaling gateway (``webrtc/signaling/worker.js``),
for tests: same wire protocol, same room rules, on 127.0.0.1. Needs the
``[webrtc]`` extra (websockets).

  * at most two peers a room; a third gets ``error room-full`` and close 4001;
  * ``welcome {peerId, peers}``, ``peer-joined``, ``peer-left``;
  * relays only offer / answer / ice-candidate, rebuilt from the standard
    fields, with ``from`` stamped by the server; ``ping`` is accepted silently;
  * 16 KiB text frames; an Origin outside ``allowed_origins`` (when given) is
    refused at the handshake.

``rewrite(msg)`` sees every relayed message and may return a changed copy or
None (drop it) -- the hook a man-in-the-middle test uses.
"""
from __future__ import annotations

import asyncio
import json
import threading
import uuid
from collections.abc import Callable
from urllib.parse import parse_qs, urlsplit

MAX_MESSAGE_BYTES = 16 * 1024
MAX_PEERS = 2
CLOSE_ROOM_FULL = 4001
RELAY_TYPES = frozenset({"offer", "answer", "ice-candidate"})


def _sanitize(t: str, payload):
    if not isinstance(payload, dict):
        return None
    if t in ("offer", "answer"):
        if payload.get("type") != t or not isinstance(payload.get("sdp"), str):
            return None
        return {"type": t, "sdp": payload["sdp"]}
    if not isinstance(payload.get("candidate"), str):
        return None
    mline = payload.get("sdpMLineIndex")
    return {
        "candidate": payload["candidate"],
        "sdpMid": payload.get("sdpMid") if isinstance(payload.get("sdpMid"), str) else None,
        "sdpMLineIndex": mline if isinstance(mline, int) and not isinstance(mline, bool) else None,
        "usernameFragment": payload.get("usernameFragment") if isinstance(payload.get("usernameFragment"), str)
        else None,
    }


class FakeSignalingServer:
    def __init__(self, *, allowed_origins: tuple[str, ...] = (), rewrite: Callable[[dict], dict | None] | None = None):
        self.allowed_origins = tuple(allowed_origins)
        self.rewrite = rewrite
        self.rooms: dict[str, dict[str, object]] = {}
        self.relayed: list[dict] = []
        self.joins: list[tuple[str, str]] = []  # (room, peer id)
        self.refused_full = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server = None
        self.url: str | None = None

    def start(self) -> str:
        from websockets.asyncio.server import serve

        self._loop = asyncio.new_event_loop()
        started = threading.Event()

        async def boot():
            self._server = await serve(self._handle, "127.0.0.1", 0, max_size=MAX_MESSAGE_BYTES,
                                       process_request=self._check, ping_interval=None)
            port = self._server.sockets[0].getsockname()[1]
            self.url = f"ws://127.0.0.1:{port}/ws"
            started.set()

        def run():
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(boot())
            self._loop.run_forever()

        self._thread = threading.Thread(target=run, daemon=True, name="fake-signaling")
        self._thread.start()
        if not started.wait(5):
            raise RuntimeError("the fake signaling server did not start")
        return self.url

    def stop(self) -> None:
        if self._loop is None:
            return

        async def down():
            self._server.close()
            await self._server.wait_closed()

        try:
            asyncio.run_coroutine_threadsafe(down(), self._loop).result(5)
        except Exception:  # noqa: BLE001
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(5)

    def kick(self, room: str) -> None:
        """Close every socket in ``room`` (a gateway restart, say)."""

        async def go():
            for ws in list(self.rooms.get(room, {}).values()):
                await ws.close(1012, "restart")

        asyncio.run_coroutine_threadsafe(go(), self._loop).result(5)

    def peers(self, room: str) -> int:
        return len(self.rooms.get(room, {}))

    # -- the gateway ------------------------------------------------------------------

    def _check(self, connection, request):
        parts = urlsplit(request.path)
        if parts.path != "/ws":
            return connection.respond(404, "not found\n")
        origin = request.headers.get("Origin")
        if self.allowed_origins and origin not in self.allowed_origins:
            return connection.respond(403, "origin not allowed\n")
        room = (parse_qs(parts.query).get("room") or [""])[0]
        if not room or len(room) > 64 or not all(c.isalnum() or c in "_-" for c in room) or not room.isascii():
            return connection.respond(400, "bad room\n")
        return None

    async def _handle(self, ws):
        room = parse_qs(urlsplit(ws.request.path).query)["room"][0]
        members = self.rooms.setdefault(room, {})
        if len(members) >= MAX_PEERS:
            self.refused_full += 1
            await ws.send(json.dumps({"type": "error", "code": "room-full", "message": "room already has 2 peers"}))
            await ws.close(CLOSE_ROOM_FULL, "room full")
            return
        peer_id = str(uuid.uuid4())
        peers = list(members)
        members[peer_id] = ws
        self.joins.append((room, peer_id))
        await ws.send(json.dumps({"type": "welcome", "peerId": peer_id, "peers": peers}))
        await self._broadcast(room, peer_id, {"type": "peer-joined", "peerId": peer_id})
        try:
            async for raw in ws:
                if not isinstance(raw, str):
                    await ws.close(1003, "binary frames are not accepted")
                    break
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(msg, dict) or msg.get("type") == "ping":
                    continue
                t = msg.get("type")
                if t not in RELAY_TYPES:
                    continue
                payload = _sanitize(t, msg.get("payload"))
                target = members.get(msg.get("to")) if isinstance(msg.get("to"), str) else None
                if payload is None or target is None or target is ws:
                    continue
                out = {"type": t, "from": peer_id, "payload": payload}
                if self.rewrite is not None:
                    out = self.rewrite(out)
                    if out is None:
                        continue
                self.relayed.append(out)
                try:
                    await target.send(json.dumps(out))
                except Exception:  # noqa: BLE001 - the target is going away
                    pass
        except Exception:  # noqa: BLE001 - a dropped socket is a peer leaving
            pass
        finally:
            if members.get(peer_id) is ws:
                del members[peer_id]
                await self._broadcast(room, None, {"type": "peer-left", "peerId": peer_id})

    async def _broadcast(self, room: str, except_id, msg: dict) -> None:
        for pid, ws in list(self.rooms.get(room, {}).items()):
            if pid != except_id:
                try:
                    await ws.send(json.dumps(msg))
                except Exception:  # noqa: BLE001
                    pass


__all__ = ["FakeSignalingServer"]
