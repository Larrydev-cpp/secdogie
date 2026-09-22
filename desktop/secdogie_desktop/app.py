"""The native window (tkinter), as a chat.

A ChatGPT-style conversation with the fleet: the operator types a task in a
composer at the bottom ("you" on the right), and the fleet answers with what
happened to it -- accepted, running, done, failed -- as messages on the left,
coloured by tone. Stop / pause / resume act on the live task, since a chat has no
table to select from.

tkinter is imported lazily inside FleetWindow so this module imports on a
headless host (viewmodel's chat-diffing is unit-tested there); constructing
FleetWindow needs a display. The transcript logic is pure (viewmodel.diff_messages);
this file is the thin view. The palette stays dark to match the rest of the
operator UI.
"""
from __future__ import annotations

import sys
from typing import Any

from . import viewmodel

BG = "#0a0b0d"
SURFACE = "#121418"
SURFACE_2 = "#191c21"
FG = "#ecece8"
MUTED = "#8b8d92"
ACCENT = "#c5cbd4"
ACCENT_FG = "#0a0b0d"
DENY = "#c48982"
OK = "#8fa38c"
WARN = "#c4b49a"
BORDER = "#2a2d33"

# Per-message-kind text colour in the transcript.
_KIND_FG = {
    "task": ACCENT,      # the operator's own line
    "status": FG,
    "result": OK,
    "error": DENY,
    "node": WARN,
    "info": MUTED,
}


def _font(size: int = 10, *, bold: bool = False) -> tuple:
    if sys.platform == "win32":
        family = "Segoe UI"
    elif sys.platform == "darwin":
        family = "Lucida Grande"
    else:
        family = "sans-serif"
    return (family, size, "bold") if bold else (family, size)


