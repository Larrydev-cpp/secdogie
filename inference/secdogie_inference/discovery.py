"""Automatic peer discovery: keep a node linked to enough of the mesh.

Without discovery a node links only to the peers it was configured with. With
it, a node bootstraps from whatever it can name and grows its links from what
the mesh tells it:

  1. **Bootstrap.** Before any heartbeat has arrived, the candidates are the
     ``hints``: static peers, then the DIDs on the mesh trust list. Dialing one
     resolves it (static entry, or a rendezvous lookup by DID) and sends this
     node's heartbeat; a direct heartbeat is a link offer, so the peer links
     back.
  2. **Learn the mesh.** Once one link works, the flooded heartbeats fill the
     link-state database with every live node, its roles and its load.
  3. **Keep the degree.** Every ``every`` seconds, while this node has fewer
     than ``min_degree`` confirmed, witnessed links *to transit nodes*, it
     dials up to
     ``dial_per_round`` more: live transit nodes (serving or forwarding, never
     the coordinator or the key broker), not excluded (latched, isolated,
     quarantined, locally suspect), not yet neighbours, lowest load first, and
     spread by a per-node hash so that nodes do not all dial the same peers.
     It never dials once ``max_degree`` neighbours are open.

Discovery tears down only its own failed attempts: a peer it dialed that never
confirmed the link within ``confirm_within`` seconds (offline, unresolvable)
is dropped from the neighbour set, so it cannot hold a slot forever. A link
that was confirmed persists, the same way repair links do; a peer that keeps
sending its heartbeat directly would re-offer a dropped link anyway. Discovery never widens trust: a candidate must
already be on the mesh trust list, and every check of the participant and the
transport still applies to it.
"""
from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .protocol import ROLE_BROKER, ROLE_COORDINATOR, TRANSIT_ROLES


@dataclass(frozen=True)
class DiscoveryConfig:
    min_degree: int = 3
    max_degree: int = 6
    every: float = 2.0
    dial_per_round: int = 2
    confirm_within: float = 20.0   # an unconfirmed dial is given up after this long

    def __post_init__(self):
        if not 1 <= self.min_degree <= self.max_degree:
            raise ValueError("need 1 <= min_degree <= max_degree")


class Discovery:
    def __init__(self, participant, *, config: DiscoveryConfig | None = None,
                 hints: Callable[[], Iterable[str]] = lambda: (), clock=time.time):
        self.participant = participant
        self.config = config or DiscoveryConfig()
        self._hints = hints
        self._clock = clock
        self.dialed: list[str] = []   # every DID this discovery dialed, oldest first (bounded)
        self._pending: dict[str, float] = {}  # dialed, not yet a confirmed link -> when

    def _spread(self, did: str) -> str:
        return hashlib.sha256(f"{self.participant.did}|{did}".encode()).hexdigest()

    def _transit(self, did: str) -> bool:
        roles = self.participant.lsdb.roles(did)
        return bool(roles & TRANSIT_ROLES) and not roles & {ROLE_COORDINATOR, ROLE_BROKER}

    def linked(self, now: float) -> set[str]:
        """Confirmed, witnessed links to transit nodes: the only links that carry
        routes. A link to the coordinator or the key broker does not count, or a
        model provider node linked only to them would look connected while no
        route could reach it."""
        p = self.participant
        return {d for d in p.lsdb.adjacency(now, exclude=p.excluded(now)).get(p.did, ()) if self._transit(d)}

    def confirmed(self, now: float) -> set[str]:
        p = self.participant
        return set(p.lsdb.adjacency(now, exclude=p.excluded(now)).get(p.did, ()))

    def candidates(self, now: float) -> list[str]:
        p = self.participant
        excluded = p.excluded(now)
        trusted = p.trust.mesh

        admitted = p.admissions

        def eligible(d: str) -> bool:
            # Admitted nodes (browsers) keep no lobby to dial: they dial in themselves.
            return d != p.did and d not in p.neighbors and d not in excluded and trusted.contains(d) \
                and not (admitted is not None and admitted.contains(d))

        known = []
        for d in p.lsdb.known():
            if eligible(d) and p.lsdb.alive(d, now) and self._transit(d):
                st = p.lsdb.get(d)
                known.append((st.load if st is not None else 0.0, self._spread(d), d))
        if known:
            return [d for _load, _h, d in sorted(known)]
        # Nothing learned yet: bootstrap from what this node can name.
        seen: set[str] = set()
        hints = []
        for d in self._hints():
            if d not in seen and eligible(d):
                seen.add(d)
                hints.append((self._spread(d), d))
        return [d for _h, d in sorted(hints)]

    def step(self) -> list[str]:
        """One round: dial more peers if this node has too few links. Returns
        the DIDs dialed."""
        p = self.participant
        cfg = self.config
        with p._lock:
            now = float(self._clock())
            confirmed = self.confirmed(now)
            for d, at in list(self._pending.items()):
                if d in confirmed:
                    del self._pending[d]
                elif now - at > cfg.confirm_within:
                    del self._pending[d]
                    p.neighbors.discard(d)  # never linked: free the slot for a live peer
                    p.note("discover-drop", peer=d[-8:])
            degree = len(self.linked(now))
            room = cfg.max_degree - len(p.neighbors)
            want = min(cfg.min_degree - degree, room, cfg.dial_per_round)
            if want <= 0:
                return []
            dialed = []
            for d in self.candidates(now):
                if len(dialed) >= want:
                    break
                if p.connect(d):
                    dialed.append(d)
                    self._pending[d] = now
            if dialed:
                p.note("discover", dialed=[d[-8:] for d in dialed], degree=degree)
                self.dialed.extend(dialed)
                del self.dialed[:-256]
            return dialed


__all__ = ["Discovery", "DiscoveryConfig"]
