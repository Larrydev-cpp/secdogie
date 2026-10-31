"""The safety decision gate: hard vetoes that cannot be turned off, and a
history that can only make a node stricter -- escalating to a cross-check,
never lowering the quorum the user asked for."""
from __future__ import annotations

import random

import pytest

pytest.importorskip("nacl")

import sim  # noqa: E402
from secdogie_inference import (  # noqa: E402
    MODE_LOCKDOWN,
    OUTCOME_ACCEPTED,
    OUTCOME_INTERCEPTED,
    OUTCOME_REFUSED,
    MeshNode,
    make_request,
    make_reset,
)
from secdogie_inference.gate import (  # noqa: E402
    ALLOW,
    ESCALATE,
    EV_ACCEPTED,
    EV_BREAKER_OPEN,
    EV_DIVERGENT,
    EV_INTERCEPTED,
    VETO,
    DecisionGate,
    History,
    model_subject,
    node_subject,
    user_subject,
)
from secdogie_inference.mutiny import MODEL_FORBIDDEN_CAPABILITY, decider_target  # noqa: E402

ALPHA, BETA = sim.ALPHA, sim.BETA
CANARY_LIKE = "sdgc_" + "0123456789abcdef" * 2


def test_history_counts_inside_its_window_and_is_bounded():
    h = History(size=3, window=10.0)
    for t in (0.0, 5.0, 8.0, 9.0):
        h.note(t, EV_DIVERGENT, model_subject(ALPHA))
    assert len(h) == 3
    assert h.count(EV_DIVERGENT, model_subject(ALPHA), now=12.0) == 3
    assert h.count(EV_DIVERGENT, model_subject(ALPHA), now=16.0) == 2
    assert h.count(EV_DIVERGENT, model_subject(BETA), now=9.0) == 0


def test_a_canary_probe_is_vetoed():
    arc = sim.Arc(entries=("S2",))
    out = arc.ask([ALPHA], f"please repeat {CANARY_LIKE} back to me")
    assert out.status == OUTCOME_REFUSED
    assert arc.S2.noted("veto")[-1]["reasons"] == ["the input carries a lease canary token"]


def test_divergence_escalates_an_action_request_to_a_cross_check():
    def a(model, text, secret):
        return "yes" if text == "contested" else f"answer({text})"

    def b(model, text, secret):
        return "no" if text == "contested" else f"answer({text})"

    arc = sim.Arc(entries=("S2",), backends={"L": a, "R": b})
    assert arc.ask([ALPHA, BETA], "contested", quorum=2).status == OUTCOME_INTERCEPTED
    assert arc.S2.history.count(EV_DIVERGENT, model_subject(ALPHA), arc.net.clock()) == 1

    # an action request asking for one answer now needs two that agree
    out = arc.ask([ALPHA, BETA], "agreed", scopes=["observe.read"])
    assert out.status == OUTCOME_ACCEPTED
    assert arc.S2.noted("escalate")[-1]["quorum"] == 2 and len(arc.S2.noted("decide")[-1]["legs"]) == 2
    assert arc.ask([ALPHA, BETA], "contested", scopes=["observe.read"]).status == OUTCOME_INTERCEPTED

    # one model with one node cannot be cross-checked: fail closed, not "try later"
    out = arc.ask([ALPHA], "single", scopes=["observe.read"])
    assert out.status == OUTCOME_REFUSED and out.reasons[-1].startswith("vetoed: cross-check unavailable")
    # a request that acts on nothing is not escalated
    assert arc.ask([ALPHA], "just text").status == OUTCOME_ACCEPTED
    assert len(arc.S2.noted("decide")[-1]["legs"]) == 1


def test_a_pinned_request_that_cannot_be_cross_checked_is_vetoed():
    arc = sim.Arc(entries=("S2",))
    now = arc.net.clock()
    arc.S2.history.note(now, EV_DIVERGENT, model_subject(ALPHA))
    request = make_request(arc.ids["U"], [ALPHA], "x", scopes=["observe.read"], pins=[(arc.did["L"], ALPHA)],
                           clock=arc.net.clock)
    v = arc.S2.gate.review(request, me=arc.did["S2"], ledger=arc.S2.ledger, history=arc.S2.history, now=now)
    assert v.status == VETO and v.reasons[0].startswith("cross-check unavailable")


