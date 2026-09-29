"""Zero trust, enforced on the source: the verify primitives keep an optional
trust argument (they are building blocks), but every production call site in
the repository passes one explicitly -- never omitted, never a literal None.
A new call that forgets it fails here, not in the field."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
# name -> (positional index of the trust argument, its keyword)
PRIMITIVES = {"verify_payload": (1, "allowlist"), "verify_binding": (None, "allowlist"),
              "verify_record": (None, "allowlist")}


def _sources():
    for pkg in sorted(ROOT.glob("*/secdogie_*")):
        for path in sorted(pkg.rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if "/tests/" in rel or "/perception/" in rel:
                continue
            yield rel, path


def _offending_calls(tree: ast.AST):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name not in PRIMITIVES:
            continue
        index, keyword = PRIMITIVES[name]
        arg = next((k.value for k in node.keywords if k.arg == keyword), None)
        if arg is None and index is not None and len(node.args) > index:
            arg = node.args[index]
        if arg is None or (isinstance(arg, ast.Constant) and arg.value is None):
            yield f"{name} at line {node.lineno}"


def test_every_production_call_names_whom_it_trusts():
    sources = list(_sources())
    if len(sources) < 20:
        pytest.skip("the rest of the repository is not checked out next to this package")
    bad = {rel: list(_offending_calls(ast.parse(path.read_text(encoding="utf-8")))) for rel, path in sources}
    assert {k: v for k, v in bad.items() if v} == {}


def test_the_scan_would_catch_a_missing_or_none_trust_argument():
    tree = ast.parse("verify_payload(obj)\nverify_payload(obj, None)\nx.verify_binding(b, now=1)\n"
                     "verify_record(o, allowlist=None)\nverify_payload(obj, allow)\nverify_record(o, allowlist=a)\n")
    assert [c.split()[0] for c in _offending_calls(tree)] == ["verify_payload", "verify_payload",
                                                               "verify_binding", "verify_record"]


def test_require_trust_refuses_only_none():
    from secdogie_identity import ALLOW_ANY, Allowlist, require_trust

    with pytest.raises(ValueError, match="Thing needs an allowlist"):
        require_trust(None, "Thing")
    empty = Allowlist()
    assert require_trust(empty, "Thing") is empty  # an empty list is a (strict) decision, not "unset"
    assert require_trust(ALLOW_ANY, "Thing") is ALLOW_ANY
    assert ALLOW_ANY.contains("did:key:zAnyone") and bool(ALLOW_ANY)
