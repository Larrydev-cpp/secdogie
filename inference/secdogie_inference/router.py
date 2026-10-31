"""Route decisions: which model nodes answer, along which paths.

``Router`` is the default decision policy of a serving node. It is
deterministic -- the same view gives the same legs on every node -- and is
written against a ``View`` the node builds from its signed link state, so it
never sees a node the node has excluded:

  1. every eligible ``(provider, model)`` gets its cheapest mesh path
     (interior nodes transit-only, cost 1 + load);
  2. the best ``quorum`` are taken, distinct providers, preferring distinct
     models (two vendors agreeing says more than one vendor twice) and, beyond
     the first leg, paths that share no interior node with the legs already
     taken, so one bad forwarder cannot sit on every leg;
  3. still short, and not in lockdown: a central leg ``me -> coordinator ->
     provider`` for providers the coordinator is linked to -- the fallback of
     state 2, "connect to the central server";
  4. still short: ``NoRoute``. The request is refused, never sent on a
     partial quorum.

A plug-in policy (an LLM-driven router, say -- the diagram's "decision model")
implements the same ``plan``. Whatever it returns, the serving node checks the
legs against the request and its own view before signing; a policy that
proposes a forbidden route is reported as ``decider.self-reported`` and the
mesh goes to lockdown (``node.MeshNode._decide``).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .protocol import MODE_LOCKDOWN, VIA_CENTRAL, VIA_MESH
from .topology import shortest_path


@dataclass(frozen=True)
class Leg:
    provider: str
    model: str
    path: tuple[str, ...]
    via: str = VIA_MESH

    def to_wire(self) -> dict:
        return {"provider": self.provider, "model": self.model, "path": list(self.path), "via": self.via}

    @classmethod
    def from_wire(cls, obj: dict) -> Leg:
        return cls(obj["provider"], obj["model"], tuple(obj["path"]), obj["via"])

    @property
    def interior(self) -> tuple[str, ...]:
        return self.path[1:-1]


@dataclass
class View:
    """A serving node's snapshot of what it may route over."""

    me: str
    mode: str
    adjacency: dict[str, set[str]]            # confirmed live links, excluded nodes removed
    transit: frozenset[str]                   # nodes allowed inside a mesh path
    candidates: list[tuple[str, str]]         # eligible (provider, model)
    central: dict[str, frozenset[str]] = field(default_factory=dict)  # my coordinator -> providers it reaches
    load: dict[str, float] = field(default_factory=dict)


class NoRoute(Exception):
    """Not enough eligible, reachable model nodes for the quorum."""


class Router:
    def __init__(self, *, prefer_distinct_models: bool = True):
        self.prefer_distinct_models = prefer_distinct_models

    def plan(self, view: View, request: dict, *, quorum: int, avoid=frozenset()) -> list[Leg]:
        candidates = [c for c in view.candidates if c[1] in request["models"]]
        pins = {(p, m) for p, m in request["pins"]}
        if pins:
            candidates = [c for c in candidates if c in pins]
            missing = pins - set(candidates)
            if missing:
                raise NoRoute(f"pinned model node not eligible: {sorted(missing)[0][0]}")
            quorum = len(pins)
        avoid = frozenset(avoid)

        def cost(d: str) -> float:
            return 1.0 + view.load.get(d, 0.0)

        def path_to(provider: str, extra=frozenset()):
            return shortest_path(view.adjacency, view.me, provider, transit=view.transit,
                                 avoid=avoid | extra, cost=cost)

        scored = []
        for provider, model in candidates:
            path = path_to(provider)
            if path is not None:
                scored.append((sum(cost(d) for d in path[1:]), view.load.get(provider, 0.0), provider, model, path))
        scored.sort()

        legs: list[Leg] = []
        for provider, model, path in self._pick(scored, quorum):
            used = {d for leg in legs for d in leg.interior}
            if used:
                path = path_to(provider, frozenset(used)) or path  # disjoint when the mesh allows it
            legs.append(Leg(provider, model, path))

        if len(legs) < quorum and view.mode != MODE_LOCKDOWN:
            taken = {leg.provider for leg in legs}
            spare = sorted((view.load.get(p, 0.0), p, m) for p, m in candidates if p not in taken)
            reachable = []
            for load, provider, model in spare:
                coord = next((c for c in sorted(view.central) if c not in avoid and provider in view.central[c]),
                             None)
                if coord is not None:
                    reachable.append((load, 0.0, provider, model, (view.me, coord, provider)))
            for provider, model, path in self._pick(reachable, quorum - len(legs), taken_models={
                    leg.model for leg in legs}):
                legs.append(Leg(provider, model, path, VIA_CENTRAL))

        if len(legs) < quorum:
            raise NoRoute(f"{len(legs)} of {quorum} model nodes reachable")
        return legs

    def _pick(self, scored, n: int, *, taken_models=frozenset()):
        """``n`` entries of ``scored`` (best first) with distinct providers,
        first one per not-yet-used model when preferring distinct models."""
        out, providers, models = [], set(), set(taken_models)
        passes = (True, False) if self.prefer_distinct_models and (n > 1 or models) else (False,)
        for distinct in passes:
            for _cost, _load, provider, model, path in scored:
                if len(out) >= n:
                    return out
                if provider in providers or (distinct and model in models):
                    continue
                out.append((provider, model, path))
                providers.add(provider)
                models.add(model)
        return out


__all__ = ["Leg", "View", "Router", "NoRoute"]
