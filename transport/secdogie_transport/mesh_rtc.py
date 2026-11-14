"""This machine as one of your devices in the mesh, over WebRTC: the Python
side of webrtc/client/mesh.js, so Studio, phones and other browsers form one
mesh.

One link per other device of the roster, each in its own room
(meshlink.link_room). On every new connection both ends sign a link-hello
over the DTLS fingerprints they see, resent until the other side has it;
nothing else is read until the other end's hello checks out. Then every
message is a DID-signed frame (secdogie/direct/v1, the same as on UDP) on the
mux channel mesh/v1:

    {m: roster, roster}   the admissions this device holds
    {m: note, text}       a line from the person
    {m: ping, t} / pong   round-trip time

    node = MeshNode(me, mesh="home", owners=[owner_did], admission=mine, signal_url=...)
    await node.start()        # the person asked to go online
    node.snapshot()           # who is online, by which link, how fast
    await node.say("...")
    await node.stop()

Runs on an asyncio loop; Studio drives it from its own loop thread.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import time
from collections.abc import Callable

from secdogie_identity import Allowlist

from . import mux
from .meshlink import MESH_CHANNEL, Roster, create_hello, link_room, verify_hello
from .rtc import RtcError, RtcPeer
from .sealed import ReplayWindow
from .udp import _decode_frame, _encode_frame

log = logging.getLogger("secdogie.transport.mesh_rtc")

OFFLINE, CONNECTING, ONLINE, REFUSED = "offline", "connecting", "online", "refused"
RESEND_EVERY = 0.7
RESEND_MAX = 40
PING_EVERY = 4.0
RETRY = (2.0, 4.0, 8.0, 15.0, 30.0)
WAIT_IN_ROOM = 90.0  # how long one attempt waits for the other device to come online
MAX_NOTE = 500


class _Link:
    def __init__(self, did: str):
        self.did = did
        self.state = OFFLINE
        self.reason: str | None = None
        self.name = ""
        self.rtt: int | None = None
        self.peer: RtcPeer | None = None
        self.authed = False
        self.task: asyncio.Task | None = None
        self.window = ReplayWindow()


class MeshNode:
    def __init__(self, me, *, mesh: str, owners, admission: dict | None = None, admissions=(), name: str = "",
                 signal_url: str, ice_servers=(), on_change: Callable[[], None] = lambda: None,
                 on_note: Callable[[dict], None] = lambda n: None, clock=time.time):
        self.me = me
        self.mesh = mesh
        self.roster = Roster(mesh, owners)
        self.admission = admission
        for a in (admission, *admissions):
            if a:
                self.roster.admit(a)
        self.name = name
        self.signal_url = signal_url
        self.ice_servers = tuple(ice_servers)
        self.on_change = on_change
        self.on_note = on_note
        self.clock = clock
        self.online = False
        self.links: dict[str, _Link] = {}
        self.seen_online: set[str] = set()
        self._ctr = time.time_ns()
        self._pinger: asyncio.Task | None = None

    # ---- what callers use -------------------------------------------------------------

    async def start(self) -> None:
        if self.online:
            return
        self.online = True
        self._changed()
        for did in self.roster.devices():
            self._ensure(did)
        self._pinger = asyncio.create_task(self._ping_loop())

    async def stop(self) -> None:
        if not self.online:
            return
        self.online = False
        if self._pinger:
            self._pinger.cancel()
        links = list(self.links.values())
        self.links.clear()
        for link in links:
            if link.task:
                link.task.cancel()
            if link.peer:
                await link.peer.close()
        self._changed()

    def snapshot(self) -> dict:
        def listed(did):
            return did != self.me.did and (self.roster.admission(did) or did not in self.roster.owners
                                            or did in self.seen_online)

        devices = []
        for did in filter(listed, self.roster.devices()):
            link = self.links.get(did)
            state = link.state if (self.online and link) else OFFLINE
            devices.append({"did": did, "name": (link.name if link and link.name else self.roster.name(did)),
                            "owner": did in self.roster.owners, "state": state,
                            "rtt": link.rtt if link and state == ONLINE else None,
                            "reason": link.reason if link else None})
        return {"online": self.online, "mesh": self.mesh, "me": {"did": self.me.did, "name": self.name,
                                                                 "owner": self.me.did in self.roster.owners},
                "devices": devices}

    async def say(self, text: str) -> int:
        line = str(text or "").strip()[:MAX_NOTE]
        if not line:
            return 0
        n = 0
        for link in list(self.links.values()):
            if await self._send_frame(link, {"m": "note", "text": line}):
                n += 1
        return n

    def admit(self, admission: dict) -> bool:
        """A device this machine just admitted (or learned of): link to it too."""
        added = self.roster.admit(admission)
        if added and self.online:
            self._ensure(admission["device"])
        if added:
            self._changed()
        return added

    # ---- links --------------------------------------------------------------------------

    def _changed(self) -> None:
        with contextlib.suppress(Exception):
            self.on_change()

    def _ensure(self, did: str) -> None:
        if not self.online or did == self.me.did or did in self.links:
            return
        link = _Link(did)
        link.name = self.roster.name(did)
        self.links[did] = link
        link.task = asyncio.create_task(self._run(link))

    def _set(self, link: _Link, state: str, reason: str | None = None) -> None:
        link.state, link.reason = state, reason
        if state != ONLINE:
            link.rtt = None
        self._changed()

    async def _run(self, link: _Link) -> None:
        room = link_room(self.mesh, self.me.did, link.did)
        attempt = 0
        while self.online and self.links.get(link.did) is link:
            peer = RtcPeer(self.signal_url, room, ice_servers=self.ice_servers)
            link.peer = peer
            try:
                await peer.open(timeout=WAIT_IN_ROOM)
                attempt = 0
                await self._serve(link, peer)
            except (RtcError, asyncio.TimeoutError) as e:
                log.debug("link %s: %s", link.did[-6:], e)
            except asyncio.CancelledError:
                await peer.close()
                raise
            finally:
                link.authed = False
                await peer.close()
            if not self.online:
                return
            if link.state != REFUSED:
                self._set(link, OFFLINE)
            await asyncio.sleep(RETRY[min(attempt, len(RETRY) - 1)])
            attempt += 1

    async def _serve(self, link: _Link, peer: RtcPeer) -> None:
        """One connection: hellos both ways, then frames until it closes."""
        local, remote = peer.fingerprints()
        self._set(link, CONNECTING)
        mine = create_hello(self.me, mesh=self.mesh, nonce=secrets.token_hex(16), local_fp=local, remote_fp=remote,
                            admission=self.admission, name=self.name, roster=self.roster.digest(), clock=self.clock)
        state = {"peer_has_mine": False, "theirs": None}

        async def send_hello():
            with contextlib.suppress(RtcError):
                # compact: mesh.js knows a hello by its first bytes, {"k":"hello"
                await peer.send(json.dumps({"k": "hello", "hello": mine, "got": link.authed}, ensure_ascii=False,
                                           separators=(",", ":")))

        async def resend():
            for _ in range(RESEND_MAX):
                await asyncio.sleep(RESEND_EVERY)
                if state["peer_has_mine"]:
                    return
                await send_hello()

        await send_hello()
        resender = asyncio.create_task(resend())
        try:
            while True:
                raw = await peer.recv()
                if raw.startswith('{"k"'):  # a hello; frames start {"t" (Python) or {"ctr" (browser)
                    await self._their_hello(link, peer, raw, state, send_hello, local, remote)
                    if link.state == REFUSED:
                        return
                    continue
                if not link.authed:
                    continue
                decoded = _decode_frame(raw.encode("utf-8"), Allowlist({link.did}), self.me.did)
                if decoded is None:
                    continue
                _, ctr, message = decoded
                if not link.window.accept(ctr):
                    continue
                state["peer_has_mine"] = True  # frames come only once our hello checked out there
                inner = mux.decode(message)
                if inner is None or inner[0] != MESH_CHANNEL:
                    continue
                try:
                    msg = json.loads(inner[1].decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                await self._on_message(link, msg)
        finally:
            resender.cancel()

    async def _their_hello(self, link, peer, raw, state, send_hello, local, remote) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(msg, dict) or msg.get("k") != "hello":
            return
        if msg.get("got") is True:
            state["peer_has_mine"] = True
        hello = msg.get("hello")
        if link.authed:
            if isinstance(hello, dict) and hello.get("sig") == state["theirs"] and msg.get("got") is not True:
                await send_hello()  # a resend: ours has not reached them
            return
        checked = verify_hello(hello, roster=self.roster, expect_did=link.did, my_local_fp=local,
                               my_remote_fp=remote, clock=self.clock)
        if not checked.ok:
            self._set(link, REFUSED, checked.reason)
            return
        link.authed = True
        state["theirs"] = checked.value["sig"]
        self.seen_online.add(link.did)
        if checked.value.get("admission"):
            self.roster.admit(checked.value["admission"])
        link.name = checked.value.get("name") or self.roster.name(link.did)
        self._set(link, ONLINE)
        await send_hello()  # now with got: true
        if checked.value.get("roster") != self.roster.digest():
            await self._send_frame(link, {"m": "roster", "roster": self.roster.wire()})
        await self._send_frame(link, {"m": "ping", "t": time.monotonic() * 1000})

    async def _on_message(self, link: _Link, msg) -> None:
        kind = msg.get("m") if isinstance(msg, dict) else None
        if kind == "roster":
            added = self.roster.merge(msg.get("roster"))
            if added:
                self._changed()
                for did in self.roster.devices():
                    self._ensure(did)
                for other in list(self.links.values()):
                    if other is not link and other.authed:
                        await self._send_frame(other, {"m": "roster", "roster": self.roster.wire()})
        elif kind == "note" and isinstance(msg.get("text"), str) and msg["text"].strip():
            with contextlib.suppress(Exception):
                self.on_note({"from": link.did, "name": link.name, "text": msg["text"][:MAX_NOTE],
                              "at": time.time()})
        elif kind == "ping":
            await self._send_frame(link, {"m": "pong", "t": msg.get("t")})
        elif kind == "pong" and isinstance(msg.get("t"), (int, float)):
            link.rtt = max(0, round(time.monotonic() * 1000 - msg["t"]))
            self._changed()

    async def _send_frame(self, link: _Link, msg: dict) -> bool:
        if not link.authed or link.peer is None:
            return False
        self._ctr += 1
        payload = mux.encode(MESH_CHANNEL, json.dumps(msg, ensure_ascii=False).encode("utf-8"))
        frame = _encode_frame(self.me, link.did, payload, self._ctr).decode("utf-8")
        try:
            await link.peer.send(frame)
            return True
        except RtcError:
            return False

    async def _ping_loop(self) -> None:
        while self.online:
            await asyncio.sleep(PING_EVERY)
            for link in list(self.links.values()):
                await self._send_frame(link, {"m": "ping", "t": time.monotonic() * 1000})


__all__ = ["CONNECTING", "OFFLINE", "ONLINE", "REFUSED", "MeshNode"]
