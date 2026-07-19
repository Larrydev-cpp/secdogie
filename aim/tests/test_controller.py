"""Aim control-law tests, all headless. The plant ("moving the mouse by dx
shifts the target's projection by -k*dx") is simulated, which is exactly the
right test: the control law's convergence is a property of the loop math, not
of SendInput -- the machine-specific half (does the camera really turn, what is
the real k) is what the CLI's calibrate command measures on real hardware."""
from secdogie_aim.controller import AimConfig, Detection, EngageResult, aim_step, engage
from secdogie_aim.mouse import RecordingMouse

CFG = AimConfig(gain=0.5, max_step=60, deadzone_px=3, fire_radius_px=12,
                fire_cooldown_s=0.25, lost_frames=5, timeout_s=100, max_fps=0)


# -- aim_step: the pure P law -------------------------------------------------

def test_aim_step_is_proportional():
    assert aim_step(40, -20, CFG) == (20, -10)  # gain 0.5


def test_aim_step_clamps_to_max_step():
    dx, dy = aim_step(1000, -1000, CFG)
    assert (dx, dy) == (60, -60)  # a hot gain walks at max_step, never slingshots


def test_aim_step_deadzone_suppresses_jitter():
    assert aim_step(2, 2, CFG) == (0, 0)  # inside the radial deadzone
    assert aim_step(4, 0, CFG) != (0, 0)  # just outside -> still corrects


def test_aim_step_invert_negates_the_chosen_axis():
    base = aim_step(40, -20, CFG)  # (20, -10)
    ix = aim_step(40, -20, AimConfig(gain=0.5, invert_x=True))
    iy = aim_step(40, -20, AimConfig(gain=0.5, invert_y=True))
    assert ix == (-base[0], base[1])  # only x flipped
    assert iy == (base[0], -base[1])  # only y flipped


# -- the simulated plant ------------------------------------------------------

class SimWorld:
    """A 1-target world: the target sits at (tx, ty) in a WxH frame; turning
    the camera by (dx, dy) counts shifts its projection by -k * counts. This is
    the linear small-angle model of mouse-look; `k` is the unknown the real
    machine calibrates, so tests exercise several values of it."""

    def __init__(self, tx: float, ty: float, *, k: float = 1.0, size=(800, 600), label="ender_dragon"):
        self.tx, self.ty = tx, ty
        self.k = k
        self.size = size
        self.label = label
        self.mouse = RecordingMouse()
        self.errors_at_click: list[tuple[float, float]] = []  # aim error when each shot fired
        # engage() drives a RelativeMouse; wrap ours so camera turns move the target.
        outer = self

        class PlantMouse:
            def move(self, dx: int, dy: int) -> None:
                outer.mouse.move(dx, dy)
                outer.tx -= outer.k * dx
                outer.ty -= outer.k * dy

            def press(self) -> None:
                outer.mouse.press()

            def release(self) -> None:
                outer.mouse.release()

            def click(self) -> None:
                outer.mouse.click()
                outer.errors_at_click.append(outer.error())

        self.plant_mouse = PlantMouse()

    def detect(self, frame_png: bytes) -> list[Detection]:
        return [Detection(cx=self.tx, cy=self.ty, w=60, h=40, confidence=0.9, label=self.label)]

    def error(self) -> tuple[float, float]:
        return (self.tx - self.size[0] / 2, self.ty - self.size[1] / 2)


def _fake_clock():
    t = {"v": 0.0}

    def clock() -> float:
        t["v"] += 0.02  # 50 fps worth of time per call, deterministic
        return t["v"]

    return clock


def _engage(world: SimWorld, cfg: AimConfig = CFG, **kw) -> EngageResult:
    return engage(
        lambda: b"", world, world.plant_mouse, world.size, cfg,
        clock=_fake_clock(), sleep=lambda s: None, **kw,
    )


# -- engage: convergence and fire gating --------------------------------------

def test_engage_converges_onto_an_offset_target_and_fires():
    # Target starts far off-center; the P loop must pull the error inside the
    # fire radius and shoot. This is the headless convergence proof.
    world = SimWorld(700, 100)  # error (+300, -200) from the 400x300 center
    result = _engage(world)
    assert result.shots >= 1
    ex, ey = world.error()
    assert abs(ex) <= CFG.fire_radius_px and abs(ey) <= CFG.fire_radius_px
    assert result.outcome == "timeout"  # combat has no "done"; the budget ends it


def test_engage_converges_for_different_plant_gains():
    # The real counts-per-degree is unknown pre-calibration; the loop must still
    # converge (not diverge) for plants both weaker and stronger than assumed.
    for k in (0.5, 1.0, 2.0):
        world = SimWorld(700, 500, k=k)
        result = _engage(world)
        assert result.shots >= 1, f"never got on target with plant k={k}"


def test_engage_with_hot_gain_stays_bounded():
    # gain*k far too high makes plain P unstable; the max_step clamp turns that
    # divergence into a BOUNDED limit cycle near the target instead of a
    # slingshot off-screen. Boundedness is the honest guarantee here -- an
    # overdriven loop orbits the target without settling into the fire radius,
    # which is exactly why the CLI's calibrate step exists.
    hot = AimConfig(gain=5.0, max_step=40, deadzone_px=3, fire_radius_px=12,
                    fire_cooldown_s=0, lost_frames=5, timeout_s=2.0, max_fps=0)
    world = SimWorld(750, 550, k=1.5)
    result = _engage(world, cfg=hot)
    assert result.outcome == "timeout"  # never diverged into "lost", never crashed
    assert all(abs(dx) <= 40 and abs(dy) <= 40 for dx, dy in world.mouse.moves)
    ex, ey = world.error()
    # The limit cycle stays near the target: within one clamped step's reach
    # (k * max_step = 60px), nowhere near the initial ~430px error.
    assert abs(ex) <= 1.5 * 40 and abs(ey) <= 1.5 * 40


