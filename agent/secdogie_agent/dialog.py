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


class GuiUnavailableError(RuntimeError):
    """tkinter is missing, or there is no display to show a window on."""


def gui_available() -> bool:
    """True if we can actually open a window right now (tkinter importable AND
    a usable display). Creates and tears down a hidden root to verify both."""
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
    except Exception as e:  # pragma: no cover - environment dependent
        raise GuiUnavailableError(
            "GUI mode needs tkinter, which isn't available here. On Linux "
            "install it (e.g. `sudo apt install python3-tk`); on Windows/macOS "
            "use a standard python.org build. Or drop --gui to use the terminal."
        ) from e


def _new_root(tk):
    root = tk.Tk()
    root.title("secdogie-agent")
    root.attributes("-topmost", True)  # surface above the app being controlled
    root.lift()
    return root


def ask_task(default: str = "") -> str | None:
    """Pop up a window asking what the agent should do. Returns the entered
    task, or None if the user cancelled/closed the window."""
    tk, scrolledtext, _ = _import_tk()
    root = _new_root(tk)
    result: dict[str, str | None] = {"task": None}

    tk.Label(root, text="What should secdogie-agent do?", font=("", 12, "bold")).pack(
        padx=16, pady=(14, 6), anchor="w"
    )
    tk.Label(
        root,
        text="Describe the task in plain language. The model will show its plan before acting.",
        wraplength=440,
        justify="left",
        fg="#555",
    ).pack(padx=16, anchor="w")

    entry = scrolledtext.ScrolledText(root, width=56, height=6, wrap="word")
    entry.insert("1.0", default)
    entry.pack(padx=16, pady=10)
    entry.focus_set()

    def submit() -> None:
        result["task"] = entry.get("1.0", "end").strip()
        root.destroy()

    def cancel() -> None:
        result["task"] = None
        root.destroy()

    buttons = tk.Frame(root)
    buttons.pack(padx=16, pady=(0, 14), anchor="e")
    tk.Button(buttons, text="Cancel", command=cancel, width=10).pack(side="right", padx=(6, 0))
    tk.Button(buttons, text="Start", command=submit, width=10, default="active").pack(side="right")

    root.protocol("WM_DELETE_WINDOW", cancel)
    root.bind("<Escape>", lambda _e: cancel())
    root.mainloop()

    task = result["task"]
    return task or None


def confirm_plan(task: str, plan: str) -> bool:
    """Show the model's restated task + plan and ask the user to proceed.
    Returns True to proceed, False to cancel."""
    tk, scrolledtext, _ = _import_tk()
    root = _new_root(tk)
    result = {"ok": False}

    tk.Label(root, text="Task", font=("", 11, "bold")).pack(padx=16, pady=(14, 2), anchor="w")
    tk.Label(root, text=task, wraplength=520, justify="left").pack(padx=16, anchor="w")

    tk.Label(root, text="The model's plan", font=("", 11, "bold")).pack(
        padx=16, pady=(12, 2), anchor="w"
    )
    box = scrolledtext.ScrolledText(root, width=68, height=14, wrap="word")
    box.insert("1.0", plan)
    box.configure(state="disabled")
    box.pack(padx=16, pady=(0, 10))

    def proceed() -> None:
        result["ok"] = True
        root.destroy()

    def cancel() -> None:
        result["ok"] = False
        root.destroy()

    buttons = tk.Frame(root)
    buttons.pack(padx=16, pady=(0, 14), anchor="e")
    tk.Button(buttons, text="Cancel", command=cancel, width=12).pack(side="right", padx=(6, 0))
    tk.Button(buttons, text="Proceed", command=proceed, width=12, default="active").pack(side="right")

    root.protocol("WM_DELETE_WINDOW", cancel)
    root.bind("<Escape>", lambda _e: cancel())
    root.mainloop()

    return result["ok"]


def ask_user(question: str) -> bool:
    """Yes/No dialog for the model's ask_user step. Returns True to continue."""
    tk, _, messagebox = _import_tk()
    root = _new_root(tk)
    root.withdraw()
    answer = messagebox.askyesno("secdogie-agent — the model is asking", question, parent=root)
    root.destroy()
    return bool(answer)
