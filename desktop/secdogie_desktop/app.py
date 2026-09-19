"""The native window (tkinter). A thin view over ConsoleController + viewmodel.

tkinter is imported lazily inside FleetWindow so this module imports on a
headless host (where viewmodel is still unit-tested); constructing FleetWindow
needs a display. The palette mirrors agent/secdogie_agent/theme.py so the app
matches the rest of the operator UI.
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


def _font(size: int = 10, *, bold: bool = False) -> tuple:
    if sys.platform == "win32":
        family = "Segoe UI"
    elif sys.platform == "darwin":
        family = "Lucida Grande"
    else:
        family = "sans-serif"
    return (family, size, "bold") if bold else (family, size)


class FleetWindow:
    """A single window: live nodes/tasks tables + submit/stop/pause/resume."""

    def __init__(self, controller: Any, *, address=None, operator_identity=None, poll_ms: int = 1500):
        import tkinter as tk
        from tkinter import ttk

        self._tk = tk
        self.controller = controller
        self.operator_identity = operator_identity
        self.poll_ms = poll_ms

        self.root = tk.Tk()
        self.root.title("secdogie")
        self.root.configure(bg=BG)
        self.root.geometry("840x620")
        self.root.minsize(640, 480)
        self._build(ttk, address)

    def _build(self, ttk, address) -> None:
        tk = self._tk
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE,
                        foreground=FG, borderwidth=0, rowheight=24)
        style.configure("Treeview.Heading", background=SURFACE_2, foreground=MUTED, borderwidth=0)
        style.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", ACCENT_FG)])

        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=14, pady=(12, 6))
        tk.Label(header, text="secdogie", bg=BG, fg=FG, font=_font(14, bold=True)).pack(side="left")
        addr = f"coordinator {address[0]}:{address[1]}" if address else "coordinator"
        tk.Label(header, text=addr, bg=BG, fg=MUTED, font=_font(9)).pack(side="right")

        tk.Label(self.root, text="NODES", bg=BG, fg=MUTED, font=_font(9, bold=True)).pack(anchor="w", padx=14)
        self.nodes = ttk.Treeview(self.root, columns=("node", "label", "caps", "task"), show="headings", height=5)
        for col, width in (("node", 170), ("label", 150), ("caps", 190), ("task", 160)):
            self.nodes.heading(col, text=col)
            self.nodes.column(col, width=width, anchor="w")
        self.nodes.pack(fill="x", padx=14, pady=(2, 10))

        tk.Label(self.root, text="TASKS", bg=BG, fg=MUTED, font=_font(9, bold=True)).pack(anchor="w", padx=14)
        self.tasks = ttk.Treeview(self.root, columns=("id", "state", "task", "where"), show="headings", height=9)
        for col, width in (("id", 140), ("state", 90), ("task", 350), ("where", 120)):
            self.tasks.heading(col, text=col)
            self.tasks.column(col, width=width, anchor="w")
        self.tasks.pack(fill="both", expand=True, padx=14, pady=(2, 8))

        actions = tk.Frame(self.root, bg=BG)
        actions.pack(fill="x", padx=14)
        for text, op, color in (("Stop", "stop", DENY), ("Pause", "pause", WARN), ("Resume", "resume", OK)):
            tk.Button(actions, text=text, command=lambda o=op: self._action(o), bg=SURFACE_2, fg=color,
                      relief="flat", font=_font(9), padx=12, pady=3,
                      activebackground=BORDER, activeforeground=color).pack(side="left", padx=(0, 8), pady=4)

        row = tk.Frame(self.root, bg=BG)
        row.pack(fill="x", padx=14, pady=(6, 4))
        self.entry = tk.Entry(row, bg=SURFACE, fg=FG, insertbackground=FG, relief="flat", font=_font(10))
        self.entry.pack(side="left", fill="x", expand=True, ipady=5, padx=(0, 8))
        self.entry.bind("<Return>", lambda e: self._submit())
        self.auto = tk.BooleanVar(value=False)
        tk.Checkbutton(row, text="auto", variable=self.auto, bg=BG, fg=MUTED, selectcolor=SURFACE,
                       activebackground=BG, activeforeground=FG, font=_font(9)).pack(side="left", padx=(0, 8))
        tk.Button(row, text="Submit", command=self._submit, bg=ACCENT, fg=ACCENT_FG, relief="flat",
                  font=_font(10, bold=True), padx=14).pack(side="left")

        self.status = tk.Label(self.root, text="", bg=BG, fg=MUTED, font=_font(9), anchor="w")
        self.status.pack(fill="x", padx=14, pady=(4, 10))

    def _selected_task(self):
        sel = self.tasks.selection()
        return sel[0] if sel else None

    def _set_status(self, text: str, kind: str = "muted") -> None:
        self.status.config(text=text, fg={"ok": OK, "bad": DENY}.get(kind, MUTED))

    def _dispatch(self, body: dict) -> None:
        prepared = viewmodel.prepare_command(body, self.operator_identity)
        ok, _signer = self.controller.authorize(prepared)
        if not ok:
            self._set_status("unauthorized: an operator DID signature is required", "bad")
            return
        try:
            out = self.controller.command(prepared)
        except ValueError as e:
            self._set_status(str(e), "bad")
            return
        self._set_status(f"ok: {out.get('task_id') or out.get('op')}", "ok")
        self.refresh(schedule=False)

    def _submit(self) -> None:
        task = self.entry.get().strip()
        if not task:
            self._set_status("enter a task first", "bad")
            return
        self._dispatch({"op": "submit", "task": task, "options": {"auto": bool(self.auto.get())}})
        self.entry.delete(0, "end")

    def _action(self, op: str) -> None:
        tid = self._selected_task()
        if not tid:
            self._set_status(f"select a task to {op}", "bad")
            return
        self._dispatch({"op": op, "task_id": tid})

    def _render(self, snap: dict) -> None:
        self.nodes.delete(*self.nodes.get_children())
        for r in viewmodel.node_rows(snap):
            self.nodes.insert("", "end", values=r)
        self.tasks.delete(*self.tasks.get_children())
        for t in viewmodel.task_rows(snap):
            self.tasks.insert("", "end", iid=t["task_id"],
                              values=(t["task_id"], t["state"], t["task"], t["node_id"]))
        self._set_status(viewmodel.status_line(snap))

    def refresh(self, schedule: bool = True) -> None:
        try:
            snap = self.controller.state_snapshot()
            self._render(snap)
        except Exception as e:  # a snapshot failure must not kill the poll loop
            self._set_status(f"snapshot failed: {e}", "bad")
        if schedule:
            self.root.after(self.poll_ms, self.refresh)

    def run(self) -> None:
        self.refresh()
        self.root.mainloop()
