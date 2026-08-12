"""High-risk classification for commercial CAD safety gate."""
from secdogie_agent.actions import HIGH_RISK_KINDS, is_high_risk
from secdogie_agent.providers.base import Action


def _act(kind: str, **kwargs) -> Action:
    raw = {"action": kind, **kwargs}
    return Action.from_dict(raw)


def test_open_and_elevated_are_high_risk():
    assert "open" in HIGH_RISK_KINDS
    assert "run_elevated" in HIGH_RISK_KINDS
    assert is_high_risk(_act("open", path="C:\\file.dwg"))
    assert is_high_risk(_act("run_elevated", path="whoami"))


def test_click_and_type_are_not_high_risk():
    assert not is_high_risk(_act("left_click", x=10, y=20))
    assert not is_high_risk(_act("type", text="hello"))
    assert not is_high_risk(_act("scroll", x=1, y=1, dy=-3))


def test_ctrl_s_and_save_as_are_high_risk():
    assert is_high_risk(_act("key", keys=["ctrl", "s"]))
    assert is_high_risk(_act("key", keys=["control", "S"]))
    assert is_high_risk(_act("key", keys=["ctrl", "shift", "s"]))
    assert is_high_risk(_act("key", keys=["cmd", "s"]))


def test_close_quit_print_are_high_risk():
    assert is_high_risk(_act("key", keys=["ctrl", "w"]))
    assert is_high_risk(_act("key", keys=["alt", "f4"]))
    assert is_high_risk(_act("key", keys=["ctrl", "q"]))
    assert is_high_risk(_act("key", keys=["ctrl", "p"]))


def test_delete_keys_are_high_risk():
    assert is_high_risk(_act("key", keys=["delete"]))
    assert is_high_risk(_act("key", keys=["del"]))
    assert is_high_risk(_act("key", keys=["backspace"]))
    assert is_high_risk(_act("key", keys=["ctrl", "delete"]))


def test_benign_keys_are_not_high_risk():
    assert not is_high_risk(_act("key", keys=["enter"]))
    assert not is_high_risk(_act("key", keys=["tab"]))
    assert not is_high_risk(_act("key", keys=["ctrl", "c"]))
    assert not is_high_risk(_act("key", keys=["ctrl", "z"]))
