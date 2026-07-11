"""Driving control-law tests, all headless. The convergence test drives a
simulated vehicle to the waypoint, which is the honest proof: the steering law's
convergence is loop math, not GTA. What can't be proven here -- the ScriptHookV
plugin turning the real car -- is called out in the README."""
import math

from secdogie_gta.driving import (
    DriveConfig,
    bearing,
    drive_to,
    normalize_deg,
    smooth_steer,
    steer_to,
)
from secdogie_gta.protocol import GameState

CFG = DriveConfig(gain=0.03, arrive_radius=5.0, min_throttle=0.3, ease_angle=90.0, timeout_s=1e9, max_fps=0)


# -- angle helpers ------------------------------------------------------------

def test_normalize_deg_wraps_to_short_way():
    assert normalize_deg(0) == 0
    assert normalize_deg(190) == -170
    assert normalize_deg(-190) == 170
    assert normalize_deg(540) == 180


def test_bearing_points_at_the_target():
    assert bearing(0, 0, 10, 0) == 0        # +x
    assert bearing(0, 0, 0, 10) == 90       # +y
    assert abs(bearing(0, 0, 10, 10) - 45) < 1e-9


# -- steer_to: one control step ----------------------------------------------

def test_steer_zero_when_pointed_at_target():
    s = GameState(x=0, y=0, heading=0)  # facing +x, target on +x
    c = steer_to(s, (50, 0), CFG)
    assert abs(c.steer) < 1e-9 and c.throttle > 0.9 and not c.arrived


def test_steer_positive_to_turn_toward_a_left_target():
    # target bearing is +30 of heading -> positive heading error -> steer toward
    # increasing heading (positive).
    s = GameState(x=0, y=0, heading=0)
    c = steer_to(s, (math.cos(math.radians(30)) * 50, math.sin(math.radians(30)) * 50), CFG)
    assert c.steer > 0


def test_steer_negative_to_turn_toward_a_right_target():
    s = GameState(x=0, y=0, heading=0)
    c = steer_to(s, (math.cos(math.radians(-30)) * 50, math.sin(math.radians(-30)) * 50), CFG)
    assert c.steer < 0


def test_steer_clamps_to_full_lock():
    s = GameState(x=0, y=0, heading=0)  # target straight behind -> huge error
    c = steer_to(s, (-50, 0.01), CFG)
    assert abs(c.steer) == 1.0


def test_throttle_eases_off_in_a_hard_turn():
    s = GameState(x=0, y=0, heading=0)
    ahead = steer_to(s, (50, 0), CFG).throttle
    sideways = steer_to(s, (0, 50), CFG).throttle  # 90deg error
    assert ahead > sideways >= CFG.min_throttle


def test_arrived_inside_the_radius():
    s = GameState(x=0, y=0, heading=123)
    c = steer_to(s, (3, 0), CFG)  # 3m < arrive_radius 5
    assert c.arrived and c.steer == 0 and c.throttle == 0


# -- the simulated vehicle + convergence -------------------------------------

class SimVehicle:
    """A unicycle-ish car: a drive_control command turns the (true) heading and
    sets speed, then it advances along its heading one dt. `wobble` degrees of
    suspension bob are added to the heading *reported* to the controller (not the
    true motion), modeling a car rolling on its springs -- so the controller sees
    a jittery signal but the car really travels its true heading."""

    def __init__(self, x, y, heading, *, turn_rate=120.0, max_speed=18.0, dt=0.1,
                 wobble=0.0, wobble_hz=3.0):
        self.x, self.y, self.heading, self.speed = x, y, heading, 0.0
        self.turn_rate, self.max_speed, self.dt = turn_rate, max_speed, dt
        self.wobble, self.wobble_hz, self.t = wobble, wobble_hz, 0.0
        self.commands = []

    def get_state(self) -> GameState:
        reported = normalize_deg(self.heading + self.wobble * math.sin(2 * math.pi * self.wobble_hz * self.t))
        return GameState(x=self.x, y=self.y, heading=reported, speed=self.speed, in_vehicle=True)

    def send(self, cmd) -> None:
        self.commands.append(cmd)
        if cmd.kind == "stop":
            self.speed = 0.0
            return
        if cmd.kind == "drive_control":
            self.heading = normalize_deg(self.heading + cmd.steer * self.turn_rate * self.dt)
            self.speed = cmd.throttle * self.max_speed
        rad = math.radians(self.heading)
        self.x += self.speed * math.cos(rad) * self.dt
        self.y += self.speed * math.sin(rad) * self.dt
        self.t += self.dt

    def steer_jerk(self) -> float:
        """Mean absolute change in commanded steer per tick -- how much the wheel
        is sawing back and forth (the thing wobble-chasing inflates)."""
        steers = [c.steer for c in self.commands if c.kind == "drive_control"]
        if len(steers) < 2:
            return 0.0
        return sum(abs(b - a) for a, b in zip(steers, steers[1:], strict=False)) / (len(steers) - 1)


