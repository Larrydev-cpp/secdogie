"""Discovery on the simulated mesh: bootstrap from hints, reach the degree with
transit links, never widen trust, give up on peers that never answer; and the
resolver that turns DIDs into endpoints without blocking."""
from __future__ import annotations

import threading

import pytest

pytest.importorskip("nacl")

import sim  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_inference import Coordinator, MeshNode, TrustRoots  # noqa: E402
from secdogie_inference.discovery import Discovery, DiscoveryConfig  # noqa: E402
from secdogie_inference.protocol import ROLE_FORWARD  # noqa: E402
from secdogie_inference.runtime import Resolver  # noqa: E402


def build(n_forwarders=5, *, extra_mesh=(), coordinator=False, min_degree=2, max_degree=4):
    net = sim.Net()
    ids = {f"F{i}": Identity.generate() for i in range(n_forwarders)}
    if coordinator:
        ids["C"] = Identity.generate()
    did = {k: v.did for k, v in ids.items()}
    mesh = set(did.values()) | set(extra_mesh)
    trust = TrustRoots(mesh=Allowlist(mesh), users=Allowlist(), deciders=Allowlist(),
                       coordinators=Allowlist({did["C"]} if coordinator else set()), brokers=Allowlist(),
                       operators=Allowlist())
    parts = {}
    for name, ident in ids.items():
        if name == "C":
            parts[name] = net.add(Coordinator(ident, net.wire(ident.did), trust, config=sim.FAST, clock=net.clock))
        else:
            parts[name] = net.add(MeshNode(ident, net.wire(ident.did), trust, roles={ROLE_FORWARD},
                                           config=sim.FAST, clock=net.clock))
    cfg = DiscoveryConfig(min_degree=min_degree, max_degree=max_degree, every=1.0, confirm_within=5.0)
    hints = sorted(mesh)
    disc = {n: Discovery(p, config=cfg, hints=lambda: hints, clock=net.clock) for n, p in parts.items()}
    return net, did, parts, disc


def run(net, disc, seconds):
    for _ in range(seconds):
        net.step(1.0)
        for d in disc.values():
            d.step()
        net.run()


def test_nodes_bootstrap_from_hints_and_reach_the_degree_with_transit_links():
    net, did, parts, disc = build(6)
    run(net, disc, 12)
    for name, d in disc.items():
        now = net.clock()
        assert len(d.linked(now)) >= 2, name
        assert len(set(d.dialed)) <= 4  # dialing stops at max_degree
    # every node can reach every other through confirmed links
    adj = parts["F0"].lsdb.adjacency(net.clock())
    seen, todo = {did["F0"]}, [did["F0"]]
    while todo:
        for nxt in adj.get(todo.pop(), ()):
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    assert seen == set(did.values())


def test_discovery_never_dials_outside_the_trust_list_and_drops_dead_dials():
    ghost = Identity.generate().did        # trusted, but no such node is running
    stranger = Identity.generate().did     # not trusted at all
    net, did, parts, disc = build(3, extra_mesh=[ghost])
    disc["F0"]._hints = lambda: [stranger, ghost] + sorted(did.values())
    run(net, disc, 3)
    assert stranger not in parts["F0"].neighbors
    assert all(stranger not in d.dialed for d in disc.values())
    run(net, disc, 12)  # the ghost never confirms: its slot is freed
    assert all(ghost not in p.neighbors for p in parts.values())
    assert any(ghost in d.dialed for d in disc.values())


def test_links_to_the_coordinator_do_not_count_toward_the_degree():
    net, did, parts, disc = build(3, coordinator=True, min_degree=2)
    run(net, disc, 12)
    now = net.clock()
    for name in ("F0", "F1", "F2"):
        linked = disc[name].linked(now)
        assert did["C"] not in linked and len(linked) >= 2


class FakeTransport:
    def __init__(self):
        self.endpoints = {}

    def set_peer_endpoint(self, did, host, port):
        self.endpoints[did] = (host, port)


class FakeRendezvous:
    def __init__(self, answers, gate):
        self.answers, self.gate, self.calls = answers, gate, []

    def lookup(self, did):
        self.calls.append(did)
        self.gate.wait(5)
        return self.answers.get(did)


class Found:
    def __init__(self, host, port):
        self.host, self.port = host, port

    def best(self):
        return self


def test_the_resolver_never_blocks_and_rate_limits_lookups():
    transport, gate = FakeTransport(), threading.Event()
    rdv = FakeRendezvous({"did:key:zB": Found("10.0.0.2", 7960)}, gate)
    clock = [0.0]
    r = Resolver(transport, {"did:key:zA": ("10.0.0.1", 7960)}, rdv, retry_after=5.0, clock=lambda: clock[0])
    r.resolve("did:key:zA")                       # static: immediate, no lookup
    assert transport.endpoints["did:key:zA"] == ("10.0.0.1", 7960) and rdv.calls == []
    r.resolve("did:key:zB")                       # returns at once; the lookup runs on a thread
    r.resolve("did:key:zB")                       # already pending: not asked twice
    gate.set()
    for _ in range(100):
        if "did:key:zB" in r.found:
            break
        threading.Event().wait(0.01)
    assert transport.endpoints["did:key:zB"] == ("10.0.0.2", 7960) and rdv.calls == ["did:key:zB"]
    r.resolve("did:key:zB")                       # within retry_after: not asked again
    assert rdv.calls == ["did:key:zB"]
    clock[0] = 10.0
    r.resolve("did:key:zB")
    for _ in range(100):
        if len(rdv.calls) == 2:
            break
        threading.Event().wait(0.01)
    assert rdv.calls == ["did:key:zB", "did:key:zB"]