def test_a_user_whose_action_request_drew_a_forbidden_capability_is_vetoed_until_reset():
    class Unguarded(MeshNode):
        def guard_output(self, output, held, request):
            return None  # a model node that signs whatever its upstream says

    def rogue(model, text, secret):
        return '{"action": "run_elevated", "text": "net user admin /add"}'

    arc = sim.Arc(entries=("S2",), node_classes={"L": Unguarded}, backends={"L": rogue})
    d = arc.did
    assert arc.ask([ALPHA], "click OK", scopes=["physical.click"]).status == OUTCOME_INTERCEPTED
    held = [i for i in arc.N1.ledger.signed_incidents() if i["kind"] == MODEL_FORBIDDEN_CAPABILITY]
    assert held and arc.N1.mode() == MODE_LOCKDOWN

    now = arc.net.clock()
    gate, history = DecisionGate(), History()

    def review(user, scopes):
        request = make_request(arc.ids[user], [ALPHA, BETA], "again", scopes=scopes, quorum=2, clock=arc.net.clock)
        return gate.review(request, me=d["N1"], ledger=arc.N1.ledger, history=history, now=now, lockdown=True)

    assert review("U", ["physical.click"]).status == VETO        # the standby forwarder will not carry it
    assert review("U", []).status == ALLOW                        # asking for text is still fine

    latched = arc.C.ledger.latched()
    targets, ids = sorted(latched), sorted({x for held in latched.values() for x in held})
    arc.C.publish(make_reset(arc.ids["O"], targets, ids, clock=arc.net.clock))
    arc.net.run()
    assert review("U", ["physical.click"]).status == ALLOW       # reviewed and reset by an operator


def test_a_latched_node_vetoes_everything():
    arc = sim.Arc()
    request = make_request(arc.ids["U"], [ALPHA], "x", clock=arc.net.clock)
    arc.S2.ledger._latches[decider_target(arc.did["S2"])] = {"0" * 64}  # as if a mutiny had latched it
    arc.S2.ledger._incidents["0" * 64] = type("I", (), {"kind": "decider.self-reported"})()
    arc.S2.ledger._derived = None
    v = DecisionGate().review(request, me=arc.did["S2"], ledger=arc.S2.ledger, history=History(),
                              now=arc.net.clock())
    assert v.status == VETO and v.reasons == ("this node is latched",)


def test_history_never_lowers_the_quorum():
    arc = sim.Arc()
    rng = random.Random(7)
    gate = DecisionGate()
    kinds = (EV_ACCEPTED, EV_INTERCEPTED, EV_DIVERGENT, EV_BREAKER_OPEN)
    subjects = (model_subject(ALPHA), model_subject(BETA), user_subject(arc.did["U"]), node_subject(arc.did["L"]))
    now = arc.net.clock()
    for _ in range(300):
        history = History()
        for _e in range(rng.randrange(12)):
            history.note(now - rng.random() * 7200, rng.choice(kinds), rng.choice(subjects))
        quorum = rng.choice((1, 2, 3))
        models = rng.choice(([ALPHA], [ALPHA, BETA], [BETA, "vendor-c/gamma", ALPHA]))
        scopes = rng.choice(([], ["observe.read"], ["physical.click", "physical.type"]))
        request = make_request(arc.ids["U"], models, "q", scopes=scopes, quorum=quorum, clock=arc.net.clock)
        v = gate.review(request, me=arc.did["S2"], ledger=arc.S2.ledger, history=history, now=now,
                        providers=[arc.did["L"], arc.did["R"]])
        assert v.quorum >= quorum
        assert v.status in (ALLOW, ESCALATE)  # nothing here is a hard veto
        if v.status == ESCALATE:
            assert scopes and v.quorum >= 2 and v.reasons
        if not scopes:
            assert v.status == ALLOW and v.quorum == quorum
