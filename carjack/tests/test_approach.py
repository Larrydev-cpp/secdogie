"""Approach control-law tests, all headless. The 'car' is simulated: turning
the camera shifts its horizontal position, walking forward grows its box (it
gets closer). The loop must centre it, close the distance, and press the enter
key -- proving convergence as loop math, exactly like the aim/driving laws."""
from secdogie_aim.controller import Detection

from secdogie_carjack.approach import (
    ApproachConfig,
    approach_and_enter,
    approach_step,
    nearest_car,
)

FRAME = (800, 600)
CFG = ApproachConfig(gain=0.4, max_step=40, center_deadzone_px=40, enter_box_frac=0.5,
                     enter_center_px=100, min_confidence=0.4, label="car",
                     lost_frames=5, timeout_s=100, max_fps=0)


def _car(cx, cy, w, h, conf=0.9, label="car"):
    return Detection(cx=cx, cy=cy, w=w, h=h, confidence=conf, label=label)


# -- nearest_car: pick the closest (biggest) matching vehicle ------------------

def test_nearest_car_picks_the_largest_box():
    small = _car(100, 300, 40, 30)
    big = _car(600, 300, 160, 120)
    assert nearest_car([small, big], CFG) is big  # nearer car = bigger projection


def test_nearest_car_filters_by_label_and_confidence():
    ped = _car(400, 300, 200, 300, label="person")  # big but not a car
    faint = _car(400, 300, 200, 150, conf=0.2)       # a car but below min_confidence
    assert nearest_car([ped, faint], CFG) is None


def test_nearest_car_label_none_takes_any_class():
    cfg = ApproachConfig(label=None, min_confidence=0.4)
    truck = _car(400, 300, 200, 150, label="truck")
    assert nearest_car([truck], cfg) is truck


# -- approach_step: the pure decision -----------------------------------------

def test_step_walks_and_faces_a_far_offcentre_car():
    car = _car(700, 300, 40, 30)  # small (far), off to the right
    cmd = approach_step(car, FRAME, CFG)
    assert cmd.walk is True and cmd.enter is False
    assert cmd.turn > 0  # err_x = +300 -> turn right toward it


def test_step_walks_straight_when_facing_a_far_car():
    car = _car(400, 300, 40, 30)  # dead ahead, still far
    cmd = approach_step(car, FRAME, CFG)
    assert cmd == type(cmd)(turn=0, walk=True, enter=False)


def test_step_enters_when_close_and_centred():
    car = _car(410, 300, 400, 320)  # box height 320 >= 0.5*600, centre within 100px
    cmd = approach_step(car, FRAME, CFG)
    assert cmd.enter is True and cmd.walk is False and cmd.turn == 0


def test_step_faces_before_entering_when_close_but_offcentre():
    car = _car(700, 300, 400, 320)  # close (big) but err_x = +300 > enter_center_px
    cmd = approach_step(car, FRAME, CFG)
    assert cmd.enter is False and cmd.walk is False  # stop, turn in place to face it
    assert cmd.turn > 0


def test_step_invert_x_flips_the_turn():
    car = _car(700, 300, 40, 30)
    normal = approach_step(car, FRAME, CFG)
    inv = approach_step(car, FRAME, ApproachConfig(**{**CFG.__dict__, "invert_x": True}))
    assert inv.turn == -normal.turn


# -- the simulated approach and the full loop ---------------------------------

class SimApproach:
    """A single car in a WxH frame. Turning the camera by dx shifts the car's
    cx by -k*dx (mouse-look plant); each walking frame grows the car's box (you
    get closer). detect() reports the current car."""

    def __init__(self, cx, h, *, k=1.0, grow=8.0, size=FRAME, label="car"):
        self.cx = cx
        self.cy = size[1] / 2
        self.h = h
        self.w = h * 1.6
        self.k = k
        self.grow = grow
        self.size = size
        self.label = label
        self.walking = False
        self.entered = False
        self.turns: list[int] = []

    def capture(self) -> bytes:
        if self.walking:  # walking forward closes the distance -> bigger box
            self.h += self.grow
            self.w = self.h * 1.6
        return b""

    def detect(self, frame_png: bytes) -> list[Detection]:
        return [Detection(cx=self.cx, cy=self.cy, w=self.w, h=self.h, confidence=0.9, label=self.label)]

    def turn(self, dx: int) -> None:
        self.turns.append(dx)
        self.cx -= self.k * dx  # camera turn shifts the car's projection

    def walk(self, on: bool) -> None:
        self.walking = on

    def enter(self) -> None:
        self.entered = True


def _fake_clock():
    t = {"v": 0.0}

    def clock() -> float:
        t["v"] += 0.02
        return t["v"]

    return clock


def _run(world: SimApproach, cfg: ApproachConfig = CFG, **kw):
    return approach_and_enter(
        world.capture, world, world.turn, world.walk, world.enter, world.size, cfg,
        clock=_fake_clock(), sleep=lambda s: None, **kw,
    )


def test_loop_walks_up_to_a_far_car_and_enters():
    world = SimApproach(cx=650, h=40)  # off-centre and far
    result = _run(world)
    assert result.outcome == "entered"
    assert world.entered is True
    # ended up roughly facing the car when it jacked it
    assert abs(world.cx - world.size[0] / 2) <= CFG.enter_center_px


def test_loop_converges_for_wrong_camera_gain_directions():
    # Whatever the plant sign/scale, invert_x must let it converge (mirrors the
    # aim controller's inversion). k<0 = camera turns the wrong way.
    world = SimApproach(cx=650, h=40, k=-1.0)
    cfg = ApproachConfig(**{**CFG.__dict__, "invert_x": True})
    result = _run(world, cfg=cfg)
    assert result.outcome == "entered"


def test_loop_releases_forward_key_on_every_exit():
    world = SimApproach(cx=400, h=40)
    _run(world)
    assert world.walking is False  # never leaves the character running


def test_loop_gives_up_lost_when_no_car():
    class NoCars:
        def detect(self, frame_png):
            return []

    world = SimApproach(cx=400, h=40)  # only used for capture/effects
    result = approach_and_enter(
        lambda: b"", NoCars(), world.turn, world.walk, world.enter, FRAME, CFG,
        clock=_fake_clock(), sleep=lambda s: None,
    )
    assert result.outcome == "lost"
    assert world.walking is False


def test_loop_stops_when_should_stop_fires():
    world = SimApproach(cx=650, h=40)
    calls = {"n": 0}

    def should_stop():
        calls["n"] += 1
        return calls["n"] > 3

    result = _run(world, should_stop=should_stop)
    assert result.outcome == "stopped"
    assert world.walking is False
