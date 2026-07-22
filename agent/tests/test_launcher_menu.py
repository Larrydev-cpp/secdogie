"""Tests for the built-in launcher menu -- the frosted-glass chooser a
double-clicked exe shows. The window itself needs a display and is on-machine;
what's proved here is the headless-safe core: the choice->argv table, the
gating that decides *when* the menu appears, and the no-display fallback."""
import sys

from secdogie_agent import launcher_menu as m


def test_every_choice_maps_to_argv_and_keys_are_unique():
    keys = [c.key for c in m.MENU_CHOICES]
    assert len(keys) == len(set(keys))  # no duplicate keys
    for c in m.MENU_CHOICES:
        assert c.args and all(a.startswith("-") for a in c.args)  # real flags
        assert m.args_for(c.key) == list(c.args)


def test_args_for_known_and_unknown():
    assert m.args_for("ax") == ["--gui", "--desktop-ax"]
    assert m.args_for("config") == ["--init-config"]
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
    import builtins

    real_import = builtins.__import__

    def no_tk(name, *a, **k):
        if name == "tkinter":
            raise ImportError("no tkinter")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_tk)
    assert m.show_menu() == ["--gui"]
