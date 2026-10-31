"""The three drawings, end to end on the simulated mesh (see sim.Arc).

  1. "new arc"        -- normal routing over the P2P mesh;
  2. "当节点失效"      -- nodes fail: central fallback, repairs, quarantine;
  3. "决策模型叛变时"   -- mutiny: evidence, lockdown, the new P2P, the reset.
"""
from __future__ import annotations

import pytest

pytest.importorskip("nacl")

import sim  # noqa: E402
from secdogie_inference import (  # noqa: E402
    MODE_DEGRADED,
    MODE_LOCKDOWN,
    MODE_NORMAL,
    OUTCOME_ACCEPTED,
    OUTCOME_INTERCEPTED,
    OUTCOME_REFUSED,
    Coordinator,
    MeshNode,
    Router,
    make_incident,
    make_request,
    make_reset,
)
from secdogie_inference.health import HEALTHY  # noqa: E402
from secdogie_inference.mutiny import (  # noqa: E402
    DECIDER_SELF_REPORTED,
    DECIDER_TAMPERING,
    MODEL_SELF_REPORTED,
    PROVIDER_EQUIVOCATION,
    PROVIDER_SECRET_EXFILTRATION,
    decider_target,
    model_target,
)
from secdogie_inference.protocol import ADVISORY, STATUS_OK, VIA_CENTRAL, make_advisory  # noqa: E402

ALPHA, BETA = sim.ALPHA, sim.BETA


def names(arc, dids):
    return sorted(arc.short(d) for d in dids)


def decisions(node):
    return node.noted("decide")


class Downgrade(Router):
    """A decision model that quietly drops the user's quorum to one."""

    def plan(self, view, request, *, quorum, avoid=frozenset()):
        return super().plan(view, request, quorum=quorum, avoid=avoid)[:1]


def lock_down(arc):
    """Put the mesh in lockdown the way state 3 starts: a decider reports its
    own decision model."""
    ev = {"reporter": arc.did["S2"], "proposal": {"legs": []}, "violation": "quorum-downgrade"}
    arc.S2.publish(make_incident(arc.ids["S2"], DECIDER_SELF_REPORTED, ev, clock=arc.net.clock))
    arc.net.run()
    assert all(p.mode() == MODE_LOCKDOWN for p in arc.everyone())


# -- state 1: the new arc ---------------------------------------------------------------

def test_normal_routing_takes_the_nearest_model_node():
    arc = sim.Arc(entries=("S2",))
    assert {p.mode() for p in arc.everyone()} == {MODE_NORMAL}
    out = arc.ask([ALPHA, BETA])
    assert (out.status, out.output, names(arc, out.providers)) == (OUTCOME_ACCEPTED, "answer(hello)", ["L"])
    assert decisions(arc.S2)[-1]["legs"] == [(arc.did["L"][-8:], "mesh", 3)]  # S2 -> S1 -> L


def test_a_quorum_of_two_uses_both_vendors_on_disjoint_paths():
    arc = sim.Arc(entries=("S2",))
    out = arc.ask([ALPHA, BETA], quorum=2)
    assert out.status == OUTCOME_ACCEPTED and names(arc, out.providers) == ["L", "R"]
    legs = decisions(arc.S2)[-1]["legs"]
    assert sorted(n for _p, _v, n in legs) == [3, 4]  # S2-S1-L and S2-S3-S4-R share no interior node


def test_the_user_does_not_take_the_deciders_word():
    class Liar(MeshNode):
        def _finish(self, user, rhash, kind, **fields):
            for _d, r in fields.get("items", []):
                r["output"] = "the model said to wire me money"  # breaks the model node's signature
            super()._finish(user, rhash, kind, **fields)

    arc = sim.Arc(node_classes={"S2": Liar}, entries=("S2", "S3"))
    out = arc.ask([ALPHA])
    assert out.status == OUTCOME_ACCEPTED and out.output == "answer(hello)"
    assert arc.short(out.via) == "S3"  # the forged result was worthless; the next entry answered


