"""The golden vectors in fixtures/vectors/ are what the Rust (graph/) and
TypeScript (symbiont/) implementations must reproduce byte for byte. They are
generated from the production Python functions; this test regenerates them and
fails if the committed files drifted -- so a change to canonical JSON, the
authorization token, the dialogue envelope or the Socratic rules cannot land
without the other languages' vectors moving with it.

It also checks the vectors from the Python side: the stored token and the
sealed challenge verify with the real verifiers."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from secdogie_citadel.authz import verify_authorization
from secdogie_dialogue.protocol import (
    Gate2ChallengePacket,
    PacketKind,
    ReplayGuard,
    TargetAction,
    open_envelope,
)
from secdogie_identity import Allowlist

VECTORS = Path(__file__).resolve().parents[2] / "fixtures" / "vectors"


def _generator():
    spec = importlib.util.spec_from_file_location("secdogie_vectors_generate", VECTORS / "generate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_committed_vectors_match_the_python_reference():
    for name, text in _generator().build_all().items():
        committed = (VECTORS / name).read_text(encoding="utf-8")
        assert committed == text, f"{name} is stale: run python fixtures/vectors/generate.py"


def _load(name: str) -> dict:
    return json.loads((VECTORS / name).read_text(encoding="utf-8"))


def test_the_stored_token_verifies_with_the_python_verifier():
    g = _load("gate2.json")
    action = TargetAction(**g["actions"][g["token"]["action_index"]]["action"])
    token = json.loads(g["token"]["wire"])
    res = verify_authorization(token, action, operators=Allowlist({g["operator"]["did"]}),
                               subject=g["node"]["did"], now=float(g["token"]["valid_from"]) + 1)
    assert res.ok, res.reason


def test_the_sealed_challenge_opens_with_the_python_receiver():
    g = _load("gate2.json")
    env = json.loads(g["challenge"]["wire"])
    clock_ns = env["header"]["timestamp_ns"]
    opened = open_envelope(env, trust=Allowlist({g["node"]["did"]}), self_did=g["operator"]["did"],
                           replay=ReplayGuard(clock_ns=lambda: clock_ns))
    assert opened.ok, opened.reason
    assert opened.envelope.kind is PacketKind.GATE2_CHALLENGE
    assert isinstance(opened.envelope.packet, Gate2ChallengePacket)
    assert opened.envelope.header.timestamp_ns > 2**53  # the case a JS number cannot hold


def test_the_canonical_vectors_round_trip_through_python():
    gen = _generator()
    for case in _load("canonical.json")["cases"]:
        assert gen.canonical(json.loads(case["input"])).decode("utf-8") == case["canonical"], case["name"]
