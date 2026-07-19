"""command() driver tests: it must read -> decide -> dispatch to the right node
and terminate correctly. Node runners and the StateReader are injected, so no
agent/aim/display is involved."""
from secdogie_commander.tactician import CommandConfig, GameState, command

CFG = CommandConfig(max_rounds=50)


class ScriptedReader:
    """Yields a fixed sequence of states, holding the last one forever (so a run
    that never reaches `done` on its own hits the injected stop/round cap)."""

    def __init__(self, states):
        self.states = states
        self.i = 0

    def read(self) -> GameState:
        s = self.states[min(self.i, len(self.states) - 1)]
        self.i += 1
        return s


def _run(states, **kw):
    log = []  # (node, detail) in dispatch order
    result = command(
        ScriptedReader(states),
        run_logistics=lambda d: log.append(("logistics", d.kind)),
        run_combat=lambda label: log.append(("combat", label)),
        cfg=CFG,
        **kw,
    )
    return result, log


def test_clears_crystals_then_dragon_then_done():
    states = [
        GameState(crystals_remaining=2, player_health=1.0, arrows=64),
        GameState(crystals_remaining=0, player_health=1.0, arrows=64, dragon_perched=True),
        GameState(dragon_alive=False),
    ]
    result, log = _run(states)
    assert result.outcome == "done"
    assert log == [("combat", "end_crystal"), ("combat", "ender_dragon")]


def test_resupply_is_dispatched_to_logistics_when_low():
    states = [
        GameState(crystals_remaining=1, player_health=0.2, arrows=64),  # too hurt -> heal first
        GameState(crystals_remaining=1, player_health=1.0, arrows=64),  # healed -> clear crystal
        GameState(dragon_alive=False),
    ]
    result, log = _run(states)
    assert result.outcome == "done"
    assert log[0] == ("logistics", "resupply")
    assert ("combat", "end_crystal") in log


def test_should_stop_ends_as_stopped():
    calls = {"n": 0}

    def should_stop() -> bool:
        calls["n"] += 1
        return calls["n"] > 2

    result, _ = _run([GameState(crystals_remaining=1)], should_stop=should_stop)
    assert result.outcome == "stopped"


def test_max_rounds_caps_a_never_ending_fight():
    # Dragon never dies and player stays fine -> the loop would run forever; the
    # round cap must stop it as "exhausted".
    result, log = _run([GameState(crystals_remaining=0, dragon_alive=True, player_health=1.0, arrows=64)])
    assert result.outcome == "exhausted"
    assert result.rounds == CFG.max_rounds
    assert len(log) == CFG.max_rounds


def test_retreat_goes_to_logistics():
    states = [
        GameState(player_health=0.2, threatened=True, crystals_remaining=1, arrows=64),
        GameState(dragon_alive=False),
    ]
    result, log = _run(states)
    assert log[0] == ("logistics", "retreat")
    assert result.outcome == "done"


def test_on_decision_callback_sees_every_decision():
    seen = []
    _run([GameState(dragon_alive=False)], on_decision=lambda d: seen.append(d.kind))
    assert seen == ["done"]