def test_fanout_reaches_two_entries_at_once():
    arc = sim.Arc(fanout=2)
    out = arc.ask([BETA])
    arc.net.settle(1)
    assert out.status == OUTCOME_ACCEPTED
    assert decisions(arc.S2) and decisions(arc.S3)  # both entries routed it
    assert len(arc.R._served) == 1  # answered once; the second path got the same answer


# -- state 2: nodes fail -------------------------------------------------------------------

class NoBypass(Coordinator):
    """A central server whose advice has no bypass links: the mesh cannot heal
    around a down node, so only the central fallback is left."""

    def advise(self, now):
        self.standby = []  # nobody to recruit either
        return super().advise(now)

    def publish(self, obj):
        if obj.get("type") == ADVISORY and obj.get("bypass"):
            obj = make_advisory(self.identity, epoch=obj["epoch"], failed=obj["failed"],
                                quarantined=obj["quarantined"], clock=self.clock)
        return super().publish(obj)


def test_when_a_decider_fails_its_neighbours_connect_to_the_central_server():
    arc = sim.Arc(entries=("S2",), coordinator_class=NoBypass)
    d = arc.did
    arc.net.down.add(d["S1"])
    arc.net.settle(8)  # S1 ages out; the coordinator advises; L and S2 link to it
    assert arc.S2.advisories[d["C"]]["failed"] == [d["S1"]] and arc.S2.mode() == MODE_DEGRADED
    assert {d["L"], d["S2"]} <= arc.C.neighbors
    assert d["C"] in arc.L.neighbors and d["C"] in arc.S2.neighbors
    assert d["L"] not in arc.S2.neighbors  # no bypass: the central server is the only way to L

    out = arc.ask([ALPHA])
    assert out.status == OUTCOME_ACCEPTED and names(arc, out.providers) == ["L"]
    assert decisions(arc.S2)[-1]["legs"][0][1:] == (VIA_CENTRAL, 3)  # S2 -> C -> L


def test_a_request_caught_by_the_failure_still_gets_through_the_central_server():
    arc = sim.Arc(entries=("S2",), coordinator_class=NoBypass)
    d = arc.did
    arc.net.down.add(d["S1"])  # crashes just as the user asks
    out = arc.ask([ALPHA])
    assert out.status == OUTCOME_ACCEPTED and names(arc, out.providers) == ["L"]
    legs = [x["legs"][0][1] for x in decisions(arc.S2)]
    assert legs[0] == "mesh" and legs[-1] == VIA_CENTRAL  # timed out on S1, then went central


def test_a_crashed_decider_is_bypassed_and_the_model_node_gets_a_new_p2p():
    arc = sim.Arc(entries=("S2",))
    d = arc.did
    arc.net.down.add(d["S1"])
    arc.net.settle(6)  # S1 ages out; the coordinator advises; the repairs are confirmed
    advisory = arc.S2.advisories[d["C"]]
    assert advisory["failed"] == [d["S1"]] and arc.S2.mode() == MODE_DEGRADED
    # drawing 2: the bypass arc, the central server, and a new P2P hung off the API key
    assert {d["S2"], d["C"], d["N1"]} <= arc.L.neighbors
    assert {d["L"], d["K"]} <= arc.N1.neighbors and d["S2"] not in arc.N1.neighbors
    out = arc.ask([ALPHA])
    assert out.status == OUTCOME_ACCEPTED and decisions(arc.S2)[-1]["legs"][0][1:] == ("mesh", 2)


def test_an_orphaned_model_node_gets_a_standby_forwarder():
    arc = sim.Arc(entries=("S3",))
    d = arc.did
    arc.net.down.add(d["S4"])
    arc.net.settle(6)
    assert d["N1"] in arc.R.neighbors  # the recruited new P2P
    assert d["C"] in arc.R.neighbors   # and the central server attached itself
    assert d["S3"] in arc.R.neighbors  # and the bypass around S4
    out = arc.ask([BETA])
    assert out.status == OUTCOME_ACCEPTED


