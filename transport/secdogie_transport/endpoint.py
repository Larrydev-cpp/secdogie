"""Endpoint candidates for a peer, and the preference order between them.

A peer may be reachable at several addresses -- a locally-known one, a public
one, one observed from an incoming packet, or an untried candidate. The session
(session.py) can migrate between these without changing identity.
"""
from __future__ import annotations

from dataclasses import dataclass

# Reachability preference, best first: a public address beats one merely observed
# from a packet, which beats a local/LAN address, which beats an untried candidate.
_KIND_PRIORITY = {"public": 0, "observed": 1, "local": 2, "candidate": 3}
KINDS = frozenset(_KIND_PRIORITY)


@dataclass(frozen=True)
class Endpoint:
    kind: str  # one of KINDS
    host: str
    port: int

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"unknown endpoint kind {self.kind!r} (expected one of {sorted(KINDS)})")
        if not (0 < self.port < 65536):
            raise ValueError(f"port out of range: {self.port}")

    def key(self) -> tuple[str, int]:
        return (self.host, self.port)


class EndpointSet:
    """An ordered set of candidate endpoints for one peer, de-duplicated by
    (host, port). `best()` returns the most-reachable one."""

    def __init__(self, endpoints=()):
        self._by_key: dict[tuple[str, int], Endpoint] = {}
        for e in endpoints:
            self.add(e)

    def add(self, endpoint: Endpoint) -> None:
        """Add or upgrade an endpoint. If the same (host, port) is seen with a
        more-reachable kind, keep the better kind."""
        existing = self._by_key.get(endpoint.key())
        if existing is None or _KIND_PRIORITY[endpoint.kind] < _KIND_PRIORITY[existing.kind]:
            self._by_key[endpoint.key()] = endpoint

    def observe(self, host: str, port: int) -> Endpoint:
        """Record an address a packet actually arrived from (an 'observed' endpoint)."""
        ep = Endpoint("observed", host, port)
        self.add(ep)
        return ep

    def all(self) -> list[Endpoint]:
        return sorted(self._by_key.values(), key=lambda e: (_KIND_PRIORITY[e.kind], e.host, e.port))

    def best(self) -> Endpoint | None:
        candidates = self.all()
        return candidates[0] if candidates else None

    def __len__(self) -> int:
        return len(self._by_key)
