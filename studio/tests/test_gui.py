"""The window itself, offscreen: first run through the form, the node started
from the button, and closing the window stops the node."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from secdogie_studio.app import build_window  # noqa: E402
from secdogie_studio.controller import RUNNING, StudioController  # noqa: E402
from secdogie_studio.nodeproc import wait_until  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _pump(qapp, pred, timeout=30.0):
    def step():
        qapp.processEvents()
        return pred()

    return wait_until(step, timeout, interval=0.02)


def test_first_run_start_and_close(qapp, tmp_path):
    c = StudioController(tmp_path / "p", kdf="min")
    w = build_window(c, poll_ms=50)
    w.show()
    qapp.processEvents()
    assert w.stack.currentWidget() is w.first_run and not w.nav.isEnabled()

    w.first_run.passphrase.setText("short")
    w.first_run.confirm.setText("different")
    w.first_run.submit()
    assert "至少" in w.first_run.problems.text() and not c.profile.initialized

    w.first_run.passphrase.setText("operator passphrase")
    w.first_run.confirm.setText("operator passphrase")
    w.first_run.submit()
    assert w.first_run.passphrase.text() == ""  # the passphrase does not linger in the form
    assert _pump(qapp, lambda: w.stack.currentWidget() is w.node_page)
    assert w.nav.isEnabled() and w.node_page.start.isEnabled() and not w.node_page.link.isEnabled()

    w.node_page.start.click()
    assert _pump(qapp, lambda: c.snapshot().node_state == RUNNING)
    assert _pump(qapp, lambda: w.node_page.link.isEnabled())
    pid = c.snapshot().pid
    w.nav.setCurrentRow(4)  # a planned page: says which slice builds it
    qapp.processEvents()
    assert "G4" in w.stack.currentWidget().text()

    w.close()
    qapp.processEvents()
    s = c.snapshot()
    assert s.returncode == 0 and s.pid == pid  # closing the window stopped the node
