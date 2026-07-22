"""Tests for the windowed-build safety net (frozen_runtime.py). The Windows
console reattach + the crash messagebox are on-machine (they need the Win32
console API / a display); what's proved here is the headless-safe core: the
tee writer, the log-path resolution, and that the whole thing stays a no-op
when running from source."""
import io
import sys

from secdogie_agent import frozen_runtime as fr


class _TtyStringIO(io.StringIO):
    def isatty(self):
        return True


def test_tee_writes_to_every_stream_and_reports_a_tty():
    plain, tty = io.StringIO(), _TtyStringIO()
    tee = fr._Tee(plain, tty, None)  # None streams are dropped
    assert tee.write("hello") == 5
    assert plain.getvalue() == "hello"
    assert tty.getvalue() == "hello"
    assert tee.isatty() is True  # at least one underlying stream is a terminal


def test_tee_is_not_a_tty_when_no_underlying_stream_is():
    tee = fr._Tee(io.StringIO(), io.StringIO())
    assert tee.isatty() is False


def test_tee_survives_a_broken_stream():
    class Broken:
        def write(self, data):
            raise OSError("stream closed")

        def flush(self):
            raise OSError("stream closed")

    good = io.StringIO()
    tee = fr._Tee(Broken(), good)
    tee.write("x")  # must not raise even though one stream throws
    tee.flush()
    assert good.getvalue() == "x"


def test_bootstrap_is_a_noop_when_not_frozen(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    out, err, hook = sys.stdout, sys.stderr, sys.excepthook
    fr.bootstrap()
    # Running from source must not touch stdio or the excepthook at all.
    assert sys.stdout is out
    assert sys.stderr is err
    assert sys.excepthook is hook


def test_attach_parent_console_is_false_off_windows():
    if sys.platform.startswith("win"):
        return  # the real AttachConsole path only runs on Windows
    assert fr._attach_parent_console() is False


def test_log_path_sits_next_to_the_exe_when_frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(fr, "_exe_dir", lambda: tmp_path)
    assert fr.log_path() == tmp_path / "secdogie.log"


def test_log_path_falls_back_when_theres_no_exe_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(fr, "_exe_dir", lambda: None)
    monkeypatch.setattr(fr.Path, "home", lambda: tmp_path)
    p = fr.log_path()
    assert p == tmp_path / ".secdogie" / "secdogie.log"
    assert p.parent.exists()  # the directory was created, so opening it will work
