"""A deterministic network for the inference mesh, and the diagram's topology.

``Net`` delivers frames in FIFO order on a fake clock. A frame goes only over a
link some node opened (``Wire.connect``); a crashed node receives nothing, but
sending to it still "succeeds", as a UDP datagram would; ``drop`` holds
per-node receive filters for nodes that heartbeat fine but mishandle traffic.

``Arc`` builds the hand-drawn "new arc"::

        C (central server) ----- K (API key)
        |  \\                      |      \\
    L(M/API) - W1 - S2 - S3 - W4 - R(M/API)      N1, N2: standby forwarders ("new P2P")
                     \\  /                        linked only to C until recruited
                      U (user)

``S2`` / ``S3`` serve (the decision layer, P2P+S); ``W1`` / ``W4`` forward
(plain P2P, the ones that go "wrong" in state 2); ``L`` answers with model
``vendor-a/alpha`` and ``R`` with ``vendor-b/beta``.
"""
from __future__ import annotations

import base64
import json
from collections import deque

from nacl.public import PrivateKey
from secdogie_identity import Allowlist, Identity, create_binding
from secdogie_inference import (
    Config,
    Coordinator,
    KeyBroker,
    MemoryKeySource,
    MeshNode,
    TrustRoots,
    UserClient,
    assignments_from_bindings,
)
from secdogie_inference.protocol import ROLE_FORWARD, ROLE_MODEL, ROLE_SERVE

ALPHA = "vendor-a/alpha"
BETA = "vendor-b/beta"

FAST = Config(heartbeat_every=1.0, fail_after=3.5, leg_timeout=3.0, advise_every=2.0, suspect_threshold=2,
              suspect_cooldown=15.0, breaker_threshold=3, breaker_cooldown=10.0, quarantine_threshold=2,
              quarantine_window=60.0, quarantine_cooldown=20.0, probation=10.0, lease_ttl=600.0)