def _drive(sim, target, **kw):
    guard = {"n": 0}

    def should_stop():
        guard["n"] += 1
        return guard["n"] > 5000  # hard cap so a broken controller can't hang the test

    return drive_to(sim.get_state, sim.send, target, CFG, clock=lambda: 0.0,
                    sleep=lambda s: None, should_stop=should_stop, **kw)


def test_drive_to_reaches_a_waypoint_it_must_turn_toward():
    sim = SimVehicle(0, 0, 0)  # facing +x; target is up-and-left, must curve to it
    result = _drive(sim, (100, 100))
    assert result.outcome == "arrived"
    assert math.hypot(sim.x - 100, sim.y - 100) <= CFG.arrive_radius
    assert result.ticks < 2000  # converged, didn't crawl


def test_drive_to_reaches_a_waypoint_behind_it():
    sim = SimVehicle(0, 0, 0)  # facing +x; target is directly behind -> hardest turn
    result = _drive(sim, (-80, -20))
    assert result.outcome == "arrived"


def test_drive_to_sends_a_stop_when_it_finishes():
    sim = SimVehicle(0, 0, 0)
    _drive(sim, (30, 0))
    assert sim.commands[-1].kind == "stop"  # car isn't left rolling


def test_drive_to_times_out_when_the_target_is_unreachable():
    # gain 0 -> never steers; a car facing away can't reach the point -> timeout.
    cfg = DriveConfig(gain=0.0, arrive_radius=5.0, timeout_s=2.0, max_fps=0)
    sim = SimVehicle(0, 0, 90)  # facing +y, target on +x, but never steers
    ticks = iter(float(n) * 0.1 for n in range(100000))
    result = drive_to(sim.get_state, sim.send, (500, 0), cfg,
                      clock=lambda: next(ticks), sleep=lambda s: None)
    assert result.outcome == "timeout"


def test_drive_to_stops_when_asked():
    sim = SimVehicle(0, 0, 0)
    calls = {"n": 0}

    def should_stop():
        calls["n"] += 1
        return calls["n"] > 3

    result = drive_to(sim.get_state, sim.send, (100, 100), CFG,
                      clock=lambda: 0.0, sleep=lambda s: None, should_stop=should_stop)
    assert result.outcome == "stopped"
    assert sim.commands[-1].kind == "stop"


# -- suspension wobble rejection ----------------------------------------------

def test_smooth_steer_low_passes_and_slew_limits():
    assert smooth_steer(0.0, 1.0, smoothing=1.0, slew=2.0) == 1.0   # no filtering -> passthrough
    assert smooth_steer(0.0, 1.0, smoothing=0.0, slew=2.0) == 0.0   # fully smoothed -> holds prev
    assert smooth_steer(0.0, 1.0, smoothing=1.0, slew=0.3) == 0.3   # slew caps the jump
    assert smooth_steer(0.5, 0.6, smoothing=0.5, slew=0.3) == 0.55  # EMA halfway


def test_drive_to_still_reaches_a_wobbly_waypoint():
    sim = SimVehicle(0, 0, 0, wobble=8.0, wobble_hz=3.0)  # 8deg suspension bob
    result = _drive(sim, (100, 100))
    assert result.outcome == "arrived"
    assert math.hypot(sim.x - 100, sim.y - 100) <= CFG.arrive_radius


def test_filter_reduces_steering_jerk_under_wobble():
    # Same wobble, two controllers: filtered (default) vs effectively unfiltered
    # (react instantly, no slew cap). The filter must saw the wheel back and
    # forth far less -- that's the whole point of rejecting suspension wobble.
    filtered = SimVehicle(0, 0, 0, wobble=8.0, wobble_hz=3.0)
    _drive(filtered, (100, 100))

    unfiltered_cfg = DriveConfig(gain=0.03, arrive_radius=5.0, timeout_s=1e9, max_fps=0,
                                 steer_smoothing=1.0, steer_slew=2.0)
    raw = SimVehicle(0, 0, 0, wobble=8.0, wobble_hz=3.0)
    guard = {"n": 0}
    drive_to(raw.get_state, raw.send, (100, 100), unfiltered_cfg, clock=lambda: 0.0,
             sleep=lambda s: None, should_stop=lambda: guard.__setitem__("n", guard["n"] + 1) or guard["n"] > 5000)

    assert filtered.steer_jerk() < 0.5 * raw.steer_jerk()  # markedly smoother steering
