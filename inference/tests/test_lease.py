"""Key leases: the public half verifies for anyone, the sealed half opens only
for its node and only onto its own lease."""
from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from nacl.public import PrivateKey  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_inference.lease import (  # noqa: E402
    LeaseBook,
    MemoryKeySource,
    check_lease,
    make_lease,
    make_lease_revocation,
    new_canary,
    open_grant,
    seal_grant,
)
from secdogie_inference.protocol import TrustRoots  # noqa: E402
from sim import public_key_b64  # noqa: E402

NOW = 1_000_000.0


def clock():
    return NOW


@pytest.fixture
def cast():
    broker, node, other, rogue = (Identity.generate() for _ in range(4))
    trust = TrustRoots(mesh=Allowlist({node.did, other.did, broker.did}), users=Allowlist(),
                       deciders=Allowlist(), coordinators=Allowlist(), brokers=Allowlist({broker.did}),
                       operators=Allowlist())
    return broker, node, other, rogue, trust


def test_seal_and_open(cast):
    broker, node, *_rest, trust = cast
    key = PrivateKey.generate()
    canary = new_canary()
    lease = make_lease(broker, node.did, "m/a", key_version=1, canary=canary, clock=clock)
    assert check_lease(lease, trust) is None
    sealed = seal_grant(lease, "sk-123", canary, public_key_b64(key))
    opened = open_grant(lease, sealed, key)
    assert (opened.secret, opened.canary, opened.model) == ("sk-123", canary, "m/a")
    assert open_grant(lease, sealed, PrivateKey.generate()) is None  # only the node's own key opens it


def test_a_sealed_body_cannot_move_to_another_lease(cast):
    broker, node, *_rest, trust = cast
    key = PrivateKey.generate()
    canary = new_canary()
    lease = make_lease(broker, node.did, "m/a", key_version=1, canary=canary, clock=clock)
    other = make_lease(broker, node.did, "m/a", key_version=1, canary=new_canary(), clock=clock)
    sealed = seal_grant(lease, "sk-123", canary, public_key_b64(key))
    assert open_grant(other, sealed, key) is None
    # anyone can seal to a public key, but not with a canary that hashes to the lease's
    forged = seal_grant({**lease}, "sk-evil", new_canary(), public_key_b64(key))
    assert open_grant(lease, forged, key) is None


def test_lease_checks(cast):
    broker, node, other, rogue, trust = cast
    lease = make_lease(broker, node.did, "m/a", key_version=1, canary=new_canary(), ttl=60, clock=clock)
    book = LeaseBook(trust)
    assert book.add(lease) and not book.add(lease)
    assert book.check(lease, provider=node.did, model="m/a", now=NOW) is None
    assert book.check(lease, provider=other.did, model="m/a", now=NOW) == "the lease is for another node"
    assert book.check(lease, provider=node.did, model="m/b", now=NOW) == "the lease is for another model"
    assert book.check(lease, provider=node.did, model="m/a", now=NOW + 61) == "the lease is outside its validity window"
    assert book.eligible(["m/a"], NOW) == [(node.did, "m/a", lease)]
    forged = make_lease(rogue, node.did, "m/a", key_version=1, canary=new_canary(), clock=clock)
    assert check_lease(forged, trust) == "signer not trusted" and not book.add(forged)
    outsider = make_lease(broker, rogue.did, "m/a", key_version=1, canary=new_canary(), clock=clock)
    assert check_lease(outsider, trust) == "the lease is for a node outside the mesh"


def test_revocation_is_broker_only_and_permanent(cast):
    broker, node, other, rogue, trust = cast
    lease = make_lease(broker, node.did, "m/a", key_version=1, canary=new_canary(), clock=clock)
    book = LeaseBook(trust)
    book.add(lease)
    assert book.revoke(make_lease_revocation(rogue, [lease["lease_id"]], clock=clock)) == frozenset()
    assert book.revoke(make_lease_revocation(broker, [lease["lease_id"]], clock=clock)) == {lease["lease_id"]}
    assert book.check(lease, provider=node.did, model="m/a", now=NOW) == "the lease is revoked"
    assert not book.add(lease) and book.eligible(["m/a"], NOW) == []


def test_key_source_rotation():
    keys = MemoryKeySource({"m/a": "sk-1"}, mint=lambda m: "sk-2")
    assert keys.current("m/a") == (1, "sk-1")
    assert keys.rotate("m/a") == 2 and keys.current("m/a") == (2, "sk-2")
    stuck = MemoryKeySource({"m/a": "sk-1"})
    assert stuck.rotate("m/a") is None
    assert stuck.set("m/a", "sk-new") == 2