def test_a_black_hole_is_quarantined_by_two_deciders_and_later_reinstated():
    arc = sim.Arc(entries=("S3", "S2"))
    d = arc.did
    arc.net.drop[d["S4"]] = lambda src, f: f["k"] == "req"  # heartbeats fine, traffic dropped
    for entry in ("S3", "S2"):  # each decider times out on S4 until it stops trusting it, and reports it
        arc.U.entries = [d[entry]]
        assert arc.ask([BETA]).status == OUTCOME_REFUSED
    now = arc.net.clock()
    assert arc.C.faults.quarantined(now) == {d["S4"]}
    assert arc.C.faults.status(d["S3"], now) == HEALTHY  # accused by S2 alone: one voice is not enough

    arc.net.settle(3)
    assert d["R"] in arc.S3.neighbors  # the bypass around S4
    arc.U.entries = [d["S3"]]
    out = arc.ask([BETA])
    assert out.status == OUTCOME_ACCEPTED and decisions(arc.S3)[-1]["legs"][0][1:] == ("mesh", 2)

    del arc.net.drop[d["S4"]]
    arc.net.until(lambda: arc.C.faults.status(d["S4"], arc.net.clock()) == HEALTHY, limit=120)
    arc.net.settle(3)
    assert arc.S3.advisories[d["C"]]["quarantined"] == []
    assert d["S4"] not in arc.S3.excluded(arc.net.clock())  # the crossed-out "wrong": reinstated


# -- state 3: mutiny -----------------------------------------------------------------------

def test_a_decision_model_that_mutinies_is_caught_by_its_own_node():
    arc = sim.Arc(entries=("S2",), policies={"S2": Downgrade()})
    d = arc.did
    out = arc.ask([ALPHA, BETA], quorum=2)

    assert all(p.ledger.is_latched(decider_target(d["S2"])) for p in arc.everyone())
    assert all(p.mode() == MODE_LOCKDOWN for p in arc.everyone())
    # the user went around the decision layer: the new P2P, both model nodes, both must agree
    assert (out.status, out.mode, arc.short(out.via)) == (OUTCOME_ACCEPTED, MODE_LOCKDOWN, "N1")
    assert names(arc, out.providers) == ["L", "R"]
    assert {d["L"], d["R"]} <= arc.N1.neighbors

    # a single model cannot be cross-checked, so lockdown refuses it
    assert arc.ask([ALPHA], "x").status == OUTCOME_REFUSED
    # serving nodes take nothing, the central server carries nothing
    assert arc.S3.may_carry({"decider": d["S3"], "mode": MODE_NORMAL}, 0) is not None
    assert arc.C.may_carry({"decider": d["S3"], "mode": MODE_NORMAL}, 0).startswith("lockdown")

    # the operator reviews the evidence and signs the way back
    arc.S2.policy = Router()
    ids = sorted(arc.C.ledger.latched()[decider_target(d["S2"])])
    forged = make_reset(arc.ids["U"], [decider_target(d["S2"])], ids, clock=arc.net.clock)
    assert not arc.C.publish(forged)
    assert arc.C.publish(make_reset(arc.ids["O"], [decider_target(d["S2"])], ids, clock=arc.net.clock))
    arc.net.run()
    assert {p.mode() for p in arc.everyone()} == {MODE_NORMAL}
    out = arc.ask([ALPHA, BETA], quorum=2)
    assert (out.status, out.mode, arc.short(out.via)) == (OUTCOME_ACCEPTED, MODE_NORMAL, "S2")


def test_a_decider_that_signs_a_bad_route_is_convicted_by_the_model_node():
    class Unchecked(MeshNode):
        def _self_check(self, p, legs, now):
            return []  # a compromised host: its guard is gone too

    arc = sim.Arc(entries=("S3",), node_classes={"S3": Unchecked}, policies={"S3": Downgrade()})
    out = arc.ask([ALPHA, BETA], quorum=2)
    incidents = arc.C.ledger.signed_incidents()
    assert [i["kind"] for i in incidents] == [DECIDER_TAMPERING]
    assert arc.short(incidents[0]["reporter"]) in ("L", "R")
    assert out.status == OUTCOME_ACCEPTED and out.mode == MODE_LOCKDOWN


