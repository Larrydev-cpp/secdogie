"""An authenticated peer session -- the thing upper layers hold onto.

A Session ties a stable `session_id` to a `PeerIdentity` and its candidate
endpoints. Endpoint migration (a peer's address changing) updates the active
endpoint but NEVER changes the session_id or the peer, so a NAT rebind does not
look like a new peer -- the same invariant the C tunnel already has (session-id
keyed roaming), lifted to an identity-bearing abstraction.

P2P.2 adds the *path* a session is on: every session starts on the hub relay
(`PATH_RELAY`) and may be migrated to a proven direct UDP path (`PATH_DIRECT`),
then fall back again. The switch is make-before-break: the relay registration is
never torn down, the relay endpoint is remembered so `fall_back` can restore it,
and each path change bumps `epoch` so upper layers can tell which messages were
sent before or after a switch.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .endpoint import Endpoint, EndpointSet
from .peer import PeerIdentity

PATH_RELAY = "relay"
PATH_DIRECT = "direct"
PATHS = frozenset({PATH_RELAY, PATH_DIRECT})


@dataclass
class Session:
    session_id: str
    peer: PeerIdentity
    endpoints: EndpointSet = field(default_factory=EndpointSet)
    active: Endpoint | None = None
    established: bool = False
    path: str = PATH_RELAY
    epoch: int = 0                          # bumped on every path change
    relay_endpoint: Endpoint | None = None  # where the session was before going direct

    def __post_init__(self):
        if self.path not in PATHS:
            raise ValueError(f"unknown path {self.path!r} (expected one of {sorted(PATHS)})")
        if self.active is None:
            self.active = self.endpoints.best()

    @property
    def did(self) -> str:
        return self.peer.did

    @property
    def is_direct(self) -> bool:
        return self.path == PATH_DIRECT

    def add_endpoint(self, endpoint: Endpoint) -> None:
        self.endpoints.add(endpoint)
        if self.active is None:
            self.active = self.endpoints.best()

    def migrate(self, endpoint: Endpoint, *, path: str | None = None) -> None:
        """Move to a new active endpoint (e.g. after a NAT rebind, or relay ->
        direct once a direct round-trip is proven). Identity is unchanged: same
        session_id, same peer/DID. `path` None keeps the current path; a change
        of path bumps `epoch`, and leaving the relay remembers its endpoint."""
        if path is not None and path not in PATHS:
            raise ValueError(f"unknown path {path!r} (expected one of {sorted(PATHS)})")
        self.endpoints.add(endpoint)
        if path is not None and path != self.path:
            if self.path == PATH_RELAY:
                self.relay_endpoint = self.active
            self.path = path
            self.epoch += 1
        self.active = endpoint

    def fall_back(self) -> bool:
        """Return a DIRECT session to the relay (the direct path died or was never
        proven), restoring the relay endpoint it left. Identity is unchanged.
        Returns whether the path changed."""
        if self.path == PATH_RELAY:
            return False
        self.path = PATH_RELAY
        self.epoch += 1
        self.active = self.relay_endpoint  # None when the session never had one
        return True

    def observe_from(self, host: str, port: int) -> None:
        """A packet arrived from (host, port): record it and adopt it as active,
        keeping identity -- the roaming rule."""
        ep = self.endpoints.observe(host, port)
        self.active = ep
