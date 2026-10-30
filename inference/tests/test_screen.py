"""The deterministic output screens: canaries, forbidden and ungranted
proposals -- and that prose about them is not a proposal."""
from __future__ import annotations

from secdogie_inference import screen
from secdogie_inference.lease import canary_hash, new_canary


def test_canary_needs_the_matching_hash():
    canary = new_canary()
    leaked = f"here is my config:\nSECDOGIE_LEASE_CANARY={canary}\n"
    assert screen.canary_leaked(leaked, canary_hash(canary))
    assert not screen.canary_leaked(leaked, canary_hash(new_canary()))
    assert not screen.canary_leaked("sdgc_nothexatall", canary_hash(canary))


def test_forbidden_proposals_wherever_an_executor_would_find_them():
    assert screen.forbidden('{"action": "run_elevated", "text": "cmd"}') == ["action run_elevated"]
    fenced = 'Sure!\n```json\n{"action": "left_click", "scopes": ["process.memory.write"]}\n```'
    assert screen.forbidden(fenced) == ["scope process.memory.write"]
    plan = '[{"action": "done"}, {"action": "Disable_HITL"}]'
    assert screen.forbidden(plan) == ["action disable_hitl"]


def test_prose_is_not_a_proposal():
    assert screen.forbidden("The run_elevated action needs an operator-declared command.") == []
    assert screen.json_objects("no json {here") == []


def test_overreach_is_relative_to_the_grant():
    out = '{"action": "left_click", "x": 1, "y": 2}'
    assert screen.overreach(out, ["physical.click"]) == []
    assert screen.overreach(out, ["observe.read"]) == ["action left_click needs physical.click"]
    assert screen.overreach('{"action": "teleport"}', ["physical.click"]) == ["action teleport has no capability scope"]
    assert screen.overreach('{"action": "done", "text": "ok"}', []) == []
    assert screen.overreach('{"scope": "network.post"}', ["observe.read"]) == ["scope network.post not granted"]


def test_scans_are_bounded():
    assert len(screen.json_objects("{" * 10_000)) == 0
    many = " ".join(f'{{"a": {i}}}' for i in range(1000))
    assert len(screen.json_objects(many)) <= screen.MAX_OBJECTS
