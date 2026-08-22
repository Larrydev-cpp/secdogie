"""Tests for the SYSTEM-elevation module (elevate.py).

The token dance (DuplicateTokenEx off winlogon + CreateProcessAsUser) is
on-machine and needs Windows + Administrator. What's proved here is everything
that decides *whether* a launch may happen -- the operator allowlist that
confines the model, the pure plan_launch verdict, and clean/honest degradation
off Windows -- because that's the safety boundary, not the ctypes plumbing."""
import sys

from secdogie_agent import elevate as e

# -- the operator allowlist (the model-facing boundary) ------------------------

def test_empty_allowlist_permits_nothing():
    # No allowlist == elevation off. The model can escalate nothing.
    assert e.is_permitted("sc stop Spooler", ()) is False
    assert e.is_permitted("anything", []) is False


def test_only_exactly_declared_commands_are_permitted():
    allow = ("sc stop Spooler", "msiexec /i C:\\pkg\\app.msi /qn")
    assert e.is_permitted("sc stop Spooler", allow) is True
    assert e.is_permitted("msiexec /i C:\\pkg\\app.msi /qn", allow) is True
    # A different argument is a different command -- no prefix/glob matching, so
    # "stop Spooler" can never stand in for "stop Themes".
    assert e.is_permitted("sc stop Themes", allow) is False
    assert e.is_permitted("sc stop", allow) is False
    assert e.is_permitted("sc stop Spooler && del C:\\", allow) is False


def test_matching_ignores_only_surrounding_and_collapsed_whitespace():
    allow = ("sc stop Spooler",)
    assert e.is_permitted("  sc stop Spooler  ", allow) is True
    assert e.is_permitted("sc   stop\tSpooler", allow) is True
    assert e.is_permitted("", allow) is False
    assert e.is_permitted(None, allow) is False


def test_normalize_command():
    assert e.normalize_command("  a   b\tc ") == "a b c"
    assert e.normalize_command(None) == ""
    assert e.normalize_command("") == ""


# -- plan_launch: the pure verdict --------------------------------------------

def test_plan_launch_reports_the_most_fundamental_blocker_first():
    # wrong OS beats missing privilege beats a bad request.
    assert e.plan_launch("cmd", elevated=True, session_id=1, on_windows=False).reason == e.UNSUPPORTED
    assert e.plan_launch("cmd", elevated=False, session_id=1, on_windows=True).reason == e.NOT_ELEVATED
    assert e.plan_launch("", elevated=True, session_id=1, on_windows=True).reason == e.BAD_COMMAND
    assert e.plan_launch("cmd", elevated=True, session_id=None, on_windows=True).reason == e.NO_SESSION


def test_plan_launch_proceeds_only_when_everything_is_satisfied():
    d = e.plan_launch("cmd /c whoami", elevated=True, session_id=2, on_windows=True)
    assert d.ok is True and d.reason == "proceed"


def test_plan_launch_never_proceeds_unelevated_even_with_a_session():
    # The core refusal: without an admin token to start from, the answer is
    # "run elevated first", never an attempt to escalate anyway.
    assert e.plan_launch("cmd", elevated=False, session_id=5, on_windows=True).ok is False


# -- run_as_system: honest refusal off Windows / unelevated -------------------

def test_run_as_system_refuses_cleanly_off_windows():
    # Never raises, never launches, gives an actionable reason.
    r = e.run_as_system("cmd /c whoami")
    assert r.outcome == e.UNSUPPORTED
    assert r.pid is None
    assert "Windows" in r.detail


def test_run_as_system_reports_not_elevated_when_windows_but_unprivileged(monkeypatch):
    # Simulate Windows-but-not-admin: it must refuse with the "run elevated
    # first" message and NOT invoke the launcher.
    monkeypatch.setattr(e.sys, "platform", "win32")
    monkeypatch.setattr(e, "is_elevated", lambda: False)
    monkeypatch.setattr(e, "active_console_session", lambda: 1)
    called = []
    r = e.run_as_system("cmd", _launcher=lambda c, s, **kw: called.append((c, s)))
    assert r.outcome == e.NOT_ELEVATED
    assert called == []                 # the token dance was never attempted
    assert "Administrator" in r.detail


def test_run_as_system_dispatches_to_the_launcher_when_all_checks_pass(monkeypatch):
    monkeypatch.setattr(e.sys, "platform", "win32")
    monkeypatch.setattr(e, "is_elevated", lambda: True)
    monkeypatch.setattr(e, "active_console_session", lambda: 3)
    seen = {}

    def fake_launch(command, session_id, **kw):
        seen["command"], seen["session"] = command, session_id
        return e.ElevateResult(e.LAUNCHED, pid=4242, detail="ok")

    r = e.run_as_system("regedit", _launcher=fake_launch)
    assert r.outcome == e.LAUNCHED and r.pid == 4242
    assert seen == {"command": "regedit", "session": 3}   # launched into the active session


def test_run_as_system_turns_a_launcher_crash_into_a_failed_result(monkeypatch):
    monkeypatch.setattr(e.sys, "platform", "win32")
    monkeypatch.setattr(e, "is_elevated", lambda: True)
    monkeypatch.setattr(e, "active_console_session", lambda: 1)

    def boom(c, s, **kw):
        raise OSError("CreateProcessAsUser blew up")

    r = e.run_as_system("cmd", _launcher=boom)
    assert r.outcome == e.FAILED and "blew up" in r.detail


# -- detection degrades cleanly off Windows -----------------------------------

def test_detection_is_clean_off_windows(monkeypatch):
    monkeypatch.setattr(e.sys, "platform", "linux")
    assert e.is_elevated() is False
    assert e.current_integrity() == "unknown"
    assert e.active_console_session() is None


def test_real_detection_never_raises_on_this_box():
    # On the (non-Windows) CI box the real paths must return the safe defaults
    # without touching Windows-only ctypes.
    if sys.platform.startswith("win"):
        return
    assert e.is_elevated() is False
    assert e.active_console_session() is None


# -- the wall above SYSTEM: TrustedInstaller / anti-EDR -----------------------

def test_trusted_installer_impersonation_is_refused():
    r = e.try_impersonate_trusted_installer()
    assert r.outcome == e.REFUSED_IDENTITY
    assert r.pid is None
    assert "TrustedInstaller" in r.detail
    # Asking twice is still a refusal -- there is no 'try harder' branch.
    r2 = e.try_impersonate_trusted_installer()
    assert r2.outcome == e.REFUSED_IDENTITY


def test_anti_edr_is_refused():
    r = e.try_edr_evasion()
    assert r.outcome == e.REFUSED_IDENTITY
    assert "Anti-EDR" in r.detail
