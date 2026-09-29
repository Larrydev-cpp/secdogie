"""Console command-line checks that run before anything is started."""
from __future__ import annotations

import pytest
from secdogie_console.cli import main


def test_revocation_flags_need_an_allowlist(capsys):
    assert main(["--no-browser", "--masters", "masters.conf"]) == 2
    assert "--masters" in capsys.readouterr().err


def test_revocations_need_masters(tmp_path, capsys):
    pytest.importorskip("nacl")
    from secdogie_identity import Identity

    allow = tmp_path / "operators.allow"
    allow.write_text(f"authorized_did = {Identity.generate().did}\n", encoding="utf-8")
    code = main(["--no-browser", "--insecure-dev", "--operator-authorized", str(allow),
                 "--revocations", str(tmp_path / "r.jsonl")])
    assert code == 2
    assert "--masters" in capsys.readouterr().err


def _no_fleet(monkeypatch):
    """Reaching the fleet at all is the failure: never start a real server here."""
    import secdogie_fleet.server as server_mod

    def refuse(*a, **k):
        raise AssertionError("an unauthenticated fleet was started")

    monkeypatch.setattr(server_mod, "FleetServer", refuse)


def test_the_fleet_never_runs_unauthenticated_by_default(capsys, monkeypatch):
    _no_fleet(monkeypatch)
    assert main(["--no-browser"]) == 2
    err = capsys.readouterr().err
    assert "without DID authentication" in err and "--insecure-dev" in err


def test_the_controller_never_accepts_unsigned_commands_by_default():
    from secdogie_console.controller import ConsoleController

    with pytest.raises(ValueError, match="allow_unsigned_local"):
        ConsoleController(object())

