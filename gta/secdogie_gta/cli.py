"""secdogie-gta: drive to a point via the ScriptHookV bridge (single-player).

Connects to the plugin, then either drives to an explicit --to X,Y or to the
map waypoint the player set in-game, using driving.drive_to. On-machine only --
it needs the game + the plugin running; the control law it drives with is what's
tested offline (driving.py).
"""
from __future__ import annotations

import argparse
import sys

from .driving import DriveConfig, drive_to


def _parse_point(text: str) -> tuple[float, float]:
    try:
        x, y = (float(p) for p in text.split(","))
    except ValueError as e:
        raise SystemExit(f"--to must be 'X,Y', got {text!r}") from e
    return (x, y)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="secdogie-gta",
        description="Drive a GTA V vehicle to a point via a ScriptHookV plugin (single-player only).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("drive", help="steer to a waypoint until arrived/timeout")
    p.add_argument("--to", default=None, help="target as 'X,Y' world coords; omit to use the in-game map waypoint")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=47800)
    p.add_argument("--gain", type=float, default=0.03, help="steer per degree of heading error")
    p.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args(argv)

    from .bridge import GtaBridge

    cfg = DriveConfig(gain=args.gain, timeout_s=args.timeout)
    try:
        with GtaBridge(args.host, args.port) as bridge:
            target = _parse_point(args.to) if args.to else bridge.read_state().waypoint
            if target is None:
                print("[secdogie-gta] no --to given and no map waypoint set in-game", file=sys.stderr)
                return 2
            result = drive_to(bridge.read_state, bridge.send, target, cfg)
            print(f"[secdogie-gta] drive: {result.outcome} after {result.ticks} tick(s)")
            return 0 if result.outcome == "arrived" else 1
    except (OSError, ConnectionError) as e:
        print(f"[secdogie-gta] could not reach the plugin at {args.host}:{args.port}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
