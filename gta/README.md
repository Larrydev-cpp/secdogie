# secdogie-gta

Drive **GTA V (single-player)** from secdogie through a small ScriptHookV plugin.
A cloud model decides *where* to go; a local proportional control law keeps the
car pointed there. Same two-tier split as the rest of secdogie — the model is
the slow strategist, this is the fast controller.

```
  secdogie strategist (LLM)            "drive to the marker, then to the garage"
        │  target waypoint
        ▼
  driving.drive_to  ── steer/throttle ──►  ScriptHookV .asi plugin (C++, in-game)
        ▲                                        │ reads natives, maps input
        └──────────── GameState ─────────────────┘ (or hands the waypoint to GTA's
                (JSON over a local socket)          own driving AI via a task)
```

## ⚠️ Single-player only

ScriptHookV is a single-player modding library and **disables itself in GTA
Online**. Automating, botting, or aim-assisting **GTA Online is against
Rockstar's rules, gets accounts banned, and ruins the game for real players** —
don't point any of this at it. Everything here is for your own single-player
game.

## Why a plugin instead of pixels

GTA is photorealistic 3D: a template matcher can't track a car or ped across
camera rotation, and a cloud vision model runs at ~1 Hz — far too slow to drive.
Two ways to get real, fast game state and control, both single-player:

- **A ScriptHookV plugin (this package's path).** A C++ `.asi` calls GTA's
  script *natives* to read exact state (player position, heading, speed, health,
  the map waypoint) and to act — either by feeding the car input, or by handing
  the whole job to GTA's own AI (`TASK_VEHICLE_DRIVE_TO_COORD`, etc.). This is
  GTA's equivalent of Minecraft's Mineflayer: skip the pixels, use the game's
  own API.
- Pixels + YOLO + relative mouse-look (secdogie's `aim/`), if you specifically
  want to play from the screen. Harder, and only sensible in single-player.

## The control law (proven here)

`driving.steer_to` is a proportional heading controller: bearing to the target
minus current heading gives a signed error; steer to null it, and ease the
throttle off in hard turns so it doesn't overshoot. Its convergence is loop math,
not GTA, so it's proven by driving a **simulated vehicle** to a waypoint
(`tests/test_driving.py`) — it reaches points ahead, to the side, and directly
behind it, and always sends a `stop` when it finishes.

```python
from secdogie_gta import steer_to, GameState
c = steer_to(GameState(x=0, y=0, heading=0), target=(100, 100))
c.steer, c.throttle, c.arrived   # e.g. 0.9, 0.65, False
```

`steer` is `-1..1`, `+1` = turn toward *increasing* heading; the plugin maps
that to the game's left/right and maps GTA's heading convention into the frame
the control law uses, so this module stays pure and convention-agnostic.

## The wire protocol (`protocol.py`)

Newline-delimited JSON over a local TCP socket. Plugin → secdogie each tick:

```json
{"x": -1037.4, "y": -2738.0, "heading": 152.3, "speed": 14.2,
 "health": 1.0, "in_vehicle": true, "waypoint": [-75.0, -818.0]}
```

secdogie → plugin, either low-level control or a delegated task:

```json
{"kind": "drive_control", "steer": 0.42, "throttle": 0.8}
{"kind": "task", "task": "TASK_VEHICLE_DRIVE_TO_COORD", "args": {"x": -75, "y": -818, "speed": 20}}
{"kind": "stop"}
```

`state_from_json` / `command_to_json` are the tested codec; malformed input off
the socket raises `ProtocolError` rather than crashing the loop.

## Run it (on your machine)

```bash
pip install -e gta
# with GTA running single-player + the plugin loaded and listening on :47800
secdogie-gta drive --to -75,-818          # drive to a world point
secdogie-gta drive                        # or to the map waypoint you set in-game
```

## The ScriptHookV plugin (you build this half)

The C++ `.asi` is the on-machine half secdogie can't be — it needs the game.
Its contract is exactly the protocol above. Sketch of the loop it runs (using
the ScriptHookV SDK):

```cpp
// each tick, inside your ScriptHookV script thread:
Ped   player   = PLAYER::PLAYER_PED_ID();
Vehicle veh    = PED::GET_VEHICLE_PED_IS_IN(player, false);
Vector3 pos    = ENTITY::GET_ENTITY_COORDS(player, true);
float   heading = ENTITY::GET_ENTITY_HEADING(player);   // map to the control-law frame
// ... read speed / health / the map waypoint blip ...
send_json_line(state);                                  // -> secdogie

Command cmd = recv_json_line();                         // <- secdogie
if (cmd.kind == "drive_control") {
    // map steer/throttle to CONTROLS::_SET_CONTROL_NORMAL / vehicle controls
} else if (cmd.kind == "task") {
    // e.g. AI::TASK_VEHICLE_DRIVE_TO_COORD(player, veh, x, y, z, speed, ...)
}
```

## What's proven vs on-machine

Proven headless (`pytest gta/tests`): the protocol codec + validation, and the
control law's convergence against a simulated vehicle. Only on your Windows
machine, with GTA + the plugin: that the plugin reads the natives correctly, that
`drive_control` maps to real input (or `task` to real AI), and that the car
actually reaches the point. The seam is deliberate — secdogie's hands can't reach
inside the game; the plugin is where you connect them.

## Layout

```
secdogie_gta/
  protocol.py   GameState / Command + JSON codec + validation      [pure, tested]
  driving.py    heading math + steer_to control law + drive_to loop [pure, tested]
  bridge.py     socket client to the ScriptHookV plugin             [on-machine]
  cli.py        secdogie-gta drive --to X,Y
tests/          protocol codec/validation + control-law convergence
```

## Test

```bash
pip install -e gta && pytest gta/tests -q
```
