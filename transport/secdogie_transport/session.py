"""An authenticated peer session -- the thing upper layers hold onto.

A Session ties a stable `session_id` to a `PeerIdentity` and its candidate
endpoints. Endpoint migration (a peer's address changing) updates the active
endpoint but NEVER changes the session_id or the peer, so a NAT rebind does not
look like a new peer -- the same invariant the C tunnel already has (session-id
keyed roaming), lifted to an identity-bearing abstraction.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .endpoint import Endpoint, EndpointSet
from .peer import PeerIdentity


@dataclass
class Session:
    session_id: str
    peer: PeerIdentity
    endpoints: EndpointSet = field(default_factory=EndpointSet)
    active: Endpoint | None = None
    established: bool = False

    def __post_init__(self):
        if self.active is None:
            self.active = self.endpoints.best()

    @property
    def did(self) -> str:
        return self.peer.did

    def add_endpoint(self, endpoint: Endpoint) -> None:
        self.endpoints.add(endpoint)
        if self.active is None:
            self.active = self.endpoints.best()

    def migrate(self, endpoint: Endpoint) -> None:
        """Move to a new active endpoint (e.g. after a NAT rebind). Identity is
        unchanged: same session_id, same peer/DID."""
        self.endpoints.add(endpoint)
        self.active = endpoint

    def observe_from(self, host: str, port: int) -> None:
        """A packet arrived from (host, port): record it and adopt it as active,
        keeping identity -- the roaming rule."""
        ep = self.endpoints.observe(host, port)
        self.active = ep
