"""tkinter GUI: detect open windows, split the screen into them, and drive
one secdogie-agent instance per selected window at once.

This is step one of driving several windows concurrently: every window's
agent shares the one API key secdogie_agent.config resolves today (env var
/ config file / --model), same as the single-window CLI. Spreading that
across a pool of keys, with a coordinator dispatching tasks to each, is
later work this is meant to make room for -- each window already gets its
own VisionProvider instance, the seam a future key pool would hand keys out
from (see runner.py).
"""
from __future__ import annotations

import io
import queue

from secdogie_agent import config as config_mod
from secdogie_agent import dialog, screen
from secdogie_agent.providers import make_provider

from . import runner, windows

# Populated lazily by main(), after dialog.gui_available() has already
# confirmed tkinter imports cleanly -- some minimal Python builds omit
# tkinter entirely (see secdogie_agent.dialog), so this module must stay
# importable (windows.py/runner.py are useful without a GUI) even then.
tk = None
ttk = None
messagebox = None

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_MAX_STEPS = 50
_THUMB_EDGE = 160

_AUTO_HELP = (
    "Off = dry run: the agent reasons about every selected window and logs what it "
    "would do, but never touches the mouse/keyboard. On = real clicks/typing against "
    "every selected window at once, with no per-step confirmation (confirmation prompts "
    "only make sense for one window at a time). Only turn this on against windows/"
    "machines you fully control and can reach immediately to stop."
)


