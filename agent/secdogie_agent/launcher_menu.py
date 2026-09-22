"""Built-in launcher menu: double-click the packaged exe -> a frosted-glass
chooser, no extra files, no terminal knowledge.

A frozen single-file build launched with NO arguments (i.e. double-clicked)
shows this window first and turns the clicked card into the CLI arguments it
stands for -- so `secdogie-agent.exe` alone is the whole install: one file,
open it, pick what to do. Launched *with* arguments (a terminal user, the
macro/skill flags, scripts), the menu never appears and the CLI is untouched.

First-run: if no API key is configured yet, the key dialog is shown before the
menu so the user never hits a silent failure after picking a task.

The glass: tkinter draws the panel, and on Windows the real acrylic blur comes
from the OS compositor -- the same SetWindowCompositionAttribute call native
apps use, applied to tkinter's HWND -- plus DWM rounded corners on Windows 11.
Both are best-effort: anywhere they can't apply (older Windows, other OSes)
the window still shows as a clean dark panel. The menu itself launches
nothing; it only *returns* the chosen argv for cli.main to run, which keeps
the choice->args mapping a pure, headless-testable table.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

from . import theme as ui

# -- the choices (pure data: provable without a display) -----------------------


@dataclass(frozen=True)
class MenuChoice:
    key: str
    title: str
    blurb: str
    args: tuple[str, ...]  # the secdogie-agent argv this card stands for
    # Special: "config" is handled by the GUI key dialog, not by returning args.


MENU_CHOICES: tuple[MenuChoice, ...] = (
    MenuChoice(
        "task",
        "Do a task",
        "Type what you want. A console stays on screen with STOP. High-risk steps still ask.",
        ("--gui", "--desktop-ax"),
    ),
    MenuChoice(
        "dry",
        "Preview only (safe)",
        "See what it would do — nothing is clicked or typed on your machine.",
        ("--gui", "--desktop-ax", "--dry-run"),
    ),
    MenuChoice(
        "ax",
        "Smarter clicks (recommended)",
        "Uses accessibility labels when possible — steadier on real apps.",
        ("--gui", "--desktop-ax"),
    ),
    MenuChoice(
        "step",
        "Ask every step",
        "Yes/No popup before each click. Slower, same as the old careful path.",
        ("--gui", "--desktop-ax", "--confirm-each"),
    ),
    MenuChoice(
        "auto",
        "Run without asking (careful)",
        "No plan dialog, no per-step confirm. High-risk actions still ask.",
        ("--gui", "--desktop-ax", "--auto"),
    ),
    MenuChoice(
        "config",
        "Set up / edit API key",
        "Paste any provider key (Anthropic, OpenAI, OpenRouter, DeepSeek, Groq, custom…).",
        (),  # handled specially by show_key_dialog
    ),
)


def args_for(key: str) -> list[str] | None:
    """The argv for a choice key, or None for an unknown key."""
    for c in MENU_CHOICES:
        if c.key == key:
            return list(c.args)
    return None


def should_offer(argv: list[str]) -> bool:
    """Show the menu only where it belongs: a frozen (packaged) build launched
    with no arguments at all -- i.e. a double-click. Any explicit argument means
    a deliberate invocation (terminal, script, the .bat passing flags), and the
    CLI must behave exactly as documented, menu-free. Running from source keeps
    the plain CLI too (developers have a terminal by definition)."""
    return not argv and bool(getattr(sys, "frozen", False))


# -- the window (on-machine: needs tkinter + a display) ------------------------

# Palette lives in theme.py so HUD / dialog / launcher are one language.


def _apply_windows_glass(root) -> None:
    ui.apply_glass(root)


def show_key_dialog(*, first_run: bool = False) -> bool:
    """A proper dialog for pasting an API key from any vendor.

    Supports Anthropic, OpenAI, and any OpenAI-compatible / custom key name.
    Writes next to the exe (or the portable location) with clear feedback.

    Returns True if a key was saved, False if the user cancelled / closed.
    """
    try:
        import tkinter as tk

        from . import config as config_mod

        root = tk.Tk()
        root.title("secdogie-agent — API key")
        root.configure(bg=ui.BG)
        root.resizable(False, False)
        root.attributes("-topmost", True)

        saved = {"ok": False}

        pad = tk.Frame(root, bg=ui.BG)
        pad.pack(padx=24, pady=20, fill="both", expand=True)

        title = "One quick setup" if first_run else "Paste your API key"
        tk.Label(
            pad, text=title,
            bg=ui.BG, fg=ui.FG, font=ui.font(17, bold=True),
        ).pack(anchor="w")

        intro = (
            "Before it can control your screen, paste an API key from any vision model provider.\n"
            "Key stays on disk next to the program — nothing is uploaded."
            if first_run
            else "Any provider: Anthropic, OpenAI, DeepSeek, Groq, local models…\n"
                 "Key stays on disk next to the program — nothing is uploaded."
        )
        tk.Label(
            pad,
            text=intro,
            bg=ui.BG, fg=ui.MUTED, font=ui.font(12), justify="left",
        ).pack(anchor="w", pady=(4, 12))

        # Provider / key-name choice
        kind_var = tk.StringVar(value="anthropic")
        kind_row = tk.Frame(pad, bg=ui.BG)
        kind_row.pack(fill="x", pady=(0, 6))

        for label, value in (
            ("Anthropic", "anthropic"),
            ("OpenAI", "openai"),
            ("OpenRouter", "openrouter"),
            ("Custom env name", "custom"),
        ):
            tk.Radiobutton(
                kind_row, text=label, variable=kind_var, value=value,
                bg=ui.BG, fg=ui.FG, selectcolor=ui.SURFACE, activebackground=ui.BG,
                activeforeground=ui.FG, font=ui.font(12),
            ).pack(side="left", padx=(0, 12))

        # Custom env var name (shown only when Custom is selected)
        custom_row = tk.Frame(pad, bg=ui.BG)
        custom_row.pack(fill="x", pady=(0, 8))
        tk.Label(
            custom_row, text="Env var:", bg=ui.BG, fg=ui.MUTED, font=ui.font(12),
        ).pack(side="left")
        custom_env_var = tk.StringVar(value="OPENAI_API_KEY")
        custom_entry = tk.Entry(
            custom_row, textvariable=custom_env_var, width=28,
            font=ui.font(12, mono=True), bg=ui.SURFACE, fg=ui.FG, insertbackground=ui.FG,
            relief="flat", highlightthickness=1, highlightcolor=ui.ACCENT,
            highlightbackground=ui.BORDER,
        )
        custom_entry.pack(side="left", padx=(8, 0), ipady=4)

        def _sync_custom_visibility(*_):
            # Always leave the row visible; grey out when not custom so layout
            # doesn't jump, but the value is only used when kind==custom.
            state = "normal" if kind_var.get() == "custom" else "disabled"
            custom_entry.config(state=state)

        kind_var.trace_add("write", _sync_custom_visibility)
        _sync_custom_visibility()

        # Key entry
        tk.Label(
            pad, text="API key", bg=ui.BG, fg=ui.MUTED, font=ui.font(12),
        ).pack(anchor="w")
        key_var = tk.StringVar()
        entry = tk.Entry(
            pad, textvariable=key_var, width=52, show="\u2022",
            font=ui.font(13, mono=True), bg=ui.SURFACE, fg=ui.FG, insertbackground=ui.FG,
            relief="flat", highlightthickness=1, highlightcolor=ui.ACCENT,
            highlightbackground=ui.BORDER,
        )
        entry.pack(fill="x", ipady=8, pady=(2, 6))
        entry.focus_set()

        # Optional default model
        tk.Label(
            pad, text="Default model (optional)", bg=ui.BG, fg=ui.MUTED, font=ui.font(12),
        ).pack(anchor="w")
        model_var = tk.StringVar()
        model_entry = tk.Entry(
            pad, textvariable=model_var, width=52,
            font=ui.font(12, mono=True), bg=ui.SURFACE, fg=ui.FG, insertbackground=ui.FG,
            relief="flat", highlightthickness=1, highlightcolor=ui.ACCENT,
            highlightbackground=ui.BORDER,
        )
        model_entry.pack(fill="x", ipady=6, pady=(2, 4))
        tk.Label(
            pad,
            text="e.g. claude-sonnet-5 \u00b7 gpt-5.5 \u00b7 openrouter/anthropic/claude-sonnet-4 \u00b7 sk-or- keys auto-detect",
            bg=ui.BG, fg=ui.MUTED, font=ui.font(11),
        ).pack(anchor="w", pady=(0, 8))

        # Show/hide toggle
        show_var = tk.BooleanVar(value=False)

        def toggle_show():
            entry.config(show="" if show_var.get() else "\u2022")

        tk.Checkbutton(
            pad, text="Show key", variable=show_var, command=toggle_show,
            bg=ui.BG, fg=ui.MUTED, selectcolor=ui.SURFACE, activebackground=ui.BG,
            activeforeground=ui.MUTED, font=ui.font(12),
        ).pack(anchor="w", pady=(0, 10))

        status = tk.Label(pad, text="", bg=ui.BG, fg=ui.MUTED, font=ui.font(12), justify="left")
        status.pack(anchor="w", pady=(0, 10))

        def save():
            key = key_var.get().strip()
            if not key:
                status.config(text="Please paste a key first.", fg="#e07070")
                return
            if len(key) < 8:
                status.config(text="That looks too short for an API key.", fg="#e07070")
                return

            kind = kind_var.get()
            if key.startswith("sk-or-"):
                kind = "openrouter"
            if kind == "custom":
                env_name = custom_env_var.get().strip()
                if not env_name:
                    status.config(text="Custom env var name is empty.", fg="#e07070")
                    return
                kwargs = {"env_var": env_name}
            elif kind == "openai":
                kwargs = {"provider": "openai"}
            elif kind == "openrouter":
                kwargs = {"provider": "openrouter"}
            else:
                kwargs = {"provider": "anthropic"}

            model = model_var.get().strip() or None
            try:
                path = config_mod.write_api_key(key, model=model, **kwargs)
                saved["ok"] = True
                tip = (
                    "\nYou can try a safe example next — it asks before every step."
                    if first_run
                    else ""
                )
                status.config(text=f"Saved to:\n{path}{tip}", fg="#6ecf8e")
                root.after(1600, root.destroy)
            except Exception as e:
                status.config(text=f"Failed: {e}", fg="#e07070")

        btn_row = tk.Frame(pad, bg=ui.BG)
        btn_row.pack(fill="x")

        save_btn = tk.Label(
            btn_row, text="  保存密钥  Save  ", bg=ui.ACCENT, fg=ui.ACCENT_FG,
            font=ui.font(15, bold=True), cursor="hand2", padx=16, pady=12,
        )
        save_btn.pack(side="left")
        save_btn.bind("<Button-1>", lambda e: save())

        cancel_btn = tk.Label(
            btn_row, text="  取消  Cancel  ", bg=ui.SURFACE, fg=ui.MUTED,
            font=ui.font(14), cursor="hand2", padx=16, pady=12,
        )
        cancel_btn.pack(side="left", padx=(10, 0))
        cancel_btn.bind("<Button-1>", lambda e: root.destroy())

        root.bind("<Return>", lambda e: save())
        root.bind("<Escape>", lambda e: root.destroy())

        root.update_idletasks()
        w, h = root.winfo_reqwidth(), root.winfo_reqheight()
        x = (root.winfo_screenwidth() - w) // 2
        y = (root.winfo_screenheight() - h) // 2
        root.geometry(f"+{x}+{y}")
        _apply_windows_glass(root)
        root.mainloop()
        return saved["ok"]
    except Exception as e:
        try:
            from tkinter import messagebox
            messagebox.showerror("secdogie-agent", f"Could not open key dialog:\n{e}")
        except Exception:
            pass
        return False


def ensure_api_key_or_prompt() -> bool:
    """If no API key is configured, show the first-run key dialog.

    Returns True if a key is available afterward (already was, or user saved
    one). Returns False if the user cancelled without saving.
    """
    from . import config as config_mod

    if config_mod.has_configured_api_key():
        return True
    return show_key_dialog(first_run=True)


def show_menu() -> list[str] | None:
    """Show the chooser; return the picked argv, or None if closed/cancelled.
    Raises nothing: any failure to build the window returns ["--gui"] so a
    double-clicked exe always does *something* useful.

    First-run: if no API key is present, the key dialog is shown first. Closing
    it without saving exits (returns None) so we don't start a doomed run.
    """
    try:
        # Gate on key before building the menu window.
        if not ensure_api_key_or_prompt():
            return None

        import tkinter as tk

        from . import config as config_mod

        root = tk.Tk()
        root.title("secdogie-agent")
        root.overrideredirect(True)  # borderless: the panel IS the window
        root.configure(bg=ui.BG)
        root.attributes("-topmost", True)

        result: list = [None]

        def choose(args: tuple[str, ...]) -> None:
            result[0] = list(args)
            root.destroy()

        def cancel(_event=None) -> None:
            result[0] = None
            root.destroy()

        def open_key_dialog(_event=None) -> None:
            root.withdraw()
            root.update()
            show_key_dialog(first_run=False)
            root.deiconify()

        pad = tk.Frame(root, bg=ui.BG)
        pad.pack(padx=22, pady=18, fill="both", expand=True)

        header = tk.Frame(pad, bg=ui.BG)
        header.pack(fill="x")
        tk.Label(header, text="secdogie", bg=ui.BG, fg=ui.FG,
                 font=ui.font(18, bold=True)).pack(side="left")
        close = tk.Label(header, text="\u2715", bg=ui.BG, fg=ui.MUTED,
                         font=ui.font(13), cursor="hand2", padx=8)
        close.pack(side="right")
        close.bind("<Button-1>", cancel)

        tk.Label(
            pad,
            text="An AI that can see your screen and use the mouse & keyboard.\n"
                 "It asks before each step. Your key stays on this machine.",
            bg=ui.BG, fg=ui.MUTED, font=ui.font(12), justify="left",
        ).pack(anchor="w", pady=(4, 12))

        if not config_mod.has_configured_api_key():
            # Should be rare (we gated above), but keep a visible hint.
            tk.Label(
                pad,
                text="No API key yet — open Set up / edit API key first.",
                bg=ui.BG, fg=ui.WARN, font=ui.font(12),
            ).pack(anchor="w", pady=(0, 8))

        for choice in MENU_CHOICES:
            card = tk.Frame(pad, bg=ui.SURFACE, cursor="hand2")
            card.pack(fill="x", pady=(0, 8), ipadx=4, ipady=4)
            title = tk.Label(card, text=choice.title, bg=ui.SURFACE, fg=ui.FG,
                             font=ui.font(14, bold=True), anchor="w", padx=12)
            title.pack(fill="x", pady=(6, 0))
            blurb = tk.Label(card, text=choice.blurb, bg=ui.SURFACE, fg=ui.MUTED,
                             font=ui.font(12), anchor="w", padx=12,
                             wraplength=380, justify="left")
            blurb.pack(fill="x", pady=(0, 6))

            widgets = (card, title, blurb)

            def on_enter(_e, ws=widgets):
                for w in ws:
                    w.configure(bg=ui.SURFACE_2)

            def on_leave(_e, ws=widgets):
                for w in ws:
                    w.configure(bg=ui.SURFACE)

            if choice.key == "config":
                def on_click(_e):
                    open_key_dialog()
            else:
                def on_click(_e, args=choice.args):
                    choose(args)

            for w in widgets:
                w.bind("<Enter>", on_enter)
                w.bind("<Leave>", on_leave)
                w.bind("<Button-1>", on_click)

        root.bind("<Escape>", cancel)

        drag = {"x": 0, "y": 0}

        def start_drag(e):
            drag["x"], drag["y"] = e.x_root - root.winfo_x(), e.y_root - root.winfo_y()

        def do_drag(e):
            root.geometry(f"+{e.x_root - drag['x']}+{e.y_root - drag['y']}")

        header.bind("<Button-1>", start_drag)
        header.bind("<B1-Motion>", do_drag)

        root.update_idletasks()
        w, h = root.winfo_reqwidth(), root.winfo_reqheight()
        x = (root.winfo_screenwidth() - w) // 2
        y = (root.winfo_screenheight() - h) // 2
        root.geometry(f"+{x}+{y}")
        _apply_windows_glass(root)

        root.mainloop()
        return result[0]
    except Exception:
        return ["--gui"]
