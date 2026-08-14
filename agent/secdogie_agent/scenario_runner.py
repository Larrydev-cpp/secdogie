"""Minimal CAD scenario runner for commercial readiness scoring.

Loads fixtures/cad/<id>/expected.json, scores a --trace JSONL against the
expected constraints. No real mouse is required for offline scoring.

Usage:
  python -m secdogie_agent.scenario_runner fixtures/cad/01_zoom_extents --trace out.jsonl
  python -m secdogie_agent.scenario_runner fixtures/cad --all
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_MUTATING_KINDS = frozenset({
    "type", "key", "hold_key", "drag", "open", "run_elevated",
})


def load_expected(scenario_dir: Path) -> dict:
    path = scenario_dir / "expected.json"
    if not path.is_file():
        raise FileNotFoundError(f"missing {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def score_actions(actions: list[dict], expected: dict) -> list[str]:
    fails: list[str] = []
    kinds = [a.get("kind") for a in actions if a.get("kind")]
    if not kinds:
        fails.append("no actions recorded")
        return fails
    allowed = set(expected.get("allowed_kinds") or [])
    forbidden = set(expected.get("forbidden_kinds") or [])
    for k in kinds:
        if forbidden and k in forbidden:
            fails.append(f"forbidden kind used: {k}")
        if allowed and k not in allowed:
            fails.append(f"kind not in allowed list: {k}")
    must_end = expected.get("must_end_with")
    if must_end and kinds[-1] != must_end:
        fails.append(f"must end with {must_end!r}, got {kinds[-1]!r}")
    max_mut = expected.get("max_mutating")
    if max_mut is not None:
        mut_count = sum(1 for k in kinds if k in _MUTATING_KINDS)
        if mut_count > max_mut:
            fails.append(f"too many mutating actions: {mut_count} > {max_mut}")
    return fails


def score_trace_file(trace_path: Path, expected: dict) -> list[str]:
    actions: list[dict] = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        act = rec.get("action") or rec.get("decision") or {}
        if isinstance(act, dict) and act.get("kind"):
            actions.append(act)
    return score_actions(actions, expected)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score CAD golden scenarios")
    parser.add_argument("path", help="scenario dir or parent of scenario dirs")
    parser.add_argument("--all", action="store_true", help="run every child scenario under path")
    parser.add_argument("--trace", default=None, help="existing --trace JSONL to score")
    args = parser.parse_args(argv)
    root = Path(args.path)
    if not root.exists():
        print(f"error: {root} does not exist", file=sys.stderr)
        return 1
    if args.all or (root.is_dir() and not (root / "expected.json").is_file()):
        scenarios = sorted(p for p in root.iterdir() if p.is_dir() and (p / "expected.json").is_file())
    else:
        scenarios = [root]
    if not scenarios:
        print("error: no scenarios found", file=sys.stderr)
        return 1
    overall = 0
    for sc in scenarios:
        try:
            expected = load_expected(sc)
        except Exception as e:
            print(f"FAIL {sc.name}: {e}")
            overall = 1
            continue
        if args.trace:
            fails = score_trace_file(Path(args.trace), expected)
        else:
            print(f"INFO {sc.name}: fixture loaded (task={expected.get('task', '')[:60]!r})")
            print(f"     to score a real run: python -m secdogie_agent.scenario_runner {sc} --trace out.jsonl")
            fails = []
        if fails:
            print(f"FAIL {sc.name}:")
            for f in fails:
                print(f"  - {f}")
            overall = 1
        else:
            print(f"PASS {sc.name}")
    return overall


if __name__ == "__main__":
    sys.exit(main())
