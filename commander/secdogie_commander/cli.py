"""secdogie-commander: the tactician front door.

  plan   dry-run the decision state machine over a scripted list of GameStates
         and print what it would do -- fully offline, the fastest way to see the
         priority table in action and sanity-check thresholds.
  run    drive the real fight on the machine: wires Node B (aim.engage) and
         Node A (agent.run) under the input baton, reading GameState from a
         StateReader you supply (see README -- that perception is the on-machine
         part). Imports agent/aim/handoff lazily so `plan` and the tests need
         none of them.
"""
from __future__ import annotations

import argparse
import json
import sys

from .tactician import CommandConfig, GameState, command, decide


def _cfg_from_args(args: argparse.Namespace) -> CommandConfig:
    return CommandConfig(
        heal_health=args.heal_health,
        retreat_health=args.retreat_health,
        min_arrows=args.min_arrows,
    )


def _add_common_thresholds(p: argparse.ArgumentParser) -> None:
    p.add_argument("--heal-health", type=float, default=0.4, help="break off to heal at/below this fraction (default 0.4)")
    p.add_argument("--retreat-health", type=float, default=0.25, help="disengage if threatened at/below this (default 0.25)")
    p.add_argument("--min-arrows", type=int, default=4, help="restock when arrows drop below this (default 4)")


def _run_plan(args: argparse.Namespace) -> int:
    try:
        with open(args.script) as f:
            rows = json.load(f)
    except (OSError, ValueError) as e:
        print(f"[secdogie-commander] could not read {args.script}: {e}", file=sys.stderr)
        return 2
    if not isinstance(rows, list):
        print("[secdogie-commander] script must be a JSON list of game-state objects", file=sys.stderr)
        return 2

    cfg = _cfg_from_args(args)
    for i, row in enumerate(rows):
        state = GameState(**row)
        d = decide(state, cfg)
        target = f" -> {d.target_label}" if d.target_label else ""
        print(f"[{i}] {d.kind}{target}: {d.reason}")
    return 0


def _run_fight(args: argparse.Namespace) -> int:
    # Imported here so `plan` and the test suite don't require agent/aim/handoff.
    from .wiring import build_runners, load_state_reader

    cfg = _cfg_from_args(args)
    reader = load_state_reader(args.state_reader)
    run_logistics, run_combat = build_runners(args)
    result = command(reader, run_logistics, run_combat, cfg, on_decision=lambda d: print(f"[commander] {d.kind}: {d.reason}"))
    print(f"[secdogie-commander] {result.outcome} after {result.rounds} round(s)")
    return 0 if result.outcome == "done" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="secdogie-commander",
        description="Tactician: decide the fight's phases and sequence Node A (logistics) / Node B (combat).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser("plan", help="dry-run the decision state machine over a scripted state list (offline)")
    p_plan.add_argument("--script", required=True, help="JSON list of game-state objects to decide over")
    _add_common_thresholds(p_plan)

    p_run = sub.add_parser("run", help="drive the real fight on the machine (needs agent/aim/handoff installed)")
    p_run.add_argument("--state-reader", required=True, help="import path 'module:factory' returning your StateReader")
    p_run.add_argument("--weights", required=True, help="YOLO .pt weights for Node B combat")
    p_run.add_argument("--macro", default=None, help="logistics macro file for Node A resupply/retreat")
    p_run.add_argument("--lock-dir", default=None, help="input-baton dir shared with Node A (default ~/.secdogie/handoff)")
    _add_common_thresholds(p_run)

    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            return _run_plan(args)
        return _run_fight(args)
    except RuntimeError as e:
        print(f"[secdogie-commander] {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
