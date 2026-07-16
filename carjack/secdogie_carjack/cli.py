"""secdogie-carjack: walk to the nearest car and get in (single-player only).

On-machine only -- it needs the game running, a YOLO model that detects "car"
(stock COCO yolov8n already does), the relative-mouse-look path (from
secdogie-aim), and a keyboard actuator. The control law it drives with is what's
tested offline (approach.py).
"""
from __future__ import annotations

import argparse
import sys
import time

from .approach import ApproachConfig, approach_and_enter


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="secdogie-carjack",
        description="Walk up to the nearest car and get in, via YOLO + relative mouse-look (single-player only).",
    )
    parser.add_argument("--weights", required=True, help="path to YOLO .pt weights (stock yolov8n.pt detects 'car')")
    parser.add_argument("--label", default="car", help="detection class to approach (default: car; empty = any)")
    parser.add_argument("--forward-key", default="w", help="key that walks forward (default: w)")
    parser.add_argument("--enter-key", default="f", help="key that enters/jacks a vehicle (GTA IV default: f)")
    parser.add_argument("--gain", type=float, default=0.4, help="camera counts per pixel of horizontal error")
    parser.add_argument("--invert-x", action="store_true", help="negate the turn if the camera pans the wrong way")
    parser.add_argument("--timeout", type=float, default=30.0, help="seconds before the approach gives up (default 30)")
    args = parser.parse_args(argv)

    try:
        return _run(args)
    except RuntimeError as e:
        # Missing optional deps / unsupported platform surface as clear
        # RuntimeErrors from the layers below; show the message, not a traceback.
        print(f"[secdogie-carjack] {e}", file=sys.stderr)
        return 2


def _run(args: argparse.Namespace) -> int:
    import mss
    import pyautogui

    from secdogie_aim.mouse import open_mouse
    from secdogie_aim.yolo import YoloDetector

    detector = YoloDetector(args.weights)
    mouse = open_mouse()
    cfg = ApproachConfig(
        gain=args.gain,
        invert_x=args.invert_x,
        label=(args.label or None),
        timeout_s=args.timeout,
    )

    def turn(dx: int) -> None:
        mouse.move(dx, 0)  # horizontal camera only

    def walk(on: bool) -> None:
        (pyautogui.keyDown if on else pyautogui.keyUp)(args.forward_key)

    def enter() -> None:
        pyautogui.press(args.enter_key)

    with mss.mss() as sct:
        monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        frame_size = (monitor["width"], monitor["height"])

        def capture() -> bytes:
            shot = sct.grab(monitor)
            return mss.tools.to_png(shot.rgb, shot.size)

        print(f"[secdogie-carjack] looking for a '{args.label or 'any'}' to jack; alt-tab into the game ...")
        time.sleep(2.0)
        result = approach_and_enter(capture, detector, turn, walk, enter, frame_size, cfg)

    print(f"[secdogie-carjack] {result.outcome} after {result.frames} frame(s), {result.elapsed_s:.1f}s")
    if result.outcome == "entered":
        print("[secdogie-carjack] in the car -- hand off to driving (keyboard WASD, or secdogie-gta on GTA V).")
    return 0 if result.outcome == "entered" else 1


if __name__ == "__main__":
    raise SystemExit(main())
