"""One transport, two kinds of path: the UDP socket and pseudo-address links.

``DirectUDPTransport`` talks to a ``UDPChannel``: ``start(on_datagram)``,
``send(host, port, data)``, ``close()``, ``address``. ``CompositeChannel`` is
that same shape over the real UDP socket *plus* named links -- a WebRTC data
channel, say -- whose datagrams arrive with a pseudo address ``(name,
link_id)`` and leave through ``link.send(link_id, data)``. Everything above the
channel runs unchanged: per-frame DID signatures, the allowlist, the replay
window, endpoint adoption (a peer that reconnects on a new link moves there on
its newest frame), the mux, the dialogue sessions.

A link name starts with ``@``, which no real host does, so a pseudo address can
never be resolved or sent to over UDP: an ``@`` host with no link behind it is
dropped. Datagrams from the UDP thread and from link threads are handed up one
at a time, so the transport never runs twice at once.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Protocol

log = logging.getLogger("secdogie_transport.composite")

LINK_HOST_PREFIX = "@"
WEBRTC_HOST = "@webrtc"


class LinkChannel(Protocol):
    def start(self, deliver: Callable[[bytes, int], None]) -> None: ...

    def send(self, link_id: int, data: bytes) -> None: ...

    def close(self) -> None: ...


def is_link_host(host) -> bool:
    return isinstance(host, str) and host.startswith(LINK_HOST_PREFIX)


class CompositeChannel:
    """``udp`` plus ``links`` (name -> LinkChannel), duck-typed as a UDPChannel."""

    def __init__(self, udp, links: dict[str, LinkChannel]):
        for name in links:
            if not is_link_host(name) or len(name) < 2:
                raise ValueError(f"a link name starts with {LINK_HOST_PREFIX!r}: {name!r}")
        self.udp = udp
        self.links = dict(links)
        self._lock = threading.Lock()

    @property
    def address(self):
        return self.udp.address

    def start(self, on_datagram: Callable[[bytes, tuple], None]) -> None:
        def one_at_a_time(data: bytes, addr: tuple) -> None:
            with self._lock:
                on_datagram(data, addr)

        self.udp.start(one_at_a_time)
        for name, link in self.links.items():
            link.start(lambda data, link_id, _n=name: one_at_a_time(data, (_n, link_id)))

    def send(self, host, port, data: bytes) -> None:
        if is_link_host(host):
            link = self.links.get(host)
            if link is not None:
                link.send(port, data)
            return  # an unknown pseudo address goes nowhere -- never out over UDP
        self.udp.send(host, port, data)

    def close(self) -> None:
        for link in self.links.values():
            try:
                link.close()
            except Exception:  # noqa: BLE001 - closing one path must not keep the other open
                log.exception("closing a link failed")
        self.udp.close()


__all__ = ["CompositeChannel", "LinkChannel", "LINK_HOST_PREFIX", "WEBRTC_HOST", "is_link_host"]
