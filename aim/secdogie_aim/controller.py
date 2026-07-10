"""The aim control law: pull the crosshair onto a detected target and fire.

In a pointer-captured game the crosshair is ALWAYS the screen center -- the
camera turns, the world moves. So the aim error is simply

    error = detection center - frame center

and each relative mouse step turns the camera so the target's projection drifts
toward the center. That makes aiming a classic proportional-control loop, which
is why `engage` can be proven headless: drive it against a simulated plant
("moving the mouse by dx shifts the target by -k*dx") and assert convergence.
What CANNOT be proven headless is the plant's real gain -- how many mouse
counts turn the camera one degree depends on the game's sensitivity setting --
which is what the CLI's `calibrate` command measures on the real machine.

The controller deliberately has no I or D term: the plant (camera yaw/pitch per
count) is linear and memoryless, so P with a per-frame clamp converges without
steady-state error, and fewer knobs means less to misconfigure. `max_step`
bounds the worst case when the configured gain is too hot for the game's
sensitivity: the loop then walks toward the target at a capped speed instead of
slingshotting past it.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .mouse import RelativeMouse


@dataclass(frozen=True)
class Detection:
    """One detected target in a frame, in that frame's own pixels."""

    cx: float  # box center
    cy: float
    w: float  # box size (used by callers for range heuristics; not by the law)
    h: float
    confidence: float
    label: str = ""


@runtime_checkable
class Detector(Protocol):
    """Per-frame target detection. The real implementation is the YOLO adapter
    (yolo.py, GPU machine only); tests drive `engage` with plain functions."""

    def detect(self, frame_png: bytes) -> list[Detection]: ...


@dataclass(frozen=True)
class AimConfig:
    gain: float = 0.5  # mouse counts per pixel of error (calibrate on the machine)
    max_step: int = 60  # per-frame |dx|,|dy| clamp: a hot gain walks, never slingshots
    deadzone_px: float = 3.0  # error inside this is noise; don't jitter the camera
    fire_radius_px: float = 12.0  # crosshair within this of the target center -> shoot
    fire_cooldown_s: float = 0.25  # min seconds between shots (melee swing rate)
    min_confidence: float = 0.5  # detections below this are ignored
    lost_frames: int = 15  # consecutive empty frames before giving up
    timeout_s: float = 20.0  # hard cap on one engagement
    max_fps: float = 60.0  # frame pacing; 0 = uncapped (tests)


def aim_step(err_x: float, err_y: float, cfg: AimConfig) -> tuple[int, int]:
    """One proportional step toward zero error: counts = clamp(gain * error).

    Inside `deadzone_px` (radial) the step is (0, 0) -- detection boxes wobble a
    pixel or two frame-to-frame even on a stationary target, and chasing that
    noise shakes the camera."""
    if err_x * err_x + err_y * err_y <= cfg.deadzone_px * cfg.deadzone_px:
        return (0, 0)

    def clamp(v: float) -> int:
        return int(max(-cfg.max_step, min(cfg.max_step, round(v))))

    return (clamp(err_x * cfg.gain), clamp(err_y * cfg.gain))


@dataclass(frozen=True)
class EngageResult:
    outcome: str  # "lost" | "timeout" | "stopped" -- combat has no "done" signal;
    # the caller (tactician/CLI) decides when the fight is over, so the loop only
    # ever ends by losing the target, running out its budget, or being told to stop.
    frames: int
    elapsed_s: float
    fps: float
    shots: int


def _best_target(dets: list[Detection], cfg: AimConfig, label: str | None) -> Detection | None:
    picks = [d for d in dets if d.confidence >= cfg.min_confidence and (label is None or d.label == label)]
    return max(picks, key=lambda d: d.confidence) if picks else None


# AimConfig is frozen, so one shared default instance is safe as a default arg.
_DEFAULT_CONFIG = AimConfig()


def engage(
    capture: Callable[[], bytes],
    detector: Detector,
    mouse: RelativeMouse,
    frame_size: tuple[int, int],
    cfg: AimConfig = _DEFAULT_CONFIG,
    *,
    label: str | None = None,
    should_stop: Callable[[], bool] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> EngageResult:
    """Track-and-fire loop: capture -> detect -> P-step the camera -> fire when
    the crosshair (frame center) is within `fire_radius_px` of the target.

    Same shape as reflex.pursue (injectable clock/sleep, frame pacing, loss/
    timeout budgets) because it is the same kind of loop -- only the perception
    (Detector vs template match) and the actuator (relative camera turn vs
    absolute cursor move) differ. `label` filters detections (e.g. only
    "ender_dragon"); None takes the best of any class."""
    center_x, center_y = frame_size[0] / 2.0, frame_size[1] / 2.0
    min_dt = 1.0 / cfg.max_fps if cfg.max_fps and cfg.max_fps > 0 else 0.0
    start = clock()
    frames = 0
    lost = 0
    shots = 0
    last_shot: float | None = None

    def result(outcome: str) -> EngageResult:
        el = clock() - start
        return EngageResult(outcome, frames, el, frames / el if el > 0 else 0.0, shots)

    while True:
        if should_stop is not None and should_stop():
            return result("stopped")
        now = clock()
        if now - start >= cfg.timeout_s:
            return result("timeout")

        target = _best_target(detector.detect(capture()), cfg, label)
        frames += 1

        if target is None:
            lost += 1
            if lost >= cfg.lost_frames:
                return result("lost")
        else:
            lost = 0
            err_x, err_y = target.cx - center_x, target.cy - center_y
            dx, dy = aim_step(err_x, err_y, cfg)
            if dx or dy:
                mouse.move(dx, dy)
            on_target = err_x * err_x + err_y * err_y <= cfg.fire_radius_px * cfg.fire_radius_px
            cooled = last_shot is None or (now - last_shot) >= cfg.fire_cooldown_s
            if on_target and cooled:
                mouse.click()
                shots += 1
                last_shot = now

        if min_dt:
            spent = clock() - now
            if spent < min_dt:
                sleep(min_dt - spent)
