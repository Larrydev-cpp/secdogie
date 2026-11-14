"""fixtures/vectors/policy.json is what the TypeScript validator must reproduce.
It is generated from the production Python functions; this fails if the
committed file drifted (run python fixtures/vectors/generate.py)."""
from __future__ import annotations

import json
from pathlib import Path

from secdogie_identity import Allowlist, MasterSet
from secdogie_policy import check_successor, normalize_phrase, verify_bundle
from secdogie_policy.vectors import build

VECTORS = Path(__file__).resolve().parents[2] / "fixtures" / "vectors" / "policy.json"


def test_committed_policy_vectors_match_the_python_reference():
    text = json.dumps(build(), indent=2, ensure_ascii=False) + "\n"
    assert VECTORS.read_text(encoding="utf-8") == text, "stale: run python fixtures/vectors/generate.py"


def test_the_committed_cases_verify_as_recorded():
    v = json.loads(VECTORS.read_text(encoding="utf-8"))
    operators = Allowlist({v["operator"]["did"]})
    masters = MasterSet([m["did"] for m in v["masters"]], v["threshold"])
    for c in v["cases"]:
        got = verify_bundle(json.loads(c["bundle"]), operators=operators, masters=masters).to_json()
        assert got == c["expect"], c["name"]
    for n in v["normalize"]:
        assert normalize_phrase(n["input"]) == n["normalized"]
    for c in v["chain"]:
        current = None if c["current"] is None else json.loads(c["current"])
        assert check_successor(current, json.loads(c["candidate"])) == c["errors"], c["name"]


def test_the_vectors_cover_every_kind_and_both_outcomes():
    v = json.loads(VECTORS.read_text(encoding="utf-8"))
    kinds_ok = {json.loads(c["bundle"])["kind"] for c in v["cases"] if c["expect"]["ok"]}
    assert kinds_ok == {"allowlist", "decision-model", "gate-rules", "ingest"}
    assert sum(not c["expect"]["ok"] for c in v["cases"]) > 50