class FleetWindow:
    """One window: a chat transcript with the fleet + a composer."""

    def __init__(self, controller: Any, *, address=None, operator_identity=None, poll_ms: int = 1500):
        import tkinter as tk

        self._tk = tk
        self.controller = controller
        self.operator_identity = operator_identity
        self.poll_ms = poll_ms
        self._prev: dict | None = None  # last snapshot, for diffing into chat lines

        self.root = tk.Tk()
        self.root.title("secdogie")
        self.root.configure(bg=BG)
        self.root.geometry("720x760")
        self.root.minsize(520, 560)
        self._build(address)

    # -- construction --------------------------------------------------------

    def _build(self, address) -> None:
        tk = self._tk

        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=16, pady=(12, 8))
        tk.Label(header, text="secdogie", bg=BG, fg=FG, font=_font(15, bold=True)).pack(side="left")
        self.header_status = tk.Label(header, text="connecting…", bg=BG, fg=MUTED, font=_font(9), anchor="e")
        self.header_status.pack(side="right")

        # The transcript: a read-only Text with per-role/kind tags, plus a scrollbar.
        wrap = tk.Frame(self.root, bg=BG)
        wrap.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        self.log = tk.Text(
            wrap, bg=BG, fg=FG, relief="flat", wrap="word", font=_font(11),
            padx=12, pady=8, spacing1=4, spacing3=6, highlightthickness=0, state="disabled", cursor="arrow",
        )
        scroll = tk.Scrollbar(wrap, command=self.log.yview, troughcolor=BG, bg=SURFACE_2, relief="flat", width=10)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)

        # role label (small, muted) + the message body per kind.
        self.log.tag_configure("op_label", foreground=MUTED, justify="right", font=_font(8), spacing1=8)
        self.log.tag_configure("op", foreground=ACCENT, justify="right", lmargin1=120, lmargin2=120, rmargin=4)
        self.log.tag_configure("sys_label", foreground=MUTED, justify="left", font=_font(8), spacing1=8)
        for kind, color in _KIND_FG.items():
            self.log.tag_configure(f"sys_{kind}", foreground=color, justify="left", lmargin1=4, lmargin2=4, rmargin=120)

        # Controls: act on the live task (running/queued/paused).
        controls = tk.Frame(self.root, bg=BG)
        controls.pack(fill="x", padx=16, pady=(0, 4))
        for text, op, color in (("Stop", "stop", DENY), ("Pause", "pause", WARN), ("Resume", "resume", OK)):
            tk.Button(
                controls, text=text, command=lambda o=op: self._action(o), bg=SURFACE_2, fg=color,
                relief="flat", font=_font(9), padx=12, pady=3, activebackground=BORDER, activeforeground=color,
            ).pack(side="left", padx=(0, 8))

        # Composer: a text entry + auto toggle + Send. Enter sends.
        composer = tk.Frame(self.root, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        composer.pack(fill="x", padx=12, pady=(2, 14))
        self.entry = tk.Entry(composer, bg=SURFACE, fg=FG, insertbackground=FG, relief="flat", font=_font(11))
        self.entry.pack(side="left", fill="x", expand=True, ipady=8, padx=(10, 8))
        self.entry.bind("<Return>", lambda e: self._submit())
        self.auto = tk.BooleanVar(value=False)
        tk.Checkbutton(
            composer, text="auto", variable=self.auto, bg=SURFACE, fg=MUTED, selectcolor=SURFACE_2,
            activebackground=SURFACE, activeforeground=FG, font=_font(9), highlightthickness=0,
        ).pack(side="left", padx=(0, 6))
        tk.Button(
            composer, text="Send", command=self._submit, bg=ACCENT, fg=ACCENT_FG, relief="flat",
            font=_font(10, bold=True), padx=16, pady=4, activebackground=FG,
        ).pack(side="left", padx=(0, 8), pady=6)

        addr = f"coordinator {address[0]}:{address[1]}" if address else "coordinator"
        self._append("system", f"connected · {addr}", "info")
        self.entry.focus_set()

    # -- transcript ----------------------------------------------------------

    def _append(self, role: str, text: str, kind: str) -> None:
        if not text:
            return
        self.log.configure(state="normal")
        if role == "operator":
            self.log.insert("end", "you\n", "op_label")
            self.log.insert("end", text + "\n", "op")
        else:
            self.log.insert("end", "secdogie\n", "sys_label")
            self.log.insert("end", text + "\n", f"sys_{kind if kind in _KIND_FG else 'info'}")
        self.log.configure(state="disabled")
        self.log.see("end")

    # -- actions -------------------------------------------------------------

    def _dispatch(self, body: dict) -> bool:
        prepared = viewmodel.prepare_command(body, self.operator_identity)
        ok, _signer = self.controller.authorize(prepared)
        if not ok:
            self._append("system", "unauthorized: an operator DID signature is required", "error")
            return False
        try:
            self.controller.command(prepared)
        except ValueError as e:
            self._append("system", str(e), "error")
            return False
        self.refresh(schedule=False)
        return True

    def _submit(self) -> None:
        task = self.entry.get().strip()
        if not task:
            return
        self._append("operator", task, "task")
        self.entry.delete(0, "end")
        self._dispatch({"op": "submit", "task": task, "options": {"auto": bool(self.auto.get())}})

    def _action(self, op: str) -> None:
        snap = self._prev or {}
        tid = viewmodel.active_task_id(snap)
        if not tid:
            self._append("system", f"没有进行中的任务可 {op}", "info")
            return
        self._dispatch({"op": op, "task_id": tid})

    # -- poll loop -----------------------------------------------------------

    def refresh(self, schedule: bool = True) -> None:
        try:
            snap = self.controller.state_snapshot()
            for m in viewmodel.diff_messages(self._prev, snap):
                self._append(m.role, m.text, m.kind)
            self._prev = snap
            self.header_status.config(text=viewmodel.status_line(snap), fg=MUTED)
        except Exception as e:  # a snapshot failure must not kill the poll loop
            self.header_status.config(text=f"snapshot failed: {e}", fg=DENY)
        if schedule:
            self.root.after(self.poll_ms, self.refresh)

    def run(self) -> None:
        self.refresh()
        self.root.mainloop()
