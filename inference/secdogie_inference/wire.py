"""How a participant reaches its neighbours.

The inference mesh needs two things from below: send bytes to a DID, and
(optionally) prepare a link to one. ``Wire`` is that seam. ``MuxWire`` puts it
on a secdogie-transport ``ChannelMux``: frames ride the ``inference/v1``
channel inside the transport's DID-signed (and, with transport keys, sealed)
frames, over direct UDP, relays or both (``FailoverTransport``). The handler
receives the transport-verified sender DID, which is what ``Participant.handle``
trusts for "who handed me this frame" -- and nothing more.

``MuxWire`` takes the mux duck-typed, so this package does not import the
transport.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from .protocol import CHANNEL


class Wire(Protocol):
    def send(self, to_did: str, payload: bytes) -> bool:
        """Hand ``payload`` to the transport for ``to_did``; False when there is
        no way to reach it at all (a lost datagram still returns True)."""

    def connect(self, did: str) -> None:
        """Prepare a link to ``did`` (learn its endpoint, say). May do nothing."""


class MuxWire:
    def __init__(self, mux, *, resolve: Callable[[str], None] | None = None, channel: str = CHANNEL):
        self._mux = mux
        self._resolve = resolve
        self._channel = channel

    def attach(self, participant) -> None:
        """Deliver this channel's inbound frames to ``participant``."""
        self._mux.channel(self._channel, participant.handle)

    def send(self, to_did: str, payload: bytes) -> bool:
        return bool(self._mux.send(to_did, self._channel, payload))

    def connect(self, did: str) -> None:
        if self._resolve is not None:
            self._resolve(did)


__all__ = ["Wire", "MuxWire"]
