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

# One-click starters shown in the task dialog. Keep them short, safe, and
# obviously reversible so a first-time user can try without fear.
EXAMPLE_TASKS: tuple[tuple[str, str], ...] = (
    (
        "Open Notepad",
        "Open Notepad and type: Hello from secdogie",
    ),
    (
        "Desktop folder",
        "Create a new folder on the desktop named secdogie-demo",
    ),
    (
        "Screenshot tip",
        "Open the default browser and go to example.com",
    ),
)


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
    task, or None if the user cancelled/closed the window.

    Includes one-click example tasks so a first-time user can try something
    safe without inventing a prompt.
    """
    tk, scrolledtext, _ = _import_tk()
    root = _new_root(tk)
    result: dict[str, str | None] = {"task": None}

    pad = tk.Frame(root)
    pad.pack(padx=16, pady=14, fill="both", expand=True)

    tk.Label(pad, text="What should it do?", font=("", 13, "bold")).pack(anchor="w")
    tk.Label(
        pad,
        text="Describe the task in plain language. It will show a plan first and ask before each step.",
        wraplength=480,
        justify="left",
        fg="#555",
    ).pack(anchor="w", pady=(4, 8))

    # Example chips
    tk.Label(pad, text="Try an example:", font=("", 9), fg="#666").pack(anchor="w")
    chips = tk.Frame(pad)
    chips.pack(anchor="w", pady=(2, 10))

    entry = scrolledtext.ScrolledText(pad, width=58, height=6, wrap="word")
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
        )
        btn.pack(side="left", padx=(0, 6))

    def submit() -> None:
        result["task"] = entry.get("1.0", "end").strip()
        root.destroy()

    def cancel() -> None:
        result["task"] = None
        root.destroy()

    buttons = tk.Frame(pad)
    buttons.pack(anchor="e")
    tk.Button(buttons, text="Cancel", command=cancel, width=10).pack(side="right", padx=(6, 0))
    tk.Button(buttons, text="Start", command=submit, width=10, default="active").pack(side="right")

    root.protocol("WM_DELETE_WINDOW", cancel)
    root.bind("<Escape>", lambda _e: cancel())
    # Ctrl+Enter to submit (plain Enter adds a newline in the text box)
    root.bind("<Control-Return>", lambda _e: submit())
    root.mainloop()

    task = result["task"]
    return task or None


def confirm_plan(task: str, plan: str) -> bool:
    """Show the model's restated task + plan and ask the user to proceed.
    Returns True to proceed, False to cancel."""
    tk, scrolledtext, _ = _import_tk()
    root = _new_root(tk)
    result = {"ok": False}

    pad = tk.Frame(root)
    pad.pack(padx=16, pady=14, fill="both", expand=True)

    tk.Label(pad, text="Ready to start?", font=("", 13, "bold")).pack(anchor="w")
    tk.Label(
        pad,
        text="Review what it understood. Nothing has been clicked yet.",
        fg="#555",
        wraplength=520,
        justify="left",
    ).pack(anchor="w", pady=(2, 10))

    tk.Label(pad, text="Your task", font=("", 10, "bold")).pack(anchor="w")
    tk.Label(pad, text=task, wraplength=520, justify="left").pack(anchor="w", pady=(0, 8))

    tk.Label(pad, text="Its plan", font=("", 10, "bold")).pack(anchor="w")
    box = scrolledtext.ScrolledText(pad, width=68, height=12, wrap="word")
    box.insert("1.0", plan)
    box.configure(state="disabled")
    box.pack(fill="both", expand=True, pady=(0, 10))

    def proceed() -> None:
        result["ok"] = True
        root.destroy()

    def cancel() -> None:
        result["ok"] = False
        root.destroy()

    buttons = tk.Frame(pad)
    buttons.pack(anchor="e")
    tk.Button(buttons, text="Cancel", command=cancel, width=12).pack(side="right", padx=(6, 0))
    tk.Button(buttons, text="Looks good — go", command=proceed, width=16, default="active").pack(
        side="right"
    )

    root.protocol("WM_DELETE_WINDOW", cancel)
    root.bind("<Escape>", lambda _e: cancel())
    root.mainloop()

    return result["ok"]


def ask_user(question: str) -> bool:
    """Yes/No dialog for the model's ask_user step. Returns True to continue."""
    tk, _, messagebox = _import_tk()
    root = _new_root(tk)
    root.withdraw()
    answer = messagebox.askyesno("secdogie-agent is asking", question, parent=root)
    root.destroy()
    return bool(answer)


def notify(title: str, message: str, *, error: bool = False) -> bool:
    """Best-effort popup for a message the user must see when there's no console
    to print it to (the windowed exe). Returns True if a dialog was actually
    shown, False if no GUI was available -- so the caller can rely on its own
    print() having gone somewhere too. Never raises."""
    try:
        tk, _, messagebox = _import_tk()
        root = _new_root(tk)
        root.withdraw()
        (messagebox.showerror if error else messagebox.showinfo)(title, message, parent=root)
        root.destroy()
        return True
    except Exception:
        return False
