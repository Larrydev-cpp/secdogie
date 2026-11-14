"""A whole pairing over WebRTC, from Python: the same conversation as
webrtc/pair.js, so Studio can pair with a phone's browser, and two Studios
with each other.

    inviting side                                   joining side
    ------------                                    ------------
    invite = pairing.create_invite(owner, ...)      (the person opens the link and agrees)
    await invite_device(owner, invite, ...)         await join_with_invite(device, invite, ...)
        both: RtcPeer in the invite's room; each sends its bind
              {t: pair/bind, bind, got} again and again until the other side
              says it has it (got: true): a browser can drop a message sent
              the instant a channel opens
        both: verify the other's bind, show the same six digits (on_code)
    decide() -> True: sign the admission
        {t: pair/admission, admission}  ------>     verify it (owner = the inviter, this mesh, this device)
        {t: pair/roster, roster}        ------>     keep the mesh's devices, to find them later
                                        <------     {t: pair/received}
    decide() -> False:  {t: pair/cancel} ------>    PairingCancelled

Either side can cancel; a bind that does not hold ends the pairing with its
reason (PairingFailed), and nothing is admitted.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .pairing import (
    DEVICE,
    INVITER,
    check_code,
    create_admission,
    create_bind,
    verify_admission,
    verify_bind,
)
from .rtc import RtcError, RtcPeer

RESEND_EVERY = 0.6
RESEND_MAX = 40


class PairingFailed(RuntimeError):
    """The pairing did not hold; `str()` is the reason (pairing.py's words)."""


class PairingCancelled(RuntimeError):
    """One side said the codes differ, or stopped."""


@dataclass(frozen=True)
class Comparison:
    code: str  # the six digits both screens show
    did: str  # the other side's DID
    name: str  # what the other side calls itself


def _nonce() -> str:
    import secrets

    return secrets.token_hex(16)


class _Exchange:
    """Both sides' bind exchange over one channel, and the messages after it."""

    def __init__(self, peer: RtcPeer, me, invite: dict, role: str, name: str, clock):
        self.peer, self.me, self.invite, self.role, self.name, self.clock = peer, me, invite, role, name, clock
        self.mine: dict | None = None
        self.theirs: dict | None = None
        self.peer_has_mine = False
        self.inbox: asyncio.Queue = asyncio.Queue()
        self._pump: asyncio.Task | None = None
        self._resend: asyncio.Task | None = None

    async def _send(self, msg: dict) -> None:
        with contextlib.suppress(RtcError):
            await self.peer.send(json.dumps(msg, ensure_ascii=False))

    async def start(self) -> None:
        local, remote = self.peer.fingerprints()
        self.mine = create_bind(self.me, invite_id=self.invite["invite_id"], role=self.role, nonce=_nonce(),
                                local_fp=local, remote_fp=remote, name=self.name, clock=self.clock)
        self._pump = asyncio.create_task(self._read())
        await self._send_bind()
        self._resend = asyncio.create_task(self._keep_sending())

    async def _send_bind(self) -> None:
        await self._send({"t": "pair/bind", "bind": self.mine, "got": self.theirs is not None})

    async def _keep_sending(self) -> None:
        for _ in range(RESEND_MAX):
            await asyncio.sleep(RESEND_EVERY)
            if self.peer_has_mine and self.theirs is not None:
                return
            await self._send_bind()

    async def _read(self) -> None:
        while True:
            try:
                raw = await self.peer.recv()
            except RtcError as e:
                await self.inbox.put({"t": "_closed", "reason": str(e)})
                return
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("t") == "pair/bind":
                if msg.get("got") is True:
                    self.peer_has_mine = True
                bind = msg.get("bind")
                if self.theirs is not None:
                    if isinstance(bind, dict) and bind.get("sig") == self.theirs.get("sig") and msg.get("got") is not True:
                        await self._send_bind()  # a resend: they still lack ours
                    continue
                await self.inbox.put({"t": "_bind", "bind": bind})
            else:
                await self.inbox.put(msg)

    async def their_bind(self, timeout: float) -> Comparison:
        """Wait for the other side's bind and check it against this connection."""
        deadline = time.monotonic() + timeout
        while True:
            msg = await self.next(deadline)
            if msg.get("t") == "_bind":
                local, remote = self.peer.fingerprints()
                expect = DEVICE if self.role == INVITER else INVITER
                checked = verify_bind(msg.get("bind"), invite=self.invite, expect_role=expect, my_local_fp=local,
                                      my_remote_fp=remote, clock=self.clock)
                if not checked.ok:
                    await self._send({"t": "pair/cancel"})
                    raise PairingFailed(checked.reason)
                self.theirs = checked.value
                await self._send_bind()  # now with got: true
                inviter_bind, device_bind = (self.mine, self.theirs) if self.role == INVITER else (self.theirs, self.mine)
                return Comparison(check_code(self.invite["invite_id"], inviter_bind, device_bind),
                                  self.theirs["did"], self.theirs.get("name", ""))
            self._other(msg)

    async def next(self, deadline: float | None = None) -> dict:
        left = None if deadline is None else max(0.0, deadline - time.monotonic())
        try:
            msg = await asyncio.wait_for(self.inbox.get(), left)
        except asyncio.TimeoutError:
            raise PairingFailed("the other device did not answer in time") from None
        if msg.get("t") == "_closed":
            raise PairingFailed(f"the connection closed: {msg.get('reason')}")
        if msg.get("t") == "pair/cancel":
            raise PairingCancelled("the other side cancelled")
        return msg

    def _other(self, msg: dict) -> None:
        pass  # anything else before the binds settle is not ours to act on

    async def send(self, msg: dict) -> None:
        await self._send(msg)

    async def stop(self) -> None:
        for t in (self._pump, self._resend):
            if t is not None:
                t.cancel()


async def invite_device(owner, invite: dict, *, my_name: str = "",
                        on_code: Callable[[Comparison], None] = lambda c: None,
                        decide: Callable[[], Awaitable[bool]], roster: dict | None = None,
                        roles=("forward",), ice_servers=(), timeout: float | None = None, clock=time.time) -> dict:
    """Wait in the invite's room for the device it was written for, compare the
    codes, and on `decide()` -> True sign and hand over its admission (and the
    mesh's `roster`, a Roster.wire()). Returns the admission."""
    ttl = timeout if timeout is not None else max(1.0, invite["expires_at"] - clock())
    peer = RtcPeer(invite["signal"], invite["room"], ice_servers=ice_servers)
    await peer.open(timeout=ttl)
    ex = _Exchange(peer, owner, invite, INVITER, my_name, clock)
    try:
        await ex.start()
        comparison = await ex.their_bind(timeout=60)
        on_code(comparison)
        decision = asyncio.create_task(decide())
        while not decision.done():
            nxt = asyncio.create_task(ex.next())
            done, _ = await asyncio.wait({decision, nxt}, return_when=asyncio.FIRST_COMPLETED)
            if nxt in done:
                nxt.result()  # raises on cancel / close
            else:
                nxt.cancel()
        if not decision.result():
            await ex.send({"t": "pair/cancel"})
            raise PairingCancelled("the codes differ")
        admission = create_admission(owner, invite["mesh"], comparison.did, invite_id=invite["invite_id"],
                                     name=comparison.name, roles=roles, clock=clock)
        if roster is not None:  # first: the joiner keeps it once the admission that follows checks out
            await ex.send({"t": "pair/roster", "roster": roster})
        await ex.send({"t": "pair/admission", "admission": admission})
        with contextlib.suppress(PairingFailed, PairingCancelled):
            deadline = time.monotonic() + 8
            while (await ex.next(deadline)).get("t") != "pair/received":
                pass
        return admission
    finally:
        await ex.stop()
        await peer.close()


async def join_with_invite(device, invite: dict, *, my_name: str = "",
                           on_code: Callable[[Comparison], None] = lambda c: None, ice_servers=(),
                           timeout: float = 600.0, clock=time.time) -> tuple[dict, dict | None]:
    """Join the room an (already verified) invite names, compare the codes,
    and wait for the inviter's admission. Returns (admission, roster or None)."""
    peer = RtcPeer(invite["signal"], invite["room"], ice_servers=ice_servers)
    await peer.open(timeout=60)
    ex = _Exchange(peer, device, invite, DEVICE, my_name, clock)
    try:
        await ex.start()
        on_code(await ex.their_bind(timeout=60))
        deadline = time.monotonic() + timeout
        admission, roster = None, None
        while admission is None:
            msg = await ex.next(deadline)
            if msg.get("t") == "pair/roster" and isinstance(msg.get("roster"), dict):
                roster = msg["roster"]  # kept only if the admission that follows checks out
                continue
            if msg.get("t") != "pair/admission":
                continue
            checked = verify_admission(msg.get("admission"), owners=_owners(invite), mesh=invite["mesh"],
                                       device=device.did)
            if not checked.ok or checked.value.get("invite_id") != invite["invite_id"]:
                raise PairingFailed(f"the admission does not check out: {checked.reason or 'another invite'}")
            admission = checked.value
        await ex.send({"t": "pair/received"})
        await asyncio.sleep(0.2)
        return admission, roster
    finally:
        await ex.stop()
        await peer.close()


def _owners(invite: dict):
    from secdogie_identity import Allowlist

    return Allowlist({invite["inviter"]})


__all__ = ["Comparison", "PairingCancelled", "PairingFailed", "invite_device", "join_with_invite"]
