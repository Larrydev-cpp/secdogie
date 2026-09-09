"""Optional tkinter GUI dialogs: task entry, plan/briefing confirmation, and
the ask_user prompt.

GUI mode is opt-in (--gui). tkinter ships with the Python standard library,
but some minimal Linux Python builds omit it (install `python3-tk`) and it
needs a display, so every entry point imports it lazily and callers should
gate on `gui_available()` first. Functions raise `GuiUnavailableError` with a
clear, actionable message if tkinter can't be used, so callers can fall back
to the terminal.
"""
from __future__ import annotations

# One-click starters shown in the task dialog. CAD-first (commercial focus).
EXAMPLE_TASKS: tuple[tuple[str, str], ...] = (
    (
        "CAD: zoom fit",
        "Zoom the current drawing so the whole model fits in the view",
    ),
    (
        "CAD: read dimension",
        "Read the highlighted dimension (or selected edge length) and report the value — do not change the drawing",
    ),
    (
        "CAD: export PDF",
        "Export the current drawing to a PDF on the Desktop named secdogie-export.pdf",
    ),
    (
        "Preview: Notepad",
        "Open Notepad and type: Hello from secdogie",
    ),
)


from . import theme as ui


class GuiUnavailableError(RuntimeError):
    """tkinter is missing, or there is no display to show a window on."""


def gui_available() -> bool:
    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        root.destroy()
        return True
    except Exception:
        return False


def _import_tk():
    try:
        import tkinter as tk
        from tkinter import messagebox, scrolledtext

        return tk, scrolledtext, messagebox
    except Exception as e:  # pragma: no cover
        raise GuiUnavailableError(
            "GUI mode needs tkinter, which isn't available here. On Linux "
            "install it (e.g. `sudo apt install python3-tk`); on Windows/macOS "
            "use a standard python.org build. Or drop --gui to use the terminal."
        ) from e


def _new_root(tk):
    root = tk.Tk()
    root.title("secdogie-agent")
    root.configure(bg=ui.BG)
    root.attributes("-topmost", True)
    root.lift()
    return root


def _frame(tk, parent):
    return tk.Frame(parent, bg=ui.BG)


def _label(tk, parent, text, *, bold=False, muted=False, wrap=0, size=10):
    return tk.Label(
        parent,
        text=text,
        bg=ui.BG,
        fg=ui.MUTED if muted else ui.FG,
        font=ui.font(size, bold=bold),
        wraplength=wrap,
        justify="left",
        anchor="w",
    )


def _btn(tk, parent, text, command, *, primary=False, width=12):
    return tk.Button(
        parent,
        text=text,
        command=command,
        width=width,
        bg=ui.ACCENT if primary else ui.SURFACE_2,
        fg=ui.ACCENT_FG if primary else ui.FG,
        activebackground=ui.ACCENT if primary else ui.SURFACE,
        activeforeground=ui.ACCENT_FG if primary else ui.FG,
        relief="flat",
        font=ui.font(10, bold=primary),
        highlightthickness=0,
        bd=0,
        cursor="hand2",
        pady=4,
    )


def ask_task(default: str = "") -> str | None:
    tk, scrolledtext, _ = _import_tk()
    root = _new_root(tk)
    result: dict[str, str | None] = {"task": None}

    pad = _frame(tk, root)
    pad.pack(padx=16, pady=14, fill="both", expand=True)

    _label(tk, pad, "What should it do?", bold=True, size=13).pack(anchor="w")
    _label(
        tk,
        pad,
        "Describe the task. Start opens a console that stays on screen — STOP anytime. High-risk steps still ask.",
        muted=True,
        wrap=480,
    ).pack(anchor="w", pady=(4, 8))

    _label(tk, pad, "Try an example:", muted=True, size=9).pack(anchor="w")
    chips = _frame(tk, pad)
    chips.pack(anchor="w", pady=(2, 10))

    entry = scrolledtext.ScrolledText(
        pad,
        width=58,
        height=6,
        wrap="word",
        bg=ui.SURFACE,
        fg=ui.FG,
        insertbackground=ui.FG,
        relief="flat",
        font=ui.font(10),
        highlightthickness=1,
        highlightbackground=ui.BORDER,
        highlightcolor=ui.BORDER,
        bd=0,
    )
    entry.insert("1.0", default)
    entry.pack(fill="both", expand=True, pady=(0, 10))
    entry.focus_set()

    def use_example(text: str) -> None:
        entry.delete("1.0", "end")
        entry.insert("1.0", text)
        entry.focus_set()

    for label, full in EXAMPLE_TASKS:
        btn = tk.Button(
            chips,
            text=label,
            command=lambda t=full: use_example(t),
            padx=8,
            pady=2,
            bg=ui.SURFACE_2,
            fg=ui.FG,
            activebackground=ui.SURFACE,
            activeforeground=ui.FG,
            relief="flat",
            highlightthickness=0,
            bd=0,
            cursor="hand2",
            font=ui.font(9),
        )
        btn.pack(side="left", padx=(0, 6))

    def submit() -> None:
        result["task"] = entry.get("1.0", "end").strip()
        root.destroy()

    def cancel() -> None:
        result["task"] = None
        root.destroy()

    buttons = _frame(tk, pad)
    buttons.pack(anchor="e")
    _btn(tk, buttons, "Cancel", cancel, width=10).pack(side="right", padx=(6, 0))
    _btn(tk, buttons, "Start", submit, primary=True, width=10).pack(side="right")

    root.protocol("WM_DELETE_WINDOW", cancel)
    root.bind("<Escape>", lambda _e: cancel())
    root.bind("<Control-Return>", lambda _e: submit())
    root.mainloop()

    task = result["task"]
    return task or None


