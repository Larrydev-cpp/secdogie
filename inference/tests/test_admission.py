"""Operator admission: who may admit, what an admission can grant (forward only),
expiry, revocation, and an admission flooding through the mesh so every node
accepts the admitted DID -- with its role claims cut to forward."""
from __future__ import annotations

import pytest

pytest.importorskip("nacl")

import sim  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_identity.signing import sign_payload  # noqa: E402
from secdogie_inference import MeshNode, TrustRoots  # noqa: E402
from secdogie_inference.admission import (  # noqa: E402
    ADMISSION,
    MAX_ADMISSION_TTL,
    AdmissionBook,
    RevocationFilter,
    check_admission,
    make_admission,
)
from secdogie_inference.protocol import (  # noqa: E402
    ROLE_FORWARD,
    ROLE_MODEL,
    ROLE_SERVE,
    AnyOf,
    make_heartbeat,
)

NOW = 1_000_000.0


def clock():
    return NOW


@pytest.fixture
def cast():
    op, other, browser = Identity.generate(), Identity.generate(), Identity.generate()
    trust = TrustRoots(mesh=Allowlist(), users=Allowlist(), deciders=Allowlist(), coordinators=Allowlist(),
                       brokers=Allowlist(), operators=Allowlist({op.did}))
    return op, other, browser, trust


def test_only_a_trusted_operator_admits_and_only_to_forward(cast):
    op, other, browser, trust = cast
    good = make_admission(op, browser.did, ttl=3600, clock=clock)
    assert check_admission(good, trust, now=NOW) is None
    assert check_admission(make_admission(other, browser.did, clock=clock), trust, now=NOW) == "signer not trusted"
    assert check_admission({**good, "subject": other.did}, trust, now=NOW) == "invalid or missing signature"
    with pytest.raises(ValueError, match="forward role only"):
        make_admission(op, browser.did, roles=(ROLE_SERVE,), clock=clock)
    body = {k: v for k, v in good.items() if k not in ("signer", "sig")}
    for bad, why in (({"roles": [ROLE_FORWARD, ROLE_MODEL]}, "forward role only"),
                     ({"subject": op.did}, "bad subject"),
                     ({"expires_at": NOW + MAX_ADMISSION_TTL + 1}, "MAX_ADMISSION_TTL")):
        assert why in check_admission(sign_payload(op, {**body, **bad}), trust, now=NOW)
    assert check_admission(good, trust, now=NOW + 3600) == "expired"
    assert good["type"] == ADMISSION


def test_the_book_keeps_current_admissions_newest_per_subject_and_bounded(cast):
    op, _other, browser, trust = cast
    t = [NOW]
    book = AdmissionBook(trust, capacity=2, clock=lambda: t[0])
    short = make_admission(op, browser.did, ttl=60, clock=clock)
    long = make_admission(op, browser.did, ttl=600, clock=clock)
    assert book.add(short) and book.add(long) and not book.add(short)  # an older one changes nothing
    assert book.contains(browser.did) and book.dids() == {browser.did}
    others = [Identity.generate().did for _ in range(2)]
    for d in others:
        assert book.add(make_admission(op, d, clock=clock))
    assert len(book) == 2 and not book.contains(browser.did)  # capacity: the oldest went
    t[0] = NOW + 86401
    assert not any(book.contains(d) for d in others)
    book.prune(t[0])
    assert len(book) == 0


def test_a_master_revocation_beats_an_admission(cast):
    op, _other, browser, trust = cast
    book = AdmissionBook(trust, clock=clock)
    book.add(make_admission(op, browser.did, clock=clock))

    class Revoked:
        def is_revoked(self, did):
            return did == browser.did

    assert not RevocationFilter(book, Revoked()).contains(browser.did)
    assert RevocationFilter(book, Revoked()).dids() == set()


def test_an_admission_floods_and_every_node_accepts_the_admitted_dids_forward_role_only():
    net = sim.Net()
    op = Identity.generate()
    ids = {n: Identity.generate() for n in ("A", "B", "C")}
    browser = Identity.generate()
    late_id = Identity.generate()  # joins after the admission was flooded
    static = {i.did for i in ids.values()} | {late_id.did}
    nodes = {}
    for name, ident in ids.items():
        book = AdmissionBook(clock=net.clock)
        trust = TrustRoots(mesh=AnyOf(Allowlist(static), book), users=Allowlist(),
                           deciders=Allowlist(), coordinators=Allowlist(), brokers=Allowlist(),
                           operators=Allowlist({op.did}))
        node = net.add(MeshNode(ident, net.wire(ident.did), trust, roles={ROLE_FORWARD}, config=sim.FAST,
                                clock=net.clock))
        node.attach_admissions(book)
        nodes[name] = node
    for a, b in (("A", "B"), ("B", "C")):
        nodes[a].connect(ids[b].did)
        nodes[b].connect(ids[a].did)
    net.settle(3)
    assert not any(n.trust.mesh.contains(browser.did) for n in nodes.values())

    assert nodes["A"].publish(make_admission(op, browser.did, clock=net.clock))
    net.run()
    assert all(n.trust.mesh.contains(browser.did) for n in nodes.values())  # C learned it two hops away

    # the admitted node claims more than forward; every node records only forward
    hb = make_heartbeat(browser, roles=[ROLE_FORWARD, ROLE_MODEL, ROLE_SERVE], seq=1, neighbors=[ids["A"].did],
                        clock=net.clock)
    assert nodes["C"].lsdb.merge(hb, now=net.clock())
    assert nodes["C"].lsdb.roles(browser.did) == {ROLE_FORWARD}

    # a node that joins later gets the admission from a directory answer
    book = AdmissionBook(clock=net.clock)
    trust = TrustRoots(mesh=AnyOf(Allowlist(static), book),
                       users=Allowlist(), deciders=Allowlist(), coordinators=Allowlist(), brokers=Allowlist(),
                       operators=Allowlist({op.did}))
    late = net.add(MeshNode(late_id, net.wire(late_id.did), trust, roles={ROLE_FORWARD}, config=sim.FAST,
                            clock=net.clock))
    late.attach_admissions(book)
    assert not late.trust.mesh.contains(browser.did)
    late.connect(ids["B"].did)
    late.send(ids["B"].did, "dir?")
    net.run()
    assert late.trust.mesh.contains(browser.did)
