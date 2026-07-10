# secdogie-commander

The **tactician** — the top of the hybrid game architecture. The fast
controllers already exist; this is the slow strategist that decides *which* one
runs now and against *what*, and sequences the whole dragon fight.

```
   ┌──────────────────────── secdogie-commander (this package) ────────────────┐
   │  read GameState → decide() → dispatch → repeat until the dragon is down    │
   └───────────────┬───────────────────────────────────┬───────────────────────┘
      fight(label) │                                    │ resupply / retreat
                   ▼                                    ▼
        Node B: secdogie-aim                  Node A: secdogie-agent macros
        (relative mouse-look + YOLO)          (inventory, potions, arrows)
                   └────────────── secdogie-handoff input baton ────────────────┘
                              (exactly one drives the mouse at a time)
```

## The decision, in priority order

`decide(state, cfg)` is a pure priority function — its **order** is the point:

| # | condition | decision | why |
|---|-----------|----------|-----|
| 1 | dragon not alive | `done` | fight over |
| 2 | threatened **and** health ≤ `retreat_health` | `retreat` → Node A | don't die mid-swing |
| 3 | health ≤ `heal_health` **or** arrows < `min_arrows` | `resupply` → Node A | survive/sustain before damage |
| 4 | crystals remaining > 0 | `fight(end_crystal)` → Node B | crystals **heal the dragon** — clear them first |
| 5 | dragon perched | `fight(ender_dragon)` → Node B | perch = melee window, best DPS |
| 6 | otherwise (flying) | `fight(ender_dragon)` → Node B | bow it |

`command()` runs read → decide → dispatch in a loop until `done`, a caller
`should_stop`, or `max_rounds` (a guard so a broken reader can't spin forever).

## What's proven here vs on the machine

**Proven headless** (`pytest commander/tests`): the priority ordering (heal
before fight, crystals before dragon), the dispatch sequencing, and every
termination path — all against injected node runners and a scripted reader.
See it yourself, offline:

```bash
pip install -e commander
secdogie-commander plan --script fight.json   # prints the decision for each state
```

**On the machine** (`secdogie-commander run`): this is where perception lives.
The commander never reads pixels itself — you supply a `StateReader`:

```python
# my_reader.py  (on your machine)
from secdogie_commander import GameState

class MyReader:
    def read(self) -> GameState:
        return GameState(
            dragon_alive=...,             # dragon boss bar present?
            crystals_remaining=...,       # count end_crystal detections (YOLO)
            player_health=read_hud_health(),   # HUD hearts
            arrows=read_hud_arrows(),          # hotbar arrow stack
            dragon_perched=judge_pose(...),    # dragon low + centered on the fountain
            threatened=breath_or_charge(...),
        )

def make_reader():          # the 'module:factory' the CLI imports
    return MyReader()
```

```bash
pip install -e commander -e agent -e 'aim[yolo]' -e handoff
secdogie-commander run \
    --state-reader my_reader:make_reader \
    --weights dragon.pt --macro logistics.macro.json
```

Node B takes the input baton for each engagement; Node A holds it for each
resupply/retreat and yields the instant Node B raises its hand
(`should_stop = arbiter.yield_requested`, agent exit code 5).

## Layout

```
secdogie_commander/
  tactician.py  GameState, StateReader, Decision, CommandConfig, decide(), command()  [pure, tested]
  wiring.py     on-machine glue: aim.engage / agent.run under the baton (lazy imports)
  cli.py        plan (offline dry-run) / run (real fight)
tests/          decision priority table, dispatch sequencing, termination, CLI plan
```

## Test

```bash
pip install -e commander && pytest commander/tests -q
```

## Scope & honesty

Single-player / own-server use. The tactician is a deterministic strategy
sequencer, not a learned agent: it plays the textbook dragon fight (crystals →
perch → bow, heal when low). It is only as good as the `StateReader` feeding it
— get the health/arrow/crystal reads right and the ordering takes care of the
rest.