class Clock:
    def __init__(self, now: float = 1_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


class Net:
    def __init__(self):
        self.clock = Clock()
        self.parts: dict = {}
        self.links: set[frozenset] = set()
        self.down: set[str] = set()
        self.drop: dict = {}       # did -> predicate(src, frame) -> True to drop on receipt
        self.queue: deque = deque()

    def wire(self, did: str) -> SimWire:
        return SimWire(self, did)

    def add(self, part):
        self.parts[part.did] = part
        return part

    def link(self, a: str, b: str) -> None:
        self.links.add(frozenset((a, b)))

    def linked(self, a: str, b: str) -> bool:
        return frozenset((a, b)) in self.links

    def run(self, limit: int = 500_000) -> None:
        n = 0
        while self.queue:
            n += 1
            assert n < limit, "message storm"
            src, dst, payload = self.queue.popleft()
            part = self.parts.get(dst)
            if part is None or dst in self.down:
                continue
            pred = self.drop.get(dst)
            if pred is not None and pred(src, json.loads(payload)):
                continue
            part.handle(src, payload)

    def step(self, dt: float = 1.0) -> None:
        self.clock.now += dt
        for did, part in list(self.parts.items()):
            if did not in self.down:
                part.tick()
        self.run()

    def settle(self, seconds: float, dt: float = 1.0) -> None:
        for _ in range(int(round(seconds / dt))):
            self.step(dt)

    def until(self, cond, *, limit: float = 60.0, dt: float = 1.0) -> float:
        """Step until ``cond()`` holds; returns the simulated seconds it took."""
        waited = 0.0
        self.run()
        while not cond():
            assert waited < limit, "condition never held"
            self.step(dt)
            waited += dt
        return waited


class SimWire:
    def __init__(self, net: Net, did: str):
        self.net = net
        self.did = did

    def send(self, to: str, payload: bytes) -> bool:
        if self.did in self.net.down or not self.net.linked(self.did, to):
            return False
        self.net.queue.append((self.did, to, payload))
        return True

    def connect(self, did: str) -> None:
        self.net.link(self.did, did)


def public_key_b64(key: PrivateKey) -> str:
    return base64.b64encode(bytes(key.public_key)).decode("ascii")


def echo(model: str, text: str, secret: str) -> str:
    """A well-behaved upstream: every model gives the same answer."""
    return f"answer({text})"


class Arc:
    """The diagram's mesh, built and settled. Pass ``node_classes`` /
    ``backends`` / ``policies`` to swap in a misbehaving participant."""

    def __init__(self, *, node_classes=None, backends=None, policies=None, config: Config = FAST,
                 fanout: int = 1, entries=("S2", "S3"), mint=True):
        node_classes = node_classes or {}
        backends = backends or {}
        policies = policies or {}
        self.net = net = Net()
        self.ids = {name: Identity.generate() for name in
                    ("L", "W1", "S2", "S3", "W4", "R", "C", "K", "N1", "N2", "U", "O")}
        did = {name: ident.did for name, ident in self.ids.items()}
        self.did = did
        mesh = {did[n] for n in ("L", "W1", "S2", "S3", "W4", "R", "C", "K", "N1", "N2")}
        self.trust = TrustRoots(mesh=Allowlist(mesh), users=Allowlist({did["U"]}),
                                deciders=Allowlist({did["S2"], did["S3"]}), coordinators=Allowlist({did["C"]}),
                                brokers=Allowlist({did["K"]}), operators=Allowlist({did["O"]}))
        clock = net.clock
        self.tkeys = {n: PrivateKey.generate() for n in ("L", "R")}
        bindings = [create_binding(self.ids[n], public_key_b64(self.tkeys[n]), key_version=1, clock=clock)
                    for n in ("L", "R")]
        counter = {"n": 0}

        def minter(model: str) -> str:
            counter["n"] += 1
            return f"sk-{model}-rotated-{counter['n']}"

        self.keys = MemoryKeySource({ALPHA: "sk-alpha-1", BETA: "sk-beta-1"}, mint=minter if mint else None)
        assignments = assignments_from_bindings(bindings, {did["L"]: [ALPHA], did["R"]: [BETA]}, self.trust,
                                                now=clock())

        def node(name, roles, **kw):
            cls = node_classes.get(name, MeshNode)
            return net.add(cls(self.ids[name], net.wire(did[name]), self.trust, roles=roles, config=config,
                               clock=clock, **kw))

        self.L = node("L", {ROLE_MODEL}, backend=backends.get("L", echo), transport_key=self.tkeys["L"])
        self.R = node("R", {ROLE_MODEL}, backend=backends.get("R", echo), transport_key=self.tkeys["R"])
        self.W1 = node("W1", {ROLE_FORWARD})
        self.W4 = node("W4", {ROLE_FORWARD})
        self.S2 = node("S2", {ROLE_SERVE, ROLE_FORWARD}, policy=policies.get("S2"))
        self.S3 = node("S3", {ROLE_SERVE, ROLE_FORWARD}, policy=policies.get("S3"))
        self.N1 = node("N1", {ROLE_FORWARD})
        self.N2 = node("N2", {ROLE_FORWARD})
        self.C = net.add(Coordinator(self.ids["C"], net.wire(did["C"]), self.trust, standby=[did["N1"], did["N2"]],
                                     config=config, clock=clock))
        self.K = net.add(KeyBroker(self.ids["K"], net.wire(did["K"]), self.trust, self.keys,
                                   assignments=assignments, config=config, clock=clock))
        for a, b in (("L", "W1"), ("W1", "S2"), ("S2", "S3"), ("S3", "W4"), ("W4", "R"),
                     ("C", "S2"), ("C", "S3"), ("C", "K"), ("C", "N1"), ("C", "N2")):
            self.part(a).connect(did[b])
            self.part(b).connect(did[a])
        self.U = net.add(UserClient(self.ids["U"], net.wire(did["U"]), self.trust,
                                    entries=[did[e] for e in entries], standby=[did["N1"], did["N2"]],
                                    fanout=fanout, timeout=12.0, config=config, clock=clock))
        net.settle(4)

    def part(self, name: str):
        return self.net.parts[self.did[name]]

    def everyone(self):
        return list(self.net.parts.values())

    def ask(self, models, text: str = "hello", **kw):
        """Ask as the user and run the mesh until the outcome is in."""
        handle = self.U.ask(models, text, **kw)
        self.net.until(lambda: self.U.outcome(handle) is not None)
        return self.U.outcome(handle)

    def short(self, did: str) -> str:
        return next(n for n, d in self.did.items() if d == did)
