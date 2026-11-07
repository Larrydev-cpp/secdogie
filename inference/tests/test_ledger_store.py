"""The mutiny ledger across restarts: a restart can make it stricter, never looser.

Stored incidents latch again whether or not they still verify (revoking the
mutinous node must not end lockdown by itself); stored resets clear only if they
still verify; a forged, torn or tampered line can add lockdown but never lift it.
"""
from __future__ import annotations

import json
import os
import stat

import pytest

pytest.importorskip("nacl")

from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_inference import Coordinator, MeshNode  # noqa: E402
from secdogie_inference.lease import make_lease, new_canary  # noqa: E402
from secdogie_inference.mutiny import (  # noqa: E402
    PROVIDER_EQUIVOCATION,
    LedgerStore,
    MutinyLedger,
    make_incident,
    make_reset,
    node_target,
)
from secdogie_inference.protocol import (  # noqa: E402
    MODE_LOCKDOWN,
    MODE_NORMAL,
    TrustRoots,
    make_decision,
    make_request,
    make_response,
)
from secdogie_inference.router import Leg  # noqa: E402

NOW = 1_000_000.0


def clock():
    return NOW


class World:
    """A decider ``s``, a model node ``p`` that equivocates, a forwarder ``w``,
    a coordinator ``c``, a broker ``k``, operators ``op`` / ``op2``, an
    outsider ``x``."""

    def __init__(self):
        self.ids = {n: Identity.generate() for n in ("user", "s", "p", "w", "k", "c", "op", "op2", "x")}
        self.did = {n: i.did for n, i in self.ids.items()}
        self.trust = self.trust_with()
        lease = make_lease(self.ids["k"], self.did["p"], "m/a", key_version=1, canary=new_canary(), clock=clock)
        req = make_request(self.ids["user"], ["m/a"], "hi", clock=clock)
        dec = make_decision(self.ids["s"], req, [Leg(self.did["p"], "m/a", (self.did["s"], self.did["w"],
                                                                              self.did["p"]))],
                            mode="normal", epoch=7, clock=clock)
        a = make_response(self.ids["p"], req, dec, "m/a", lease, output="yes", clock=clock)
        b = make_response(self.ids["p"], req, dec, "m/a", lease, output="no", clock=clock)
        self.incident = make_incident(self.ids["s"], PROVIDER_EQUIVOCATION, {"a": a, "b": b}, clock=clock)
        self.target = node_target(self.did["p"])

    def trust_with(self, *, mesh=("s", "p", "w", "k", "c"), operators=("op",)):
        d = self.did
        return TrustRoots(mesh=Allowlist({d[n] for n in mesh}), users=Allowlist({d["user"]}),
                          deciders=Allowlist({d["s"]}), coordinators=Allowlist({d["c"]}),
                          brokers=Allowlist({d["k"]}), operators=Allowlist({d[n] for n in operators}))

    def reset(self, iid, by="op"):
        return make_reset(self.ids[by], [self.target], [iid], clock=clock)


@pytest.fixture
def w():
    return World()


@pytest.fixture
def store(tmp_path):
    return LedgerStore(tmp_path / "ledger.jsonl")


def lines(store):
    return store.path.read_text(encoding="utf-8").splitlines()


def test_latches_survive_a_restart(w, store):
    inc = MutinyLedger(w.trust, store=store).add(w.incident)
    assert inc is not None

    again = MutinyLedger(w.trust, store=store)
    assert again.lockdown and again.latched() == {w.target: frozenset({inc.incident_id})}
    assert again.unverified() == []          # still verifies: an ordinary incident
    assert again.signed_incidents() == [w.incident]


def test_a_reset_survives_a_restart_and_the_incident_stays_cleared(w, store):
    ledger = MutinyLedger(w.trust, store=store)
    inc = ledger.add(w.incident)
    assert ledger.reset(w.reset(inc.incident_id)) == (True, frozenset({w.target}))

    again = MutinyLedger(w.trust, store=store)
    assert not again.lockdown
    assert again.add(w.incident) is None and not again.lockdown  # re-delivered: already known, cleared


def test_revoking_the_mutinous_node_does_not_end_lockdown_on_restart(w, store):
    """The operator's natural move after a mutiny is to drop the node from the
    mesh. That makes its signed answers -- the evidence -- stop verifying. The
    restart must still come back in lockdown, until an operator reset."""
    inc = MutinyLedger(w.trust, store=store).add(w.incident)
    without_p = w.trust_with(mesh=("s", "w", "k", "c"))

    again = MutinyLedger(without_p, store=store)
    assert again.lockdown and again.is_latched(w.target)
    assert again.unverified() == [inc.incident_id]
    assert again.signed_incidents() == []    # peers could not verify it either: not handed on

    assert again.reset(w.reset(inc.incident_id)) == (True, frozenset({w.target}))
    assert not MutinyLedger(without_p, store=store).lockdown  # and the reset itself persists