def test_engage_fires_only_within_the_radius():
    world = SimWorld(700, 100)
    result = _engage(world)
    # Every shot must have been taken while the aim error was inside the fire
    # radius -- the plant records the exact error at each click.
    assert result.shots >= 1
    for ex, ey in world.errors_at_click:
        assert ex * ex + ey * ey <= CFG.fire_radius_px**2


def test_engage_respects_fire_cooldown():
    # On-target from frame one, 0.02s per frame, 0.25s cooldown -> shots are
    # paced ~13 frames apart, not every frame.
    world = SimWorld(400, 300)  # dead center already
    result = _engage(world)
    assert result.shots >= 2
    assert result.shots <= result.frames // 10  # far fewer shots than frames


def test_engage_gives_up_lost_when_target_vanishes():
    class NoTargets:
        def detect(self, frame_png: bytes) -> list[Detection]:
            return []

    result = engage(
        lambda: b"", NoTargets(), RecordingMouse(), (800, 600), CFG,
        clock=_fake_clock(), sleep=lambda s: None,
    )
    assert result.outcome == "lost"
    assert result.frames == CFG.lost_frames


def test_engage_filters_by_label_and_confidence():
    class Noisy:
        def detect(self, frame_png: bytes) -> list[Detection]:
            return [
                Detection(cx=100, cy=100, w=10, h=10, confidence=0.95, label="cow"),
                Detection(cx=700, cy=500, w=60, h=40, confidence=0.3, label="ender_dragon"),
            ]

    mouse = RecordingMouse()
    # Only low-confidence dragons + a confident cow: with label filtering the
    # cow must be ignored and the weak dragon rejected -> "lost", no movement.
    result = engage(
        lambda: b"", Noisy(), mouse, (800, 600), CFG,
        label="ender_dragon", clock=_fake_clock(), sleep=lambda s: None,
    )
    assert result.outcome == "lost"
    assert mouse.moves == [] and mouse.events == []


def test_engage_detects_divergence_on_inverted_sign():
    # k<0 = the game turns the camera the WRONG way, so every steer grows the
    # error (positive feedback). The guard must catch it instead of spinning.
    cfg = AimConfig(gain=0.5, max_step=60, deadzone_px=3, fire_radius_px=12,
                    fire_cooldown_s=0.25, lost_frames=1000, timeout_s=100, max_fps=0,
                    diverge_frames=12)
    world = SimWorld(500, 400, k=-1.0)
    result = _engage(world, cfg=cfg)
    assert result.outcome == "diverging"
    assert result.frames <= 15  # caught in ~a dozen frames, not spinning forever


def test_engage_invert_restores_convergence_on_inverted_sign():
    # Same wrong-sign plant, but invert both axes -> negative feedback again, so
    # the loop converges and fires exactly like an un-inverted game.
    cfg = AimConfig(gain=0.5, max_step=60, deadzone_px=3, fire_radius_px=12,
                    fire_cooldown_s=0.25, lost_frames=5, timeout_s=100, max_fps=0,
                    invert_x=True, invert_y=True)
    world = SimWorld(700, 500, k=-1.0)
    result = _engage(world, cfg=cfg)
    assert result.shots >= 1
    assert result.outcome == "timeout"


def test_diverge_guard_ignores_a_bounded_limit_cycle():
    # An over-hot but correctly-signed loop orbits the target (error oscillates,
    # never grows monotonically); the guard must NOT mistake that for divergence.
    hot = AimConfig(gain=5.0, max_step=40, deadzone_px=3, fire_radius_px=12,
                    fire_cooldown_s=0, lost_frames=5, timeout_s=2.0, max_fps=0,
                    diverge_frames=12)
    world = SimWorld(750, 550, k=1.5)
    result = _engage(world, cfg=hot)
    assert result.outcome == "timeout"  # bounded orbit, not "diverging"


def test_engage_stops_when_should_stop_fires():
    world = SimWorld(700, 100)
    calls = {"n": 0}

    def should_stop() -> bool:
        calls["n"] += 1
        return calls["n"] > 3

    result = _engage(world, should_stop=should_stop)
    assert result.outcome == "stopped"


def test_engage_times_out_against_an_untrackable_target():
    class Teleporter:
        """Target rejoins at a far corner every frame -- error never shrinks."""

        def __init__(self):
            self.flip = False

        def detect(self, frame_png: bytes) -> list[Detection]:
            self.flip = not self.flip
            x = 790 if self.flip else 10
            return [Detection(cx=x, cy=10, w=60, h=40, confidence=0.9, label="ender_dragon")]

    short = AimConfig(gain=0.5, max_step=10, deadzone_px=3, fire_radius_px=12,
                      fire_cooldown_s=0.25, lost_frames=100, timeout_s=1.0, max_fps=0)
    result = engage(
        lambda: b"", Teleporter(), RecordingMouse(), (800, 600), short,
        clock=_fake_clock(), sleep=lambda s: None,
    )
    assert result.outcome == "timeout"
    assert result.shots == 0
