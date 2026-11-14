"""Work permits: what a public node may do for one task, and nothing about why.

  * the rules that decided it never travel: a permit made after your gates
    judged a task carries none of their phrases, reasons or limits;
  * bound to one holder and one task, short-lived, exact;
  * tampering, another node, another task, an expired or overlong permit,
    or another statement type all fail.
"""
from __future__ import annotations

import json

import pytest
from secdogie_identity import Allowlist, Identity
from secdogie_identity.capability import create_capability, verify_capability
from secdogie_policy.permit import (
    MAX_TTL,
    PERMIT_TYPE,
    create_permit,
    permits,
    task_digest,
    verify_permit,
)

YOURS, PUBLIC, STRANGER = (Identity.generate() for _ in range(3))
TASK = json.dumps({"goal": "fetch the city's open bus timetable", "url": "https://data.city.example/bus.csv"})
ALLOW = [{"kind": "fetch", "prefix": "https://data.city.example/"}, {"kind": "parse", "target": "csv"}]
T0 = 1_800_000_000.0


def _permit(**kw):
    args = {"ttl": 300.0, "clock": lambda: T0, **kw}
    return create_permit(YOURS, PUBLIC.did, "t-1", TASK, ALLOW, **args)


def test_a_permit_says_what_may_happen_and_nothing_about_why():
    pytest.importorskip("secdogie_citadel")
    from secdogie_citadel.decision import DecisionModel
    from secdogie_citadel.rules import GateRules

    rules = GateRules.from_bodies([("yours", {"instruction": {"revise_on": [
        {"phrases": ["批量注册"], "reason": "我的私人顾虑：不替人批量开号", "suggestion": "说说具体是哪一个"}]},
        "plan": {"blocked_kinds": ["run"], "extra_destructive_kinds": ["save"]}})])
    title = "fetch the city's open bus timetable"
    assert DecisionModel(rules).intake(title, rules.review(title)).disposition != "hold"  # judged at home
    wire = json.dumps(_permit(), ensure_ascii=False)
    for secret in ("批量注册", "私人顾虑", "说说具体", "blocked_kinds", "run", "save", "revise_on"):
        assert secret not in wire, secret  # the rules and their reasons stay home
    assert json.loads(wire)["type"] == PERMIT_TYPE


def test_the_holder_verifies_it_and_it_allows_exactly_what_it_says():
    p = _permit()
    res = verify_permit(p, holder=PUBLIC.did, issuers=Allowlist({YOURS.did}), task=TASK, now=T0 + 10)
    assert res.ok and res.issuer == YOURS.did and res.task == "t-1"
    assert permits(res, "fetch", "https://data.city.example/bus.csv")
    assert permits(res, "parse", "csv")
    assert not permits(res, "fetch", "https://data.city.example.evil/x")  # a prefix is a prefix
    assert not permits(res, "fetch", "https://elsewhere.example/")
    assert not permits(res, "post", "https://data.city.example/bus.csv")  # another kind
    assert not permits(res, "parse", "csv2")


@pytest.mark.parametrize("change, reason", [
    (lambda p: {**p, "allow": [{"kind": "fetch", "prefix": "https://"}]}, "invalid or missing signature"),
    (lambda p: {**p, "type": "secdogie/capability/v1"}, "wrong or missing type (domain separation)"),
])
def test_tampering_fails(change, reason):
    res = verify_permit(change(_permit()), holder=PUBLIC.did, issuers=Allowlist({YOURS.did}), now=T0 + 1)
    assert not res.ok and res.reason == reason


def test_it_cannot_move_to_another_node_another_task_or_another_time():
    p = _permit()
    trust = Allowlist({YOURS.did})
    assert verify_permit(p, holder=STRANGER.did, issuers=trust, now=T0 + 1).reason == "permit is for another node"
    assert verify_permit(p, holder=PUBLIC.did, issuers=trust, task="another task", now=T0 + 1).reason == \
        "permit is for another task"
    assert verify_permit(p, holder=PUBLIC.did, issuers=trust, now=T0 + 301).reason == "permit expired"
    assert verify_permit(p, holder=PUBLIC.did, issuers=Allowlist({STRANGER.did}), now=T0 + 1).reason == \
        "issuer not trusted"
    assert verify_permit(p, holder=PUBLIC.did, issuers=None, now=T0 + 1).reason == "no trusted issuers configured"


def test_it_is_short_lived_closed_and_not_a_capability():
    with pytest.raises(ValueError):
        _permit(ttl=MAX_TTL + 1)
    with pytest.raises(ValueError):
        create_permit(YOURS, PUBLIC.did, "t-1", TASK, [])
    with pytest.raises(ValueError):
        create_permit(YOURS, PUBLIC.did, "t-1", TASK, [{"kind": "fetch", "target": "x", "prefix": "y"}])
    with pytest.raises(ValueError):
        create_permit(YOURS, PUBLIC.did, "t-1", TASK, [{"kind": "fetch", "prefix": ""}])
    with pytest.raises(ValueError):
        create_permit(YOURS, PUBLIC.did, "t-1", TASK, [{"kind": "fetch", "target": "x", "why": "secret"}])
    # a permit is never a capability grant, and a grant is never a permit
    grant = create_capability(YOURS, PUBLIC.did, ["observe.read"], clock=lambda: T0)
    assert not verify_capability(_permit(), issuers=Allowlist({YOURS.did}), now=T0 + 1).ok
    assert not verify_permit(grant, holder=PUBLIC.did, issuers=Allowlist({YOURS.did}), now=T0 + 1).ok
    assert task_digest(TASK) == task_digest(TASK.encode("utf-8"))