def test_an_unverified_incident_is_upgraded_when_it_verifies_again(w, store):
    MutinyLedger(w.trust, store=store).add(w.incident)
    held = MutinyLedger(w.trust_with(mesh=("s", "w", "k", "c")), store=store)
    assert held.unverified()

    restored = MutinyLedger(w.trust, store=store)  # p's signature counts again
    assert restored.unverified() == [] and restored.signed_incidents() == [w.incident]


def test_a_reset_from_a_removed_operator_does_not_replay(w, store):
    ledger = MutinyLedger(w.trust, store=store)
    inc = ledger.add(w.incident)
    ledger.reset(w.reset(inc.incident_id))
    assert not ledger.lockdown

    after = MutinyLedger(w.trust_with(operators=("op2",)), store=store)
    assert after.lockdown and after.is_latched(w.target)  # fails closed: op2 must review it


def test_forged_torn_and_tampered_lines_never_lift_lockdown(w, store):
    inc = MutinyLedger(w.trust, store=store).add(w.incident)
    good = json.loads(lines(store)[0])
    tampered = {**good, "obj": {**good["obj"], "incident_id": "0" * 64}}
    bad_targets = {**good, "targets": ["node:not-a-did"]}
    with store.path.open("a", encoding="utf-8") as f:
        f.write('{"t": "reset", "obj": {"truncated\n')                     # torn write
        f.write("not json at all\n")
        f.write(json.dumps({"t": "reset", "obj": w.reset(inc.incident_id, by="x")}) + "\n")  # forged reset
        f.write(json.dumps({"t": "incident", "obj": tampered["obj"]}) + "\n")
        f.write(json.dumps(bad_targets) + "\n")
        f.write(json.dumps(["a", "list"]) + "\n")

    again = MutinyLedger(w.trust, store=store)
    assert again.latched() == {w.target: frozenset({inc.incident_id})}


def test_the_store_can_add_lockdown_but_not_remove_it(w, store):
    """Whoever can write the store is a trusted operator (SECURITY.md). A whole
    but unverifiable incident line latches -- fail closed -- and still needs an
    operator's signed reset, like any other latch."""
    fake = make_incident(w.ids["x"], PROVIDER_EQUIVOCATION, {"a": {}, "b": {}}, clock=clock)
    store.append({"t": "incident", "obj": fake, "targets": [w.target]})

    ledger = MutinyLedger(w.trust, store=store)
    assert ledger.lockdown and ledger.unverified() == [fake["incident_id"]]
    assert ledger.reset(w.reset(fake["incident_id"])) == (True, frozenset({w.target}))


def test_replay_order_does_not_matter(w, store):
    ledger = MutinyLedger(w.trust, store=store)
    inc = ledger.add(w.incident)
    ledger.reset(w.reset(inc.incident_id))
    reversed_store = LedgerStore(store.path.with_name("reversed.jsonl"))
    for line in reversed(lines(store)):
        reversed_store.append(json.loads(line))   # the reset first, then its incident

    assert not MutinyLedger(w.trust, store=reversed_store).lockdown


def test_store_is_private_and_not_rewritten_on_redelivery(w, store):
    MutinyLedger(w.trust, store=store).add(w.incident)
    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600

    again = MutinyLedger(w.trust, store=store)
    assert again.add(w.incident) is None
    assert len(lines(store)) == 1


def test_a_failing_store_still_latches(w, tmp_path):
    class Broken(LedgerStore):
        def append(self, obj):
            raise OSError("disk full")

    ledger = MutinyLedger(w.trust, store=Broken(tmp_path / "x.jsonl"))
    assert ledger.add(w.incident) is not None
    assert ledger.lockdown and ledger.store_errors == 1


class _Wire:
    def send(self, to_did, payload):
        return True

    def connect(self, did):
        pass


def test_participants_restart_in_lockdown(w, store):
    MutinyLedger(w.trust, store=store).add(w.incident)

    coordinator = Coordinator(w.ids["c"], _Wire(), w.trust, clock=clock, ledger_store=store)
    forwarder = MeshNode(w.ids["w"], _Wire(), w.trust, roles={"forward"}, clock=clock, ledger_store=store)
    assert coordinator.mode() == MODE_LOCKDOWN and forwarder.mode() == MODE_LOCKDOWN
    assert forwarder.ledger.excludes_node(w.did["p"])

    fresh = MeshNode(w.ids["w"], _Wire(), w.trust, roles={"forward"}, clock=clock)
    assert fresh.mode() == MODE_NORMAL  # without a store, nothing carries over (as before)
