"""Desktop command-line checks that run before any window or socket opens."""
from __future__ import annotations

import pytest
from secdogie_desktop.cli import main


@pytest.fixture(autouse=True)
def no_fleet(monkeypatch):
    """Reaching the fleet at all is the failure: never start a real server here."""
    server_mod = pytest.importorskip("secdogie_fleet.server")

    def refuse(*a, **k):
        raise AssertionError("an unauthenticated fleet was started")

    monkeypatch.setattr(server_mod, "FleetServer", refuse)


def test_the_fleet_never_runs_unauthenticated_by_default(capsys):
    assert main([]) == 2
    err = capsys.readouterr().err
    assert "without DID authentication" in err and "--insecure-dev" in err


def test_half_a_secure_configuration_is_refused(capsys):
    assert main(["--identity", "coordinator.key"]) == 2
    assert "both --identity and --authorized" in capsys.readouterr().err
