"""The tactician: the slow strategist that sequences the fight.

This is the top of the two-tier design. The fast controllers already exist --
Node A (secdogie 2D macros, logistics) and Node B (aim.engage, combat) -- and
each does its own tight loop. What was missing is the brain that decides WHICH
one should be running right now and against WHAT: clear the crystals before the
dragon (crystals heal it), take the perch window for melee, bow it while it
flies, and break off to heal/restock before that becomes fatal.

That decision is a small priority state machine over a GameState snapshot, and
it is fully testable here -- the ordering (heal before fight, crystals before
dragon) is loop math, not perception. What is NOT here is where GameState comes
from: reading the health bar / arrow count off the HUD and counting crystals /
judging the dragon's pose with YOLO happens on the real machine, behind the
`StateReader` protocol (the same injection seam aim.Detector / reflex use), and
is stubbed in tests. The node runners are injected too, so this core imports
neither agent nor aim.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

# Target class labels handed to Node B; kept as named constants so the tactician
# and the YOLO model agree on one spelling instead of scattering string literals.
CRYSTAL = "end_crystal"
DRAGON = "ender_dragon"


@dataclass(frozen=True)
class GameState:
    """A snapshot of everything the tactician needs to pick the next move. Filled
    by a StateReader on the real machine; the tactician never perceives directly."""

    dragon_alive: bool = True
    crystals_remaining: int = 0  # end crystals still healing the dragon
    player_health: float = 1.0  # 0..1
    arrows: int = 0
    dragon_perched: bool = False  # on the central fountain -> melee window
    threatened: bool = False  # incoming dragon breath / charge


@runtime_checkable
class StateReader(Protocol):
    """Perceive the current GameState. The real implementation reads the HUD +
    YOLO on the machine; tests pass a scripted stub."""

    def read(self) -> GameState: ...


DecisionKind = Literal["fight", "resupply", "retreat", "done"]


@dataclass(frozen=True)
class Decision:
    """A closed decision: `kind` says which node to run (or that we're finished),
    `target_label` names Node B's target when fighting, `reason` is for logs."""

    kind: DecisionKind
    reason: str
    target_label: str | None = None  # set iff kind == "fight"


@dataclass(frozen=True)
class CommandConfig:
    heal_health: float = 0.4  # at/below this, break off to heal before fighting
    retreat_health: float = 0.25  # at/below this AND threatened, disengage now
    min_arrows: int = 4  # fewer than this and we can't sustain the bow phase
    max_rounds: int = 200  # hard cap so a broken StateReader can't loop forever


def decide(state: GameState, cfg: CommandConfig) -> Decision:
    """Pick the next move by fixed priority. Order is the whole point: survival
    before damage, and crystals before the dragon (they heal it)."""
    if not state.dragon_alive:
        return Decision("done", "dragon down")
    if state.threatened and state.player_health <= cfg.retreat_health:
        return Decision("retreat", "under attack at critical health")
    if state.player_health <= cfg.heal_health or state.arrows < cfg.min_arrows:
        low = "low health" if state.player_health <= cfg.heal_health else "out of arrows"
        return Decision("resupply", f"{low}: restock before engaging")
    if state.crystals_remaining > 0:
        return Decision("fight", f"{state.crystals_remaining} crystal(s) still healing the dragon", CRYSTAL)
    if state.dragon_perched:
        return Decision("fight", "dragon perched: melee window", DRAGON)
    return Decision("fight", "dragon flying: bow it", DRAGON)


# CommandConfig is frozen, so one shared default instance is safe as a default arg.
_DEFAULT_CONFIG = CommandConfig()


@dataclass(frozen=True)
class CommandResult:
    outcome: Literal["done", "stopped", "exhausted"]
    rounds: int
    last: Decision | None


def command(
    reader: StateReader,
    run_logistics: Callable[[Decision], None],
    run_combat: Callable[[str], None],
    cfg: CommandConfig = _DEFAULT_CONFIG,
    *,
    should_stop: Callable[[], bool] | None = None,
    clock: Callable[[], float] = time.monotonic,
    on_decision: Callable[[Decision], None] | None = None,
) -> CommandResult:
    """Read -> decide -> dispatch, until the dragon is down, a caller stops us,
    or max_rounds is hit. `run_combat(label)` runs Node B against that target;
    `run_logistics(decision)` runs Node A (resupply/retreat). Both are injected,
    so this loop has no dependency on agent or aim -- the CLI wires the real
    ones. `clock` is unused by the logic but kept for parity/future pacing."""
    last: Decision | None = None
    for rounds in range(1, cfg.max_rounds + 1):
        if should_stop is not None and should_stop():
            return CommandResult("stopped", rounds - 1, last)

        decision = decide(reader.read(), cfg)
        last = decision
        if on_decision is not None:
            on_decision(decision)

        if decision.kind == "done":
            return CommandResult("done", rounds, last)
        if decision.kind == "fight":
            run_combat(decision.target_label or DRAGON)
        else:  # resupply | retreat -> Node A
            run_logistics(decision)

    return CommandResult("exhausted", cfg.max_rounds, last)
