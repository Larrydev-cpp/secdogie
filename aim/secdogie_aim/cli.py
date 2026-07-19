"""secdogie-aim: the Node B combat CLI -- calibrate the mouse-look gain, then
run engagements under the cross-process input baton.

Two subcommands, matching the two things that can only happen on the real
machine:

  calibrate  prove the relative-injection path works at all (the camera turns)
             and measure the game's counts-per-degree so `--gain` can be set.
  engage     take the input baton from Node A, run the YOLO-driven track-and-
             fire loop, and hand the baton back when it ends.
"""
from __future__ import annotations

import argparse
import sys
import time


def _add_calibrate(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "calibrate",
        help="send fixed relative-mouse pulses so you can measure how far the camera turns",
    )
    p.add_argument("--counts", type=int, default=100, help="mouse counts per pulse (default 100)")
    p.add_argument("--pulses", type=int, default=10, help="number of pulses (default 10)")
    p.add_argument("--interval", type=float, default=0.5, help="seconds between pulses (default 0.5)")
    p.add_argument("--delay", type=float, default=5.0, help="seconds to alt-tab into the game first (default 5)")


def _add_engage(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("engage", help="track-and-fire on the best detected target until lost/timeout")
    p.add_argument("--weights", required=True, help="path to YOLO .pt weights (e.g. dragon.pt)")
    p.add_argument("--label", default=None, help="only engage detections with this class label (e.g. ender_dragon)")
    p.add_argument("--gain", type=float, default=0.5, help="mouse counts per pixel of error (from calibrate)")
    p.add_argument("--invert-x", action="store_true", help="negate horizontal steer if the camera turns the wrong way left/right")
    p.add_argument("--invert-y", action="store_true", help="negate vertical steer if the game has 'invert look' on (camera spins up/down)")
    p.add_argument("--timeout", type=float, default=20.0, help="seconds before one engagement gives up (default 20)")
    p.add_argument("--lock-dir", default=None, help="input-baton directory shared with Node A (default ~/.secdogie/handoff)")
    p.add_argument("--no-baton", action="store_true", help="skip the input baton (single-node runs, no Node A)")


def _run_calibrate(args: argparse.Namespace) -> int:
    from .mouse import open_mouse

    mouse = open_mouse()
    total = args.counts * args.pulses
    print(f"[secdogie-aim] switch to the game window; pulsing in {args.delay:.0f}s ...")
    time.sleep(args.delay)
    for i in range(args.pulses):
        mouse.move(args.counts, 0)
        print(f"  pulse {i + 1}/{args.pulses}: +{args.counts} counts right")
        time.sleep(args.interval)
    print(
        f"[secdogie-aim] sent {total} counts total. If the camera turned N degrees,\n"
        f"  counts-per-degree = {total} / N. For --gain, start with ~0.3-0.7 counts/pixel\n"
        "  and lower it if the crosshair overshoots. If the camera did NOT move, the\n"
        "  relative-injection path is broken on this machine -- fix that before engage."
    )
    return 0


def _run_engage(args: argparse.Namespace) -> int:
    import mss

    from .controller import AimConfig, engage
    from .mouse import open_mouse
    from .yolo import YoloDetector

    detector = YoloDetector(args.weights)
    mouse = open_mouse()
    cfg = AimConfig(gain=args.gain, timeout_s=args.timeout, invert_x=args.invert_x, invert_y=args.invert_y)

    with mss.mss() as sct:
        monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        frame_size = (monitor["width"], monitor["height"])

        def capture() -> bytes:
            shot = sct.grab(monitor)
            return mss.tools.to_png(shot.rgb, shot.size)

        def run() -> int:
            result = engage(capture, detector, mouse, frame_size, cfg, label=args.label)
            print(
                f"[secdogie-aim] engagement over: {result.outcome} after {result.frames} frame(s), "
                f"{result.shots} shot(s) [{result.fps:.0f} fps]"
            )
            if result.outcome == "diverging":
                print(
                    "[secdogie-aim] the camera was turning the WRONG way (error grew every "
                    "frame) -- an inverted axis. Re-run with --invert-y (and/or --invert-x), "
                    "or toggle the game's 'invert look' setting.",
                    file=sys.stderr,
                )
            return 0 if result.shots > 0 else 1

        if args.no_baton:
            return run()

        from secdogie_handoff import InputArbiter

        arbiter = InputArbiter(args.lock_dir)
        # Raise a hand first so Node A releases at its next safe point, then
        # block on acquire -- succeeding IS the proof Node A let go.
        arbiter.request_yield("node-b")
        print("[secdogie-aim] waiting for the input baton (Node A finishing its step) ...")
        with arbiter.hold("node-b"):
            return run()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="secdogie-aim",
        description="Node B: real-time combat controller (relative mouse-look + YOLO aim).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    _add_calibrate(sub)
    _add_engage(sub)
    args = parser.parse_args(argv)

    try:
        if args.command == "calibrate":
            return _run_calibrate(args)
        return _run_engage(args)
    except RuntimeError as e:
        # Missing optional deps / unsupported platform arrive as clear
        # RuntimeErrors from the layers below; show the message, not a traceback.
        print(f"[secdogie-aim] {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
