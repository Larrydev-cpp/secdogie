"""Decision-priority tests: the whole value of the tactician is the ORDER --
survival before damage, crystals before the dragon. All pure, no perception."""
from secdogie_commander.tactician import CRYSTAL, DRAGON, CommandConfig, GameState, decide

CFG = CommandConfig()


def _state(**kw) -> GameState:
    # A healthy, well-supplied default so each test overrides only what it probes.
    base = dict(dragon_alive=True, crystals_remaining=0, player_health=1.0,
                arrows=64, dragon_perched=False, threatened=False)
    base.update(kw)
    return GameState(**base)


def test_dead_dragon_is_done():
    assert decide(_state(dragon_alive=False, crystals_remaining=3), CFG).kind == "done"


def test_critical_and_threatened_retreats():
    d = decide(_state(player_health=0.2, threatened=True, crystals_remaining=3), CFG)
    assert d.kind == "retreat"


def test_low_health_resupplies_before_fighting_even_with_crystals_up():
    # Survival outranks the crystals-first rule: don't go clear crystals at 30% hp.
    d = decide(_state(player_health=0.3, crystals_remaining=3), CFG)
    assert d.kind == "resupply"


def test_out_of_arrows_resupplies():
    assert decide(_state(arrows=1), CFG).kind == "resupply"


def test_crystals_are_fought_before_the_dragon():
    d = decide(_state(crystals_remaining=2, dragon_perched=True), CFG)
    assert d.kind == "fight" and d.target_label == CRYSTAL  # crystals win even if perched


def test_perched_dragon_is_meleed():
    d = decide(_state(crystals_remaining=0, dragon_perched=True), CFG)
    assert d.kind == "fight" and d.target_label == DRAGON


def test_flying_dragon_is_the_default_fight():
    d = decide(_state(crystals_remaining=0, dragon_perched=False), CFG)
    assert d.kind == "fight" and d.target_label == DRAGON


def test_threatened_but_healthy_does_not_retreat():
    # Retreat is only for critical health under attack; a healthy player fights on.
    d = decide(_state(threatened=True, player_health=0.9), CFG)
    assert d.kind == "fight"
