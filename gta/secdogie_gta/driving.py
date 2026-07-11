"""The driving control law: steer a vehicle toward a target point.

This is the fast, local half -- the same two-tier idea as everywhere else. A
cloud model decides *where* to go (a waypoint); this closes a tight loop that
keeps the car pointed at it: read heading, compute the bearing to the target,
steer to null the angular error, ease off the throttle in hard turns. It is a
proportional heading controller, and its convergence is a property of the loop
math, not of GTA -- so `drive_to` is proven here by driving a simulated vehicle
to the point (tests/), exactly as the aim controller is.

Angle frame: `heading` and `bearing` are both degrees in the same frame, and
`steer` is +1 = turn toward *increasing* heading. The ScriptHookV plugin maps
GTA's heading convention into this frame and maps `steer` back to the game's
left/right -- so this module never has to know GTA's exact convention, which
keeps it pure and testable. Alternatively the plugin can ignore steer entirely
and hand the waypoint to GTA's own driving AI via a `task` command (protocol.py).
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from .protocol import Command, GameState, drive_control


def normalize_deg(angle: float) -> float:
    """Wrap to (-180, 180] so the controller always turns the short way around."""
    a = math.fmod(angle, 360.0)
    if a > 180.0:
        a -= 360.0
    elif a <= -180.0:
        a += 360.0
    return a


def bearing(fx: float, fy: float, tx: float, ty: float) -> float:
    """Compass-free bearing from (fx,fy) to (tx,ty), degrees, same frame as heading."""
    return math.degrees(math.atan2(ty - fy, tx - fx))


@dataclass(frozen=True)
class DriveConfig:
    gain: float = 0.03  # steer per degree of heading error (33deg error -> full lock)
    arrive_radius: float = 5.0  # within this many metres of the target = arrived
    min_throttle: float = 0.3  # always creep forward so the car can actually turn
    ease_angle: float = 90.0  # heading error at which throttle is fully eased to min
    timeout_s: float = 60.0
    max_fps: float = 20.0  # control rate; 0 = uncapped (tests)


@dataclass(frozen=True)
class DriveControl:
    steer: float  # -1..1
    throttle: float  # 0..1
    arrived: bool


# DriveConfig is frozen, so one shared default instance is safe as a default arg.
_DEFAULT_CONFIG = DriveConfig()


def steer_to(state: GameState, target: tuple[float, float], cfg: DriveConfig = _DEFAULT_CONFIG) -> DriveControl:
    """One control step: steer to null the heading error to `target`, and ease
    the throttle (down to `min_throttle`) the further off-heading we are, so a
    hard turn doesn't overshoot. Arrived (steer/throttle 0) inside arrive_radius."""
    dx, dy = target[0] - state.x, target[1] - state.y
    if math.hypot(dx, dy) <= cfg.arrive_radius:
        return DriveControl(0.0, 0.0, arrived=True)

    err = normalize_deg(bearing(state.x, state.y, target[0], target[1]) - state.heading)
    steer = max(-1.0, min(1.0, cfg.gain * err))
    # Full throttle when aligned, eased toward min_throttle as |err| -> ease_angle.
    align = max(0.0, 1.0 - abs(err) / cfg.ease_angle) if cfg.ease_angle > 0 else 1.0
    throttle = cfg.min_throttle + (1.0 - cfg.min_throttle) * align
    return DriveControl(steer, throttle, arrived=False)


@dataclass(frozen=True)
class DriveResult:
    outcome: str  # "arrived" | "timeout" | "stopped"
    ticks: int
    elapsed_s: float


def drive_to(
    get_state: Callable[[], GameState],
    send: Callable[[Command], None],
    target: tuple[float, float],
    cfg: DriveConfig = _DEFAULT_CONFIG,
    *,
    should_stop: Callable[[], bool] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> DriveResult:
    """Read state -> steer_to -> send a drive_control command, until arrived,
    timed out, or stopped. `send`/`get_state` are the bridge to the plugin;
    injecting them (plus clock/sleep) is what makes this loop testable against a
    simulated vehicle. On exit it sends a stop so the car doesn't keep rolling."""
    from .protocol import stop

    min_dt = 1.0 / cfg.max_fps if cfg.max_fps and cfg.max_fps > 0 else 0.0
    start = clock()
    ticks = 0

    def done(outcome: str) -> DriveResult:
        send(stop())
        return DriveResult(outcome, ticks, clock() - start)

    while True:
        if should_stop is not None and should_stop():
            return done("stopped")
        now = clock()
        if now - start >= cfg.timeout_s:
            return done("timeout")

        control = steer_to(get_state(), target, cfg)
        ticks += 1
        if control.arrived:
            return done("arrived")
        send(drive_control(control.steer, control.throttle))

        if min_dt:
            spent = clock() - now
            if spent < min_dt:
                sleep(min_dt - spent)