class _Row:
    """One detected window's picker widgets + its current run, if any."""

    def __init__(self, parent: tk.Widget, win: windows.WindowInfo):
        self.window = win
        self.run: runner.WindowRun | None = None
        self._photo = None  # keep a reference alive -- tkinter drops GC'd images

        self.var = tk.BooleanVar(value=False)
        self.frame = ttk.Frame(parent, padding=6, relief="groove")
        ttk.Checkbutton(self.frame, variable=self.var).grid(row=0, column=0, rowspan=2, sticky="n")

        self.thumb_label = ttk.Label(self.frame)
        self.thumb_label.grid(row=0, column=1, rowspan=2, padx=6)

        title = win.title if len(win.title) <= 60 else win.title[:57] + "..."
        ttk.Label(self.frame, text=title, font=("", 10, "bold")).grid(row=0, column=2, sticky="w")
        ttk.Label(
            self.frame, text=f"{win.width}x{win.height} @ ({win.left}, {win.top})", foreground="#666"
        ).grid(row=1, column=2, sticky="w")

        self.status_var = tk.StringVar(value="idle")
        ttk.Label(self.frame, textvariable=self.status_var, width=28).grid(row=0, column=3, rowspan=2, padx=10)

    def set_thumbnail(self, photo) -> None:
        self._photo = photo
        self.thumb_label.configure(image=photo)

    def set_status(self, text: str) -> None:
        self.status_var.set(text)


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("secdogie-open: split the screen, control multiple windows")
        root.geometry("760x600")

        self.rows: dict[str, _Row] = {}
        self.status_queue: "queue.Queue[tuple[str, str, str]]" = queue.Queue()

        self._build_controls()
        self._build_list()
        self.refresh_windows()
        self.root.after(200, self._poll_status)

    # -- layout ---------------------------------------------------------
    def _build_controls(self) -> None:
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")
        top.columnconfigure(0, weight=1)

        ttk.Label(top, text="Task (applied to every selected window):").grid(row=0, column=0, columnspan=4, sticky="w")
        self.task_entry = tk.Text(top, height=3, width=60)
        self.task_entry.grid(row=1, column=0, columnspan=4, sticky="we", pady=(2, 6))

        ttk.Label(top, text="Model:").grid(row=2, column=0, sticky="w")
        self.model_var = tk.StringVar(value=DEFAULT_MODEL)
        ttk.Entry(top, textvariable=self.model_var, width=24).grid(row=2, column=1, sticky="w")

        ttk.Label(top, text="Max steps:").grid(row=2, column=2, sticky="e")
        self.max_steps_var = tk.IntVar(value=DEFAULT_MAX_STEPS)
        ttk.Spinbox(top, from_=1, to=100000, textvariable=self.max_steps_var, width=8).grid(row=2, column=3, sticky="w")

        self.auto_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            top, text="Enable real actions (auto-execute)", variable=self.auto_var
        ).grid(row=3, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Label(top, text=_AUTO_HELP, foreground="#666", wraplength=720, justify="left").grid(
            row=4, column=0, columnspan=4, sticky="w"
        )

        btns = ttk.Frame(top)
        btns.grid(row=5, column=0, columnspan=4, sticky="w", pady=(10, 0))
        ttk.Button(btns, text="Refresh windows", command=self.refresh_windows).pack(side="left")
        ttk.Button(btns, text="Start selected", command=self.start_selected).pack(side="left", padx=6)
        ttk.Button(btns, text="Stop all", command=self.stop_all).pack(side="left")

    def _build_list(self) -> None:
        container = ttk.Frame(self.root)
        container.pack(fill="both", expand=True, padx=8, pady=8)

        canvas = tk.Canvas(container, borderwidth=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        self.list_frame = ttk.Frame(canvas)
        self.list_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    # -- window list ---------------------------------------------------------
    def refresh_windows(self) -> None:
        for row in self.rows.values():
            row.frame.destroy()
        self.rows.clear()

        try:
            found = windows.list_windows()
        except windows.NoWindowBackendError as e:
            messagebox.showerror("secdogie-open", str(e))
            return

        for win in found:
            row = _Row(self.list_frame, win)
            row.frame.pack(fill="x", pady=3)
            self.rows[win.id] = row
            self._load_thumbnail(row)

    def _load_thumbnail(self, row: _Row) -> None:
        try:
            from PIL import Image, ImageTk

            png, _size = screen.capture_screenshot(region=row.window.region)
            img = Image.open(io.BytesIO(png))
            img.thumbnail((_THUMB_EDGE, _THUMB_EDGE))
            row.set_thumbnail(ImageTk.PhotoImage(img))
        except Exception:
            pass  # a thumbnail is a nicety -- a picker row without one is still usable

    # -- running ---------------------------------------------------------
    def start_selected(self) -> None:
        task = self.task_entry.get("1.0", "end").strip()
        if not task:
            messagebox.showwarning("secdogie-open", "Enter a task first.")
            return
        selected = [row for row in self.rows.values() if row.var.get()]
        if not selected:
            messagebox.showwarning("secdogie-open", "Select at least one window.")
            return

        resolved = config_mod.resolve(cli_model=self.model_var.get() or None)
        if not resolved.api_key:
            messagebox.showerror(
                "secdogie-open",
                f"No API key found for the {resolved.provider} provider. Set "
                f"{resolved.env_var} or fill in a secdogie-agent config file, then retry.",
            )
            return

        auto = self.auto_var.get()
        if auto and not messagebox.askyesno("secdogie-open", _AUTO_HELP + "\n\nStart anyway?"):
            return

        max_steps = self.max_steps_var.get()
        for row in selected:
            if row.run is not None and row.run.is_alive():
                continue  # already running against this window
            row.set_status("running: starting")

            def provider_factory(r=resolved):
                return make_provider(r.provider, r.model, r.api_key)

            row.run = runner.launch(
                row.window,
                provider_factory,
                task,
                auto=auto,
                dry_run=not auto,
                max_steps=max_steps,
                status_queue=self.status_queue,
            )

    def stop_all(self) -> None:
        for row in self.rows.values():
            if row.run is not None and row.run.is_alive():
                row.run.stop()
                row.set_status("stopping...")

    # -- status polling ---------------------------------------------------------
    def _poll_status(self) -> None:
        try:
            while True:
                window_id, status, detail = self.status_queue.get_nowait()
                row = self.rows.get(window_id)
                if row is not None:
                    row.set_status(f"{status}: {detail}" if detail else status)
        except queue.Empty:
            pass
        self.root.after(200, self._poll_status)


def main() -> int:
    if not dialog.gui_available():
        print("secdogie-open needs a GUI (tkinter, plus a display) -- neither the terminal-only "
              "secdogie-agent CLI's fallback nor a headless session can drive multiple windows at once.")
        return 1

    global tk, ttk, messagebox
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0
