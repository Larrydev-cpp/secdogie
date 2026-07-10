# secdogie-aim

**Node B of the hybrid game architecture**: a real-time combat controller that
pulls the crosshair onto a detected target with **relative mouse-look** and
fires when it's on target. (The `commander/` package sits above this and
`agent/`, deciding *when* to fight vs restock and sequencing the two.) Built for the single-player / own-server Minecraft
ender-dragon project; secdogie's normal 2D machinery (Node A) handles menus,
crafting and hotbar, and this node takes over only for the fight.

```
                 ┌──────────────────────────── one physical mouse/keyboard ──┐
                 │                                                            │
  Node A ────────┤ secdogie agent + macros: inventory, crafting, hotbar,      │
  (2D logistics) │ potions, reconnect (absolute cursor, agent/'s input lock)  │
                 │                                                            │
        ⇅ input baton: secdogie-handoff InputArbiter (OS file lock)           │
                 │                                                            │
  Node B ────────┤ THIS PACKAGE: capture → YOLO detect → P-control camera     │
  (3D combat)    │ turn (relative counts) → fire — at frame rate, locally     │
                 └────────────────────────────────────────────────────────────┘
```

## Why a separate input path

secdogie's desktop control positions the cursor at **absolute** screen
coordinates (pyautogui) — right for 2D UI, useless in a captured-pointer 3D
game: Minecraft hides the pointer and rotates the camera from **relative
motion deltas**; there is no cursor position to set. So this package injects
relative counts at the OS level:

- **Windows**: `SendInput` with `MOUSEEVENTF_MOVE` (relative mickeys), via
  ctypes — no extra dependency.
- **Linux**: a `uinput` virtual device emitting `REL_X`/`REL_Y`
  (`pip install 'secdogie-aim[linux]'`, needs write access to `/dev/uinput`).

## The control law (and why it's provable headless)

In a captured-pointer game the crosshair is **always the frame center**, so:

```
error = detection center − frame center
step  = clamp(gain × error, ±max_step)     # P control + per-frame clamp
```

Turning the camera moves the target's projection toward the center — a classic
proportional loop. Its convergence is a property of the loop math, not of the
OS: the tests drive `engage()` against a simulated plant ("moving the mouse by
dx shifts the target by −k·dx") and prove it converges for a range of plant
gains, stays bounded even with a badly hot gain (`max_step`), fires only inside
`fire_radius_px`, respects the fire cooldown, and ends as `lost`/`timeout`/
`stopped`. A radial `deadzone_px` stops the camera jittering on detection-box
noise. No I/D terms: the plant is linear and memoryless, P converges without
steady-state error, and fewer knobs means less to misconfigure.

## Perception: YOLO, not template matching, not a cloud LLM

- A **cloud vision LLM** is ~1 Hz — physics of a network round trip; it cannot
  sit in this loop no matter which model it is. It stays the *tactician* above.
- The agent's **reflex layer** (NCC template matching) has no rotation/scale
  invariance — right for 2D UI, hopeless against a 3D model that turns and
  grows on approach. (Minecraft's *pixel art style* doesn't help: the rendering
  is fully 3D.)
- A small **fine-tuned YOLO** (e.g. yolov8n on a few hundred labeled dragon
  frames) runs at frame rate on a local GPU. `YoloDetector` adapts it to the
  `Detector` protocol; `pip install 'secdogie-aim[yolo]'`.

## Verify on the machine (this cannot be proven headless)

What the test suite proves: the control law (convergence, clamping, fire
gating, loss/timeout), the mouse protocol, the CLI plumbing. What it cannot
prove — and what you verify once on the real Windows + GPU box:

1. **The camera actually turns** (the SendInput path):

   ```bash
   pip install -e aim
   secdogie-aim calibrate            # alt-tab into Minecraft during the delay
   ```

   The camera should sweep right in 10 pulses. Measure how many degrees the
   total 1000 counts turned you: `counts-per-degree = 1000 / degrees`. If the
   camera didn't move, stop here — fix injection before anything else.

2. **YOLO sees the dragon**: record a few minutes of dragon footage, label a
   few hundred frames (the blocky silhouette makes this easy), fine-tune
   yolov8n, then sanity-check detections on saved frames.

3. **The fight**:

   ```bash
   pip install -e 'aim[yolo]'
   secdogie-aim engage --weights dragon.pt --label ender_dragon --gain 0.4
   ```

   Start with a low `--gain` and raise it until tracking is snappy without
   overshoot. `engage` takes the input baton from Node A first (waits for it),
   and hands it back when the engagement ends.

## Wiring Node A (no agent changes needed)

Node A wraps each macro step in the baton and yields between steps — both
hooks already exist in the agent (`AgentConfig.should_stop`,
`agent/secdogie_agent/loop.py`):

```python
from secdogie_handoff import InputArbiter
from secdogie_agent.loop import AgentConfig

arb = InputArbiter()
config = AgentConfig(
    task="restock: craft beds, refill hotbar",
    macro_path="logistics.macro.json",
    should_stop=lambda: arb.yield_requested() is not None,  # exit code 5 = yielded
)
with arb.hold("node-a"):
    rc = run(provider, config)
# baton released here; Node B's pending acquire() succeeds -- that success IS
# the proof Node A let go (see handoff/README.md)
```

## Layout

```
secdogie_aim/
  mouse.py       RelativeMouse protocol + RecordingMouse fake; SendInput / uinput injectors
  controller.py  Detection/Detector, AimConfig, aim_step (P law), engage (track-and-fire loop)
  yolo.py        Ultralytics adapter implementing Detector ([yolo] extra)
  cli.py         calibrate (measure counts-per-degree) / engage (fight under the baton)
tests/           headless proofs: convergence, clamping, fire gating, outcomes, CLI
```

## Test

```bash
pip install -e aim && pytest aim/tests -q
```

## Scope & honesty

Single-player / own-server use. On public multiplayer servers, automation and
aim assistance violate server rules and harm other players — don't point this
there. And this is a *utility* controller, not an esports aimbot: it converges
in a handful of frames and fires, which is plenty for a dragon-sized target;
sub-frame flick precision is out of scope.
