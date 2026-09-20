"""The transport interface upper layers depend on, plus an in-memory hub
reference implementation.

`Transport` is the seam: register an authenticated peer session, then route
messages to a peer by DID -- never by socket. Today the only implementation is
`HubTransport`, an in-memory hub-and-spoke router that models the current
topology (and stays the fallback / rendezvous / relay). A real peer-to-peer
`DirectUDPTransport` (Phase 2.10) implements the same interface, so nothing above
this seam changes when direct transport lands.

This layer is deliberately transport-mechanism-free (no sockets): it is the
identity/session/routing model, driven in tests and backed later by the fleet
TCP or the C tunnel.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from .endpoint import Endpoint
from .session import Session

DeliverFn = Callable[[str, bytes], None]  # (from_did, message) -> None


class Transport(ABC):
    @abstractmethod
    def register(self, session: Session, deliver: DeliverFn) -> bool:
        """Register an authenticated peer session and its inbound-delivery
        callback. Returns False if the peer is not authorized."""

    @abstractmethod
    def route(self, from_did: str, to_did: str, message: bytes) -> bool:
        """Deliver `message` to the peer `to_did`. Returns True if delivered."""

    @abstractmethod
    def migrate(self, did: str, endpoint: Endpoint) -> bool:
        """Record an endpoint migration for a peer's session (identity unchanged)."""


class HubTransport(Transport):
    """In-memory hub-and-spoke router. Peers register their sessions with the
    hub; messages route through it. With an allowlist, only authorized DIDs may
    register (a message to/from an unregistered peer is not delivered)."""

    def __init__(self, *, allowlist=None):
        self._allowlist = allowlist
        self._sessions: dict[str, Session] = {}          # did -> session
        self._deliver: dict[str, DeliverFn] = {}         # did -> inbound callback

    def register(self, session: Session, deliver: DeliverFn) -> bool:
        did = session.peer.did
        if self._allowlist is not None and not self._allowlist.contains(did):
            return False
        self._sessions[did] = session
        self._deliver[did] = deliver
        session.established = True
        return True

    def route(self, from_did: str, to_did: str, message: bytes) -> bool:
        if from_did not in self._sessions:
            return False  # sender not a registered peer
        cb = self._deliver.get(to_did)
        if cb is None:
            return False  # destination not reachable via this hub
        cb(from_did, message)
        return True

    def migrate(self, did: str, endpoint: Endpoint) -> bool:
        session = self._sessions.get(did)
        if session is None:
            return False
        session.migrate(endpoint)  # session_id + peer unchanged
        return True

    def peers(self) -> list[str]:
        return sorted(self._sessions)


# The real peer-to-peer transport lives in udp.py (DirectUDPTransport), which
# implements this same Transport interface over UDP -- imported at the package
# level so upper layers are unchanged whether they use the hub or direct path.