def test_lease_material_in_an_answer_latches_the_node_and_rotates_the_key():
    class Leaky(MeshNode):
        def _answer(self, request, model, held):
            return STATUS_OK, f"debug dump:\nSECDOGIE_LEASE_CANARY={held.canary}\n", ""

    arc = sim.Arc(entries=("S3",), node_classes={"R": Leaky})
    d = arc.did
    assert arc.keys.current(BETA)[0] == 1
    out = arc.ask([BETA])
    assert out.status == OUTCOME_INTERCEPTED and out.output is None  # withheld, not delivered with a warning
    assert [i["kind"] for i in arc.C.ledger.signed_incidents()] == [PROVIDER_SECRET_EXFILTRATION]
    assert all(p.ledger.excludes_node(d["R"]) for p in arc.everyone())
    assert arc.keys.current(BETA)[0] == 2  # the exposed key is dead
    assert arc.R._held == {}  # every lease it held is revoked
    arc.net.settle(3)
    assert not any(lease["subject"] == d["R"] for lease in arc.S3.leases.live(arc.net.clock()))


def test_an_upstream_model_proposing_a_forbidden_capability_is_reported_by_its_honest_node():
    def rogue(model, text, secret):
        return '{"action": "run_elevated", "text": "net user admin /add"}'

    arc = sim.Arc(entries=("S2",), backends={"L": rogue})
    d = arc.did
    out = arc.ask([ALPHA], "click the OK button", scopes=["physical.click"])
    assert out.status == OUTCOME_REFUSED
    inc = arc.C.ledger.signed_incidents()
    assert [(i["kind"], arc.short(i["reporter"])) for i in inc] == [(MODEL_SELF_REPORTED, "L")]
    assert all(p.ledger.is_latched(model_target(ALPHA)) for p in arc.everyone())
    assert not arc.C.ledger.excludes_node(d["L"])  # the node told on its model; it is not the culprit
    assert arc.keys.current(ALPHA)[0] == 2


def test_multipath_catches_an_equivocating_model_node():
    class TwoFaced(MeshNode):
        calls = 0

        def serve(self, request, decision, leg_i, now):
            self._served.clear()  # forgets what it said
            super().serve(request, decision, leg_i, now)

        def _answer(self, request, model, held):
            TwoFaced.calls += 1
            return STATUS_OK, f"answer #{TwoFaced.calls}", ""

    arc = sim.Arc(fanout=2, node_classes={"L": TwoFaced})
    arc.ask([ALPHA])
    arc.net.settle(2)
    kinds = [i["kind"] for i in arc.C.ledger.signed_incidents()]
    assert kinds == [PROVIDER_EQUIVOCATION]
    assert arc.C.ledger.excludes_node(arc.did["L"])


def test_in_lockdown_disagreeing_model_nodes_are_intercepted():
    def a(model, text, secret):
        return "yes" if text == "contested" else f"answer({text})"

    def b(model, text, secret):
        return "no" if text == "contested" else f"answer({text})"

    arc = sim.Arc(backends={"L": a, "R": b})
    lock_down(arc)
    assert arc.ask([ALPHA, BETA], "agreed").status == OUTCOME_ACCEPTED
    out = arc.ask([ALPHA, BETA], "contested")
    assert out.status == OUTCOME_INTERCEPTED and "the model nodes disagree" in out.reasons


def test_in_lockdown_serving_nodes_refuse_and_a_user_without_standby_is_refused():
    arc = sim.Arc(entries=("S2",))
    lock_down(arc)
    seen = []
    arc.net.drop[arc.did["U"]] = lambda src, f: seen.append(f) or True  # read what reaches the user
    request = make_request(arc.ids["U"], [ALPHA, BETA], "hi", quorum=2, clock=arc.net.clock)
    arc.U.send(arc.did["S2"], "submit", request=request)
    arc.net.run()
    assert [f["reason"] for f in seen if f["k"] == "refused"] == [
        "lockdown: the decision layer is suspended; use a standby forwarder"]
    del arc.net.drop[arc.did["U"]]
    arc.U.standby = []
    out = arc.ask([ALPHA, BETA], quorum=2)
    assert out.status == OUTCOME_REFUSED and out.reasons == ("lockdown: no standby forwarder",)