def confirm_plan(task: str, plan: str) -> bool:
    tk, scrolledtext, _ = _import_tk()
    root = _new_root(tk)
    result = {"ok": False}

    pad = _frame(tk, root)
    pad.pack(padx=16, pady=14, fill="both", expand=True)

    _label(tk, pad, "Ready to start?", bold=True, size=13).pack(anchor="w")
    _label(
        tk,
        pad,
        "Nothing has been clicked yet. Approve, then the operator console stays up while it runs.",
        muted=True,
        wrap=520,
    ).pack(anchor="w", pady=(2, 10))

    _label(tk, pad, "Your task", bold=True).pack(anchor="w")
    _label(tk, pad, task, wrap=520).pack(anchor="w", pady=(0, 8))

    _label(tk, pad, "Its plan", bold=True).pack(anchor="w")
    box = scrolledtext.ScrolledText(
        pad,
        width=68,
        height=12,
        wrap="word",
        bg=ui.SURFACE,
        fg=ui.FG,
        relief="flat",
        font=ui.font(10),
        highlightthickness=1,
        highlightbackground=ui.BORDER,
        highlightcolor=ui.BORDER,
        bd=0,
    )
    box.insert("1.0", plan)
    box.configure(state="disabled")
    box.pack(fill="both", expand=True, pady=(0, 10))

    def proceed() -> None:
        result["ok"] = True
        root.destroy()

    def cancel() -> None:
        result["ok"] = False
        root.destroy()

    buttons = _frame(tk, pad)
    buttons.pack(anchor="e")
    _btn(tk, buttons, "Cancel", cancel, width=12).pack(side="right", padx=(6, 0))
    _btn(tk, buttons, "Looks good — go", proceed, primary=True, width=16).pack(side="right")

    root.protocol("WM_DELETE_WINDOW", cancel)
    root.bind("<Escape>", lambda _e: cancel())
    root.mainloop()

    return result["ok"]


class BusyHandle:
    """Status window the caller must `.close()`. Never raises on close."""

    def __init__(self, root=None):
        self._root = root

    def close(self) -> None:
        root = self._root
        self._root = None
        if root is None:
            return
        try:
            root.destroy()
        except Exception:
            pass


def working(message: str) -> BusyHandle:
    """A small 'Calling the model…' window so Start isn't a blank desktop.

    Best-effort: if tkinter/display isn't there, returns a no-op handle.
    The HTTP call runs on this thread, so we `update()` once up front to
    paint, then the caller blocks until close().
    """
    try:
        tk, _, _ = _import_tk()
        root = _new_root(tk)
        pad = _frame(tk, root)
        pad.pack(padx=22, pady=16)
        _label(tk, pad, "Working…", bold=True, size=13).pack(anchor="w")
        _label(tk, pad, message, muted=True, wrap=440).pack(anchor="w", pady=(4, 0))
        root.update_idletasks()
        root.update()
        return BusyHandle(root)
    except Exception:
        return BusyHandle(None)


def confirm_action(prompt: str, *, high_risk: bool = False) -> bool:
    """Yes/No popup for a single proposed action. Fail-closed on any error.

    Used by `--gui` instead of `safety.confirm`'s stdin prompt — a windowed
    exe has no console, so `input()` either hangs forever or hits EOF and
    skips every action with no window. That was 'I typed a command and
    nothing happened'.
    """
    try:
        tk, _, messagebox = _import_tk()
        root = _new_root(tk)
        root.withdraw()
        title = "secdogie-agent — HIGH-RISK" if high_risk else "secdogie-agent — execute this?"
        answer = messagebox.askyesno(title, prompt, parent=root)
        root.destroy()
        return bool(answer)
    except Exception:
        return False


def ask_user(question: str) -> bool:
    tk, _, messagebox = _import_tk()
    root = _new_root(tk)
    root.withdraw()
    answer = messagebox.askyesno("secdogie-agent is asking", question, parent=root)
    root.destroy()
    return bool(answer)


def notify(title: str, message: str, *, error: bool = False) -> bool:
    try:
        tk, _, messagebox = _import_tk()
        root = _new_root(tk)
        root.withdraw()
        (messagebox.showerror if error else messagebox.showinfo)(title, message, parent=root)
        root.destroy()
        return True
    except Exception:
        return False
