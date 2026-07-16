"""Get on foot into a car: walk up to the nearest vehicle and jack it.

The driving control law (secdogie-gta) assumes you are already *in* a vehicle.
This is the missing prerequisite -- the on-foot half that produces one. It is
the same two-tier idea as the rest of the stack: a detector says *which* pixels
are a car, and this closes a tight local loop that faces the car, walks to it,
and presses the enter-vehicle key when close.

Like the aim and driving laws, its convergence is loop math, not a specific
game, so it is proven headless by driving a *simulated* approach (tests/): the
"car" grows as you walk toward it and shifts as you turn the camera, and the
loop must centre it, close the distance, and fire the enter key. What cannot be
proven headless -- that the enter key really jacks the car, that "car" is what
YOLO detects -- is the on-machine half (see README).

Coordinates: `turn` is horizontal camera counts, +1 = turn the view right;
`invert_x` flips it for a game whose look direction is opposite (the same
inversion the aim controller handles). The vehicle is "near" when its detection
box fills `enter_box_frac` of the frame height -- a car looming large in view is
one you're standing next to.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from secdogie_aim.controller import Detection, Detector  # reuse the detected-box type + Detector protocol


@dataclass(frozen=True)
class ApproachConfig:
    gain: float = 0.4  # camera counts per pixel of horizontal error (turn to face the car)
    max_step: int = 40  # per-tick camera clamp, same idea as the aim controller's
    invert_x: bool = False  # negate the turn if the camera pans the wrong way (see aim's invert)
    center_deadzone_px: float = 40.0  # |x error| under this while walking = "facing it", go straight
    enter_box_frac: float = 0.5  # car box height >= this fraction of frame height = close enough to enter
    enter_center_px: float = 100.0  # once close, only press enter when the car is within this of centre
    min_confidence: float = 0.4  # detections below this are ignored
    label: str | None = "car"  # only approach this class (stock COCO YOLO already knows "car"); None = any
    lost_frames: int = 30  # consecutive carless frames before giving up
    timeout_s: float = 30.0  # hard cap on one approach
    max_fps: float = 20.0  # loop pacing; 0 = uncapped (tests)


@dataclass(frozen=True)
class ApproachCommand:
    turn: int  # horizontal camera counts this tick (0 = hold heading)
    walk: bool  # hold the forward key this tick?
    enter: bool  # press the enter-vehicle key now?


@dataclass(frozen=True)
class ApproachResult:
    outcome: str  # "entered" | "lost" | "timeout" | "stopped"
    frames: int
    elapsed_s: float


def nearest_car(dets: list[Detection], cfg: ApproachConfig) -> Detection | None:
    """The closest matching vehicle = the largest detection box (nearer things
    project bigger), among those passing the label/confidence filter."""
    picks = [
        d for d in dets
        if d.confidence >= cfg.min_confidence and (cfg.label is None or d.label == cfg.label)
    ]
    return max(picks, key=lambda d: d.w * d.h) if picks else None


def _clamp(v: float, lo: int, hi: int) -> int:
    return int(max(lo, min(hi, round(v))))


def approach_step(car: Detection, frame_size: tuple[int, int], cfg: ApproachConfig) -> ApproachCommand:
    """One tick: turn to face the car, walk toward it, and enter once you're
    close AND facing it. Far away -> walk while steering; right next to it ->
    stop walking, finish turning to face it, then press enter."""
    fw, fh = frame_size
    err_x = car.cx - fw / 2.0
    sx = -1.0 if cfg.invert_x else 1.0

    def turn_for(deadzone: float) -> int:
        if abs(err_x) <= deadzone:
            return 0
        return _clamp(cfg.gain * err_x * sx, -cfg.max_step, cfg.max_step)

    close = car.h >= cfg.enter_box_frac * fh
    if close:
        # Standing next to a car: don't keep walking into it -- face it, then jack.
        if abs(err_x) <= cfg.enter_center_px:
            return ApproachCommand(turn=0, walk=False, enter=True)
        return ApproachCommand(turn=turn_for(0.0), walk=False, enter=False)

    # Still approaching: walk forward, steering the camera to keep the car ahead.
    return ApproachCommand(turn=turn_for(cfg.center_deadzone_px), walk=True, enter=False)


def approach_and_enter(
    capture: Callable[[], bytes],
    detector: Detector,
    turn: Callable[[int], None],
    walk: Callable[[bool], None],
    enter: Callable[[], None],
    frame_size: tuple[int, int],
    cfg: ApproachConfig = ApproachConfig(),
    *,
    should_stop: Callable[[], bool] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> ApproachResult:
    """Loop: capture -> detect nearest car -> face+walk -> enter when close.

    `turn(dx)` turns the camera, `walk(on)` holds/releases the forward key,
    `enter()` presses the enter-vehicle key. Injecting them (plus clock/sleep)
    is what makes the loop testable against a simulated approach. On any exit
    the forward key is released so the character doesn't keep running."""
    min_dt = 1.0 / cfg.max_fps if cfg.max_fps and cfg.max_fps > 0 else 0.0
    start = clock()
    frames = 0
    lost = 0
    walking = False

    def stop_walking() -> None:
        nonlocal walking
        if walking:
            walk(False)
            walking = False

    def result(outcome: str) -> ApproachResult:
        stop_walking()  # never leave the forward key held down
        return ApproachResult(outcome, frames, clock() - start)

    while True:
        if should_stop is not None and should_stop():
            return result("stopped")
        now = clock()
        if now - start >= cfg.timeout_s:
            return result("timeout")

        car = nearest_car(detector.detect(capture()), cfg)
        frames += 1

        if car is None:
            lost += 1
            stop_walking()  # lost sight of the car; don't keep running blind
            if lost >= cfg.lost_frames:
                return result("lost")
        else:
            lost = 0
            cmd = approach_step(car, frame_size, cfg)
            if cmd.turn:
                turn(cmd.turn)
            if cmd.walk != walking:
                walk(cmd.walk)
                walking = cmd.walk
            if cmd.enter:
                enter()
                return result("entered")

        if min_dt:
            spent = clock() - now
            if spent < min_dt:
                sleep(min_dt - spent)
