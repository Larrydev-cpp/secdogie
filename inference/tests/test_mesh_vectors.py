"""fixtures/vectors/mesh.json is what generate_mesh.py makes from the current
code: the browser node (symbiont/src/mesh/) is tested against that file, so a
protocol change that does not regenerate it fails here, not in a browser."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("nacl")

GENERATOR = Path(__file__).resolve().parents[2] / "fixtures" / "vectors" / "generate_mesh.py"


def test_mesh_vectors_are_current():
    spec = importlib.util.spec_from_file_location("generate_mesh", GENERATOR)
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    assert (GENERATOR.parent / "mesh.json").read_text(encoding="utf-8") == gen.build(), \
        "stale fixtures/vectors/mesh.json: run python fixtures/vectors/generate_mesh.py"
