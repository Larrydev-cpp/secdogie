"""CLI smoke tests: argument parsing and the failure paths that need no real
device, model weights, or display."""
import pytest
from secdogie_aim import cli


def test_cli_requires_a_subcommand():
    with pytest.raises(SystemExit):
        cli.main([])


def test_engage_requires_weights():
    with pytest.raises(SystemExit):
        cli.main(["engage"])


def test_calibrate_pulses_the_mouse(monkeypatch):
    import secdogie_aim.mouse as mouse_mod

    m = mouse_mod.RecordingMouse()
    monkeypatch.setattr(mouse_mod, "open_mouse", lambda: m)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)

    rc = cli.main(["calibrate", "--counts", "50", "--pulses", "3", "--delay", "0", "--interval", "0"])
    assert rc == 0
    assert m.moves == [(50, 0), (50, 0), (50, 0)]


def test_unsupported_platform_yields_a_clean_error(monkeypatch, capsys):
    import secdogie_aim.mouse as mouse_mod

    monkeypatch.setattr(mouse_mod.sys, "platform", "sunos5")
    rc = cli.main(["calibrate", "--delay", "0"])
    assert rc == 2
    assert "Windows and Linux" in capsys.readouterr().err
