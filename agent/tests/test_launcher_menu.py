"""What a double-clicked exe does (it opens the one secdogie window; any
argument keeps the CLI), and the CLI's first-run API key gate. The card menu
is retired: nothing here may bring it back (``--menu`` is gone too)."""
import sys
from unittest import mock

import pytest
from secdogie_agent import launcher_menu as m


def test_menu_offered_only_for_a_frozen_build_with_no_args(monkeypatch):
    # Not frozen (running from source / pip): never show the menu, even bare.
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert m.should_offer([]) is False

    # Frozen (packaged exe): a bare double-click shows it; any explicit arg
    # means a deliberate invocation and the CLI must stay menu-free.
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert m.should_offer([]) is True
    assert m.should_offer(["--gui"]) is False
    assert m.should_offer(["do a thing"]) is False
    assert m.should_offer(["--init-config"]) is False


def test_ensure_api_key_or_prompt_short_circuits_when_present(monkeypatch):
    from secdogie_agent import config as config_mod

    monkeypatch.setattr(config_mod, "has_configured_api_key", lambda: True)
    with mock.patch.object(m, "show_key_dialog") as dlg:
        assert m.ensure_api_key_or_prompt() is True
        assert not dlg.called


def test_ensure_api_key_or_prompt_opens_dialog_when_missing(monkeypatch):
    from secdogie_agent import config as config_mod

    monkeypatch.setattr(config_mod, "has_configured_api_key", lambda: False)
    with mock.patch.object(m, "show_key_dialog", return_value=True) as dlg:
        assert m.ensure_api_key_or_prompt() is True
        dlg.assert_called_once_with(first_run=True)


# -- a double-click opens the one secdogie window ------------------------------

def test_a_double_clicked_exe_opens_the_window(monkeypatch):
    import sys
    import types

    from secdogie_agent import cli

    opened = []
    fake = types.ModuleType("secdogie_app.window")
    fake.main = lambda argv: opened.append(argv) or 0
    monkeypatch.setitem(sys.modules, "secdogie_app", types.ModuleType("secdogie_app"))
    monkeypatch.setitem(sys.modules, "secdogie_app.window", fake)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with mock.patch.object(cli, "run") as run:
        assert cli.main([]) == 0
    assert opened == [[]] and not run.called


def test_any_argument_keeps_the_cli(monkeypatch):
    import sys

    from secdogie_agent import cli

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with mock.patch.object(cli, "open_window") as window:
        with pytest.raises(SystemExit):
            cli.main(["--help"])
    assert not window.called


def test_the_menu_is_gone():
    from secdogie_agent import cli

    assert not hasattr(m, "show_menu") and not hasattr(m, "MENU_CHOICES")
    with pytest.raises(SystemExit):
        cli.main(["--menu"])  # an unknown flag now
