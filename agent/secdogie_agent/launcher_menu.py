"""What a double-clicked exe does, and the agent CLI's API key dialog.

A frozen single-file build launched with NO arguments (a double-click) opens
the one secdogie window (``cli.open_window``, the ``secdogie_app`` package):
goals, questions, approvals, memory, other machines' nodes and the API key, in
one conversation. Launched *with* arguments (a terminal user, the macro/skill
flags, scripts), the CLI is untouched. The card menu that used to open here is
retired.

``show_key_dialog`` is the key dialog the CLI's ``--gui`` path still uses when
no API key is configured. The glass: tkinter draws the panel, and on Windows
the real acrylic blur comes from the OS compositor (``theme.apply_glass``);
anywhere it can't apply, the window still shows as a clean dark panel.
"""
from __future__ import annotations

import sys

from . import theme as ui


def should_offer(argv: list[str]) -> bool:
    """A double-click: a frozen (packaged) build launched with no arguments at
    all. That opens the secdogie window (``cli.open_window``). Any explicit
    argument means a deliberate invocation (terminal, script, the .bat passing
    flags), and the CLI must behave exactly as documented. Running from source
    keeps the plain CLI too (developers have a terminal by definition)."""
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
            problem = config_mod.api_key_problem(key)
            if problem:
                status.config(text=problem, fg="#e07070")
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
