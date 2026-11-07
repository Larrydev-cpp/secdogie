"""The Studio window (PySide6): a thin view over ``StudioController``.

Qt is imported lazily inside ``run_gui`` / the classes' builders, so the rest of
the package imports on a machine without PySide6 or a display. The view never
runs slow work itself and never touches Qt from another thread: it calls the
controller and redraws from ``snapshot()`` on a timer.

No modal dialogs, no focus grabs: errors and progress show inline. Closing the
window stops the node -- that is the whole lifecycle (no tray, no autostart in
this slice).
"""
from __future__ import annotations

from pathlib import Path

from . import viewmodel as vm
from .controller import StudioController
from .profile import ROLES


def build_window(controller: StudioController, *, poll_ms: int = 300):
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import (
        QCheckBox,
        QFormLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QMainWindow,
        QPlainTextEdit,
        QPushButton,
        QStackedWidget,
        QVBoxLayout,
        QWidget,
    )

    mono = QFont("monospace")
    mono.setStyleHint(QFont.StyleHint.Monospace)

    class FirstRunPage(QWidget):
        def __init__(self):
            super().__init__()
            lay = QVBoxLayout(self)
            intro = QLabel(
                "首次运行：在本机生成三把钥匙。\n"
                "· 节点钥：节点签自己做的事；\n"
                "· App 钥：Studio 与本机节点对话；\n"
                "· 操作员钥：唯一能授权破坏性动作的钥匙，用口令加密保存，每次签名时才解锁。\n"
                "能力授权一项也不预先签发：在操作员授权之前，节点拒绝一切会改变外界的动作。")
            intro.setWordWrap(True)
            lay.addWidget(intro)
            form = QFormLayout()
            self.passphrase = QLineEdit()
            self.passphrase.setEchoMode(QLineEdit.EchoMode.Password)
            self.confirm = QLineEdit()
            self.confirm.setEchoMode(QLineEdit.EchoMode.Password)
            form.addRow("操作员口令", self.passphrase)
            form.addRow("再输入一次", self.confirm)
            lay.addLayout(form)
            lay.addWidget(QLabel("本节点向网格提供的角色（以后可改）："))
            self.roles = {}
            for role in ROLES:
                box = QCheckBox(vm.ROLE_LABELS[role])
                box.setChecked(role == "forward")
                self.roles[role] = box
                lay.addWidget(box)
            self.problems = QLabel("")
            self.problems.setWordWrap(True)
            lay.addWidget(self.problems)
            self.create = QPushButton("生成钥匙并创建配置")
            self.create.clicked.connect(self.submit)
            lay.addWidget(self.create)
            lay.addStretch(1)

        def chosen_roles(self) -> tuple[str, ...]:
            return tuple(r for r in ROLES if self.roles[r].isChecked())

        def submit(self) -> None:
            problems = vm.first_run_problems(self.passphrase.text(), self.confirm.text(), self.chosen_roles())
            self.problems.setText("\n".join(problems))
            if not problems:
                controller.create_profile(self.passphrase.text(), roles=self.chosen_roles())
                self.passphrase.clear()
                self.confirm.clear()

    class NodePage(QWidget):
        def __init__(self):
            super().__init__()
            lay = QVBoxLayout(self)
            self.form = QFormLayout()
            self.values: dict[str, QLabel] = {}
            lay.addLayout(self.form)
            buttons = QHBoxLayout()
            self.start = QPushButton("启动节点")
            self.stop = QPushButton("停止节点")
            self.link = QPushButton("检查与节点的连接")
            self.start.clicked.connect(controller.start_node)
            self.stop.clicked.connect(lambda: controller.stop_node())
            self.link.clicked.connect(controller.check_link)
            for b in (self.start, self.stop, self.link):
                buttons.addWidget(b)
            buttons.addStretch(1)
            lay.addLayout(buttons)
            lay.addWidget(QLabel("节点日志"))
            self.log = QPlainTextEdit()
            self.log.setReadOnly(True)
            self.log.setFont(mono)
            lay.addWidget(self.log, 1)
            self._log_shown: tuple[str, ...] = ()

        def show_snapshot(self, s) -> None:
            for label, value in vm.node_rows(s):
                if label not in self.values:
                    v = QLabel()
                    v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                    v.setWordWrap(True)
                    self.values[label] = v
                    self.form.addRow(label, v)
                self.values[label].setText(value)
            self.start.setEnabled(vm.can_start(s))
            self.stop.setEnabled(vm.can_stop(s))
            self.link.setEnabled(vm.can_check_link(s))
            if s.log != self._log_shown:
                self._log_shown = s.log
                self.log.setPlainText("\n".join(s.log))
                self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    class MainWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("secdogie Studio")
            self.resize(960, 640)
            central = QWidget()
            row = QHBoxLayout(central)
            self.nav = QListWidget()
            self.nav.setFixedWidth(180)
            self.stack = QStackedWidget()
            self.first_run = FirstRunPage()
            self.node_page = NodePage()
            self.pages: dict[str, QWidget] = {}
            for item in vm.NAV:
                self.nav.addItem(item.label + ("" if not item.planned else f"（{item.planned}）"))
                if item.key == "node":
                    page = self.node_page
                else:
                    page = QLabel(vm.planned_text(item))
                    page.setWordWrap(True)
                    page.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
                self.pages[item.key] = page
                self.stack.addWidget(page)
            self.stack.addWidget(self.first_run)
            self.nav.currentRowChanged.connect(self._select)
            row.addWidget(self.nav)
            row.addWidget(self.stack, 1)
            self.setCentralWidget(central)
            self.status = QLabel("")
            self.statusBar().addWidget(self.status, 1)
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.refresh)
            self.timer.start(poll_ms)
            self.nav.setCurrentRow(0)
            self.refresh()

        def _select(self, index: int) -> None:
            if 0 <= index < len(vm.NAV) and controller.profile.initialized:
                self.stack.setCurrentWidget(self.pages[vm.NAV[index].key])

        def refresh(self) -> None:
            s = controller.snapshot()
            if not s.initialized:
                self.nav.setEnabled(False)
                self.stack.setCurrentWidget(self.first_run)
                self.first_run.create.setEnabled(not s.busy)
            else:
                if not self.nav.isEnabled() or self.stack.currentWidget() is self.first_run:
                    self.nav.setEnabled(True)
                    self._select(self.nav.currentRow())
                self.node_page.show_snapshot(s)
            text = s.busy + "…" if s.busy else ""
            if s.error:
                text = (text + "  " if text else "") + "出错：" + s.error
            self.status.setText(text)

        def closeEvent(self, event) -> None:  # noqa: N802 - Qt's name
            self.timer.stop()
            controller.shutdown()
            event.accept()

    return MainWindow()


def run_gui(root: Path, *, kdf: str = "moderate") -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    controller = StudioController(root, kdf=kdf)
    window = build_window(controller)
    window.show()
    try:
        return app.exec()
    finally:
        controller.shutdown()
