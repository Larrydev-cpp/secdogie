from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from secdogie_citadel import sync  # noqa: E402
from secdogie_citadel.journal import Journal  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402


def _counter(start=0.0):
    n = {"t": start}

    def clock():
        n["t"] += 1.0
        return n["t"]

    return clock


def _order(journal):
    return [(e["author"], e["seq"]) for e in journal.events()]


def test_two_nodes_converge_in_one_round():
    a, b = Identity.generate(), Identity.generate()
    allow = Allowlist({a.did, b.did})
    ja = Journal(identity=a, allowlist=allow, clock=_counter())
    jb = Journal(identity=b, allowlist=allow, clock=_counter(100.0))

    for i in range(3):
        ja.append("note", {"n": i})
    for i in range(2):
        jb.append("note", {"n": i})

    got_a, got_b = sync.sync_round(ja, jb)
    assert (got_a, got_b) == (2, 3)
    assert _order(ja) == _order(jb)          # converged
    assert sync.sync_round(ja, jb) == (0, 0)  # idempotent: nothing new


def test_have_and_respond_message_path():
    a, b = Identity.generate(), Identity.generate()
    allow = Allowlist({a.did, b.did})
    ja = Journal(identity=a, allowlist=allow, clock=_counter())
    jb = Journal(identity=b, allowlist=allow, clock=_counter(100.0))
    ja.append("note", {})
    ja.append("note", {})
    jb.append("note", {})

    # b pulls from a: b announces have, a responds with what b lacks, b applies
    reply = sync.respond_to_have(ja, sync.have_message(jb))
    assert reply["kind"] == sync.EVENTS
    assert sync.apply_events_message(jb, reply) == 2
    # and the reverse direction
    reply2 = sync.respond_to_have(jb, sync.have_message(ja))
    assert sync.apply_events_message(ja, reply2) == 1
    assert _order(ja) == _order(jb)


def test_star_topology_relays_through_a_hub():
    a, b, hub = Identity.generate(), Identity.generate(), Identity.generate()
    allow = Allowlist({a.did, b.did, hub.did})
    ja = Journal(identity=a, allowlist=allow, clock=_counter())
    jb = Journal(identity=b, allowlist=allow, clock=_counter(100.0))
    jhub = Journal(identity=hub, allowlist=allow, clock=_counter(200.0))

    ja.append("note", {"who": "a"})
    jb.append("note", {"who": "b"})

    # each spoke syncs only with the hub; the hub relays between them
    sync.sync_round(ja, jhub)
    sync.sync_round(jb, jhub)
    sync.sync_round(ja, jhub)  # a pulls b's event (now on the hub)

    assert _order(ja) == _order(jb) == _order(jhub)
    ok, _ = jhub.verify()
    assert ok


def test_sync_drops_events_from_unauthorized_author():
    a, rogue = Identity.generate(), Identity.generate()
    ja = Journal(identity=a, allowlist=Allowlist({a.did}), clock=_counter())
    jrogue = Journal(identity=rogue, allowlist=Allowlist({a.did, rogue.did}), clock=_counter(100.0))
    ja.append("note", {})
    jrogue.append("note", {"evil": True})

    # a only authorizes itself: the rogue's events are dropped on merge
    sync.sync_round(ja, jrogue)
    authors = {e["author"] for e in ja.events()}
    assert authors == {a.did}
