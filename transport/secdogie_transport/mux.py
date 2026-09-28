"""Share one transport's inbound path among named application channels.

A ``Transport`` delivers every authenticated message for this node to a single
``deliver(from_did, message)`` callback. ``ChannelMux`` takes that one callback
and hands each message to the application channel it names -- the operator's
dialogue session is one channel; others can sit beside it on the same socket
without knowing about each other.

Wire shape, inside the transport's already signed (and, with a transport key,
sealed) frame: one byte giving the channel name's length, the ASCII name, then
the channel's own bytes. The mux adds no authentication of its own and needs
none: the transport has already verified the sender, and the handler receives
that verified DID. A message for an unknown channel, or a malformed one, is
dropped; a handler that raises does not disturb the others.
"""
from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable

from .session import Session
from .transport import Transport

ChannelHandler = Callable[[str, bytes], None]  # (verified sender DID, payload) -> None

_NAME = re.compile(r"^[A-Za-z0-9._/-]{1,64}$")

log = logging.getLogger("secdogie_transport.mux")


def encode(channel: str, payload: bytes) -> bytes:
    if not _NAME.match(channel):
        raise ValueError(f"bad channel name {channel!r}")
    name = channel.encode("ascii")
    return bytes([len(name)]) + name + bytes(payload)


def decode(message: bytes) -> tuple[str, bytes] | None:
    if not message:
        return None
    n = message[0]
    name = message[1:1 + n]
    if len(name) != n:
        return None
    try:
        channel = name.decode("ascii")
    except UnicodeDecodeError:
        return None
    if not _NAME.match(channel):
        return None
    return channel, message[1 + n:]


class ChannelMux:
    """Own ``transport``'s delivery for the local node and dispatch by channel."""

    def __init__(self, transport: Transport, session: Session):
        self._transport = transport
        self.local_did = session.peer.did
        self._handlers: dict[str, ChannelHandler] = {}
        self._lock = threading.Lock()
        transport.register(session, self._deliver)

    def channel(self, name: str, handler: ChannelHandler | None) -> None:
        """Route messages on ``name`` to ``handler(sender_did, payload)``;
        ``None`` closes the channel."""
        if not _NAME.match(name):
            raise ValueError(f"bad channel name {name!r}")
        with self._lock:
            if handler is None:
                self._handlers.pop(name, None)
            else:
                self._handlers[name] = handler

    def send(self, to_did: str, channel: str, payload: bytes) -> bool:
        """Send ``payload`` on ``channel`` to ``to_did``. False when the
        transport could not route it (no endpoint or key for that peer yet)."""
        return self._transport.route(self.local_did, to_did, encode(channel, payload))

    def _deliver(self, from_did: str, message: bytes) -> None:
        decoded = decode(message)
        if decoded is None:
            return
        channel, payload = decoded
        with self._lock:
            handler = self._handlers.get(channel)
        if handler is None:
            return
        try:
            handler(from_did, payload)
        except Exception:  # noqa: BLE001 - one channel's bug must not take down the others
            log.exception("channel %s handler failed", channel)


__all__ = ["ChannelMux", "ChannelHandler", "encode", "decode"]
