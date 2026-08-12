"""Tests for the built-in launcher menu -- the frosted-glass chooser a
double-clicked exe shows. The window itself needs a display and is on-machine;
what's proved here is the headless-safe core: the choice->argv table, the
gating that decides *when* the menu appears, and the no-display fallback."""
import sys
from unittest import mock

from secdogie_agent import launcher_menu as m


def test_every_choice_maps_to_argv_and_keys_are_unique():
    keys = [c.key for c in m.MENU_CHOICES]
    assert len(keys) == len(set(keys))  # no duplicate keys
    for c in m.MENU_CHOICES:
        if c.key == "config":
            # Special: opens the GUI key dialog; does not return CLI flags.
            assert c.args == ()
            assert m.args_for(c.key) == []
        else:
            assert c.args and all(a.startswith("-") for a in c.args)  # real flags
            assert m.args_for(c.key) == list(c.args)


def test_args_for_known_and_unknown():
    assert m.args_for("ax") == ["--gui", "--desktop-ax"]
    assert m.args_for("config") == []  # GUI dialog, not --init-config
    assert m.args_for("nope") is None


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


def test_show_menu_falls_back_to_gui_without_a_display(monkeypatch):
    # With no usable display/tkinter the chooser can't build; it must return a
    # sensible argv rather than raise, so a double-clicked exe still does
    # something. Force the tkinter import to fail to simulate that deterministically.
    # Also stub the key gate so we reach the window path.
    import builtins

    real_import = builtins.__import__

    def no_tk(name, *a, **k):
        if name == "tkinter":
            raise ImportError("no tkinter")
        return real_import(name, *a, **k)

    monkeypatch.setattr(m, "ensure_api_key_or_prompt", lambda: True)
    monkeypatch.setattr(builtins, "__import__", no_tk)
    assert m.show_menu() == ["--gui"]


def test_show_menu_exits_when_first_run_key_cancelled(monkeypatch):
    monkeypatch.setattr(m, "ensure_api_key_or_prompt", lambda: False)
    assert m.show_menu() is None


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


# -- the --menu flag: show the real chooser from a normal CLI run --------------

def test_menu_flag_shows_the_chooser_and_runs_the_choice():
    # `--menu` lets you see/run the real menu without building the exe. It should
    # pop the chooser and hand its chosen flags to the rest of the CLI.
    from secdogie_agent import cli

    with mock.patch.object(m, "show_menu", return_value=["--gui", "--dry-run"]) as sm, \
         mock.patch.object(cli, "run", return_value=0) as run, \
         mock.patch("secdogie_agent.cli_common.resolve_provider", return_value=object()), \
         mock.patch("secdogie_agent.dialog.gui_available", return_value=True), \
         mock.patch("secdogie_agent.dialog.ask_task", return_value="a task"), \
         mock.patch("secdogie_agent.config.has_configured_api_key", return_value=True):
        assert cli.main(["--menu"]) == 0
        assert sm.called
        assert run.call_args.args[1].dry_run is True   # the chosen card's flags took effect


def test_menu_flag_cancelled_exits_without_running():
    from secdogie_agent import cli

    with mock.patch.object(m, "show_menu", return_value=None), \
         mock.patch.object(cli, "run", return_value=0) as run:
        assert cli.main(["--menu"]) == 0    # closing the chooser just exits
        assert not run.called
