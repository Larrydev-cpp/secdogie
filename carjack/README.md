# secdogie-carjack

The on-foot step the driving stack was missing: **walk up to the nearest car
and get in.** The driving control law (`gta/`) assumes you're already in a
vehicle — this is what produces one. Same two-tier shape as the rest of
secdogie: a detector says *which* pixels are a car; a fast local loop faces it,
walks to it, and presses the enter-vehicle key.

## ⚠️ Single-player only

This automates a single-player game you own. Don't point it at any online mode
— see the warnings in [`gta/README.md`](../gta/README.md) and
[`aim/README.md`](../aim/README.md).

## Why this is the easy half of perception

Unlike the aim controller (which needs a YOLO model fine-tuned on a specific
target like the Ender Dragon), **a car is a stock COCO class** — an
out-of-the-box `yolov8n.pt` already detects `car`/`truck`/`bus`. So there's
nothing to train: point the detector at stock weights with `--label car`.

## The control law (proven headless)

`approach_step(car, frame_size, cfg)` decides one tick:

- **far away** → walk forward, turning the camera to keep the car centred;
- **close** (the car's box fills `enter_box_frac` of the frame height — a car
  looming large is one you're beside) → stop, finish facing it, then press the
  enter key.

"Nearest" car = the largest detection box (nearer things project bigger).
`approach_and_enter(...)` closes the loop and **always releases the forward key
on exit** so the character never keeps running. Its convergence is loop math,
not a specific game, so it's proven by driving a **simulated approach**
(`tests/test_approach.py`): the car grows as you walk and shifts as you turn,
and the loop must centre it, close the distance, and fire the enter key — for
normal and inverted camera directions alike.

```python
from secdogie_carjack import approach_step, ApproachConfig
from secdogie_aim.controller import Detection
cmd = approach_step(Detection(cx=700, cy=300, w=64, h=40, confidence=0.9, label="car"),
                    (800, 600), ApproachConfig())
cmd.turn, cmd.walk, cmd.enter   # e.g. 40, True, False -> turn right, keep walking
```

## Run it (on the machine, single-player)

```bash
pip install -e 'carjack[yolo]'
secdogie-carjack --weights yolov8n.pt --label car --enter-key f
```

- `--enter-key` is the game's enter/exit-vehicle key (GTA IV keyboard default:
  `f`); `--forward-key` defaults to `w`.
- **Camera turns the wrong way / spins?** Same inverted-axis issue as the aim
  controller — add `--invert-x` (or fix the game's look-inversion setting).
- `--gain` is camera counts per pixel of error; start ~0.3–0.5.

On `entered` it exits 0; hand off to driving (keyboard `WASD`, or
`secdogie-gta` on GTA V's plugin bridge).

## What's proven vs on-machine

Proven headless (`pytest carjack/tests`): the decision (`approach_step`),
nearest-car selection, and the full loop's convergence + forward-key release.
What only the real machine can show: that YOLO detects the car, that the camera
actually turns, and that the enter key jacks the car — the same on-machine
boundary as `aim/`.

## Layout

```
secdogie_carjack/
  approach.py   the control law (approach_step) + loop (approach_and_enter)
  cli.py        wires YOLO + relative mouse-look + keyboard into the loop
tests/          headless convergence tests against a simulated approach
```
