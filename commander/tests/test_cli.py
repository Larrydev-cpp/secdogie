"""CLI tests for the offline `plan` path (the `run` path needs the machine)."""
import json

import pytest
from secdogie_commander import cli


def test_requires_a_subcommand():
    with pytest.raises(SystemExit):
        cli.main([])


def test_plan_prints_a_decision_per_state(tmp_path, capsys):
    script = tmp_path / "states.json"
    script.write_text(json.dumps([
        {"crystals_remaining": 2, "player_health": 1.0, "arrows": 64},
        {"crystals_remaining": 0, "dragon_perched": True, "player_health": 1.0, "arrows": 64},
        {"player_health": 0.2, "arrows": 64},
        {"dragon_alive": False},
    ]))
    rc = cli.main(["plan", "--script", str(script)])
    assert rc == 0
    out = capsys.readouterr().out.splitlines()
    assert "end_crystal" in out[0]
    assert "ender_dragon" in out[1]
    assert "resupply" in out[2]
    assert "done" in out[3]


def test_plan_rejects_a_non_list_script(tmp_path, capsys):
    script = tmp_path / "bad.json"
    script.write_text(json.dumps({"not": "a list"}))
    rc = cli.main(["plan", "--script", str(script)])
    assert rc == 2
    assert "JSON list" in capsys.readouterr().err


def test_plan_reports_a_missing_script(capsys):
    rc = cli.main(["plan", "--script", "/no/such/file.json"])
    assert rc == 2
    assert "could not read" in capsys.readouterr().err
