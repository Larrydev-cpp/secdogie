"""The golden vectors are fresh, self-consistent, and verify.

Freshness: regenerating must reproduce the committed file (so a drift in the
canonical encoding or a schema change is caught). Self-consistency: every
stored canonical string re-encodes identically, every signature verifies, and
every digest recomputes -- the same checks a TS/Workers consumer will run."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from secdogie_identity import canonical, verify_payload
from secdogie_swarm import output_digest, wire

_GEN = Path(__file__).resolve().parents[1] / "fixtures" / "generate.py"
_spec = importlib.util.spec_from_file_location("swarm_vectorgen", _GEN)
assert _spec and _spec.loader
generate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(generate)

VECTORS = json.loads((_GEN.parent / "swarm_vectors.json").read_text("utf-8"))


def test_committed_file_is_not_stale() -> None:
    assert generate.main(["--check"]) == 0


def test_signed_task_and_result_verify() -> None:
    for section in ("task", "result"):
        signed = VECTORS[section]["signed"]
        ok, _signer = verify_payload(signed)
        assert ok, section
        payload = {k: v for k, v in signed.items() if k not in ("signer", "sig")}
        assert canonical(payload).decode("utf-8") == VECTORS[section]["payload_canonical"]


def test_output_digest_recomputes() -> None:
    assert VECTORS["result"]["output_digest"] == output_digest(VECTORS["result"]["signed"]["output"])
    for case in VECTORS["digests"]:
        assert output_digest(case["output"]) == case["digest"]


def test_every_wire_message_re_encodes_identically() -> None:
    for name, case in VECTORS["wire"].items():
        assert wire.encode(case["message"]).decode("utf-8") == case["canonical"], name
        assert wire.decode(case["canonical"]) == case["message"], name
