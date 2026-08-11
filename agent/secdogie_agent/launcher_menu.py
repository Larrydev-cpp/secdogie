"""Built-in launcher menu: double-click the packaged exe -> a frosted-glass
chooser, no extra files, no terminal knowledge.

A frozen single-file build launched with NO arguments (i.e. double-clicked)
shows this window first and turns the clicked card into the CLI arguments it
stands for -- so `secdogie-agent.exe` alone is the whole install: one file,
open it, pick what to do. Launched *with* arguments (a terminal user, the
macro/skill flags, scripts), the menu never appears and the CLI is untouched.

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
        "Describe a task",
        "Type what you want done; it asks before every step.",
        ("--gui",),
    ),
    MenuChoice(
        "dry",
        "Preview first (dry run)",
        "See what it would do -- touches nothing on your machine.",
        ("--gui", "--dry-run"),
    ),
    MenuChoice(
        "ax",
        "Element mode (accessibility)",
        "Clicks UI elements by identity, not by pixel -- steadier on real apps.",
        ("--gui", "--desktop-ax"),
    ),
    MenuChoice(
        "auto",
        "Unattended (careful)",
        "No per-step confirmation. High-risk actions still ask.",
        ("--gui", "--auto"),
    ),
    MenuChoice(
        "config",
        "Set up / edit API key",
        "Paste any provider key (Anthropic, OpenAI, DeepSeek, Groq, custom…).",
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

# Palette: a dark glass panel. On Windows the acrylic tint below shows through
# these; elsewhere they're just a good-looking dark UI.
_BG = "#1f1a22"          # panel base (also the acrylic fallback colour)
_CARD = "#332d38"        # card at rest
_CARD_HOVER = "#4a4252"  # card under the pointer
_FG = "#ffffff"
_FG_DIM = "#b9b3c0"
_ACCENT = "#7c6aef"      # soft purple for the save button


def _apply_windows_glass(root) -> None:
    """Acrylic blur-behind + rounded corners via the OS compositor. Windows
    only, best-effort: any failure (old build, unexpected pyinstaller/tk HWND
    shape) leaves the plain dark panel, never an error."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        class ACCENT_POLICY(ctypes.Structure):
            _fields_ = [
                ("AccentState", ctypes.c_int),
                ("Flags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint),
                ("AnimationId", ctypes.c_int),
            ]

        class WINCOMPATTRDATA(ctypes.Structure):
            _fields_ = [
                ("Attribute", ctypes.c_int),
                ("Data", ctypes.c_void_p),
                ("SizeOfData", ctypes.c_size_t),
            ]

        user32 = ctypes.windll.user32
        # tkinter's winfo_id is a child; the top-level HWND is its parent.
        hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()

        accent = ACCENT_POLICY()
        accent.AccentState = 4  # ACCENT_ENABLE_ACRYLICBLURBEHIND (1803+)
        accent.GradientColor = 0xCC221A22  # 0xAABBGGRR: dark tint at ~80%
        data = WINCOMPATTRDATA()
        data.Attribute = 19  # WCA_ACCENT_POLICY
        data.Data = ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p)
        data.SizeOfData = ctypes.sizeof(accent)
        user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data))

        # Windows 11 rounded corners (no-op error on Win10 -- fine).
        pref = ctypes.c_int(2)  # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref)  # DWMWA_WINDOW_CORNER_PREFERENCE
        )
    except Exception:
        pass


def show_key_dialog() -> None:
    """A proper dialog for pasting an API key from any vendor.

    Supports Anthropic, OpenAI, and any OpenAI-compatible / custom key name.
    Writes next to the exe (or the portable location) with clear feedback.
    """
    try:
        import tkinter as tk

        from . import config as config_mod

        root = tk.Tk()
        root.title("secdogie-agent — API key")
        root.configure(bg=_BG)
        root.resizable(False, False)
        root.attributes("-topmost", True)

        pad = tk.Frame(root, bg=_BG)
        pad.pack(padx=24, pady=20, fill="both", expand=True)

        tk.Label(
            pad, text="Paste your API key",
            bg=_BG, fg=_FG, font=("Segoe UI", 14, "bold"),
        ).pack(anchor="w")

        tk.Label(
            pad,
            text="Any provider: Anthropic, OpenAI, DeepSeek, Groq, local models…\n"
                 "Key stays on disk next to the program — nothing is uploaded.",
            bg=_BG, fg=_FG_DIM, font=("Segoe UI", 9), justify="left",
        ).pack(anchor="w", pady=(4, 12))

        # Provider / key-name choice
        kind_var = tk.StringVar(value="anthropic")
        kind_row = tk.Frame(pad, bg=_BG)
        kind_row.pack(fill="x", pady=(0, 6))

        for label, value in (
            ("Anthropic", "anthropic"),
            ("OpenAI / compatible", "openai"),
            ("Custom env name", "custom"),
        ):
            tk.Radiobutton(
                kind_row, text=label, variable=kind_var, value=value,
                bg=_BG, fg=_FG, selectcolor=_CARD, activebackground=_BG,
                activeforeground=_FG, font=("Segoe UI", 9),
            ).pack(side="left", padx=(0, 12))

        # Custom env var name (shown only when Custom is selected)
        custom_row = tk.Frame(pad, bg=_BG)
        custom_row.pack(fill="x", pady=(0, 8))
        tk.Label(
            custom_row, text="Env var:", bg=_BG, fg=_FG_DIM, font=("Segoe UI", 9),
        ).pack(side="left")
        custom_env_var = tk.StringVar(value="OPENAI_API_KEY")
        custom_entry = tk.Entry(
            custom_row, textvariable=custom_env_var, width=28,
            font=("Consolas", 10), bg=_CARD, fg=_FG, insertbackground=_FG,
            relief="flat", highlightthickness=1, highlightcolor=_ACCENT,
            highlightbackground="#4a4252",
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
            pad, text="API key", bg=_BG, fg=_FG_DIM, font=("Segoe UI", 9),
        ).pack(anchor="w")
        key_var = tk.StringVar()
        entry = tk.Entry(
            pad, textvariable=key_var, width=52, show="•",
            font=("Consolas", 11), bg=_CARD, fg=_FG, insertbackground=_FG,
            relief="flat", highlightthickness=1, highlightcolor=_ACCENT,
            highlightbackground="#4a4252",
        )
        entry.pack(fill="x", ipady=8, pady=(2, 6))
        entry.focus_set()

        # Optional default model
        tk.Label(
            pad, text="Default model (optional)", bg=_BG, fg=_FG_DIM, font=("Segoe UI", 9),
        ).pack(anchor="w")
        model_var = tk.StringVar()
        model_entry = tk.Entry(
            pad, textvariable=model_var, width=52,
            font=("Consolas", 10), bg=_CARD, fg=_FG, insertbackground=_FG,
            relief="flat", highlightthickness=1, highlightcolor=_ACCENT,
            highlightbackground="#4a4252",
        )
        model_entry.pack(fill="x", ipady=6, pady=(2, 4))
        tk.Label(
            pad,
            text="e.g. claude-sonnet-5 · gpt-5.5 · deepseek-chat · openai/gpt-5.5",
            bg=_BG, fg=_FG_DIM, font=("Segoe UI", 8),
        ).pack(anchor="w", pady=(0, 8))

        # Show/hide toggle
        show_var = tk.BooleanVar(value=False)

        def toggle_show():
            entry.config(show="" if show_var.get() else "•")

        tk.Checkbutton(
            pad, text="Show key", variable=show_var, command=toggle_show,
            bg=_BG, fg=_FG_DIM, selectcolor=_CARD, activebackground=_BG,
            activeforeground=_FG_DIM, font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(0, 10))

        status = tk.Label(pad, text="", bg=_BG, fg=_FG_DIM, font=("Segoe UI", 9), justify="left")
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
            if kind == "custom":
                env_name = custom_env_var.get().strip()
                if not env_name:
                    status.config(text="Custom env var name is empty.", fg="#e07070")
                    return
                kwargs = {"env_var": env_name}
            elif kind == "openai":
                kwargs = {"provider": "openai"}
            else:
                kwargs = {"provider": "anthropic"}

            model = model_var.get().strip() or None
            try:
                path = config_mod.write_api_key(key, model=model, **kwargs)
                status.config(text=f"Saved to:\n{path}", fg="#6ecf8e")
                root.after(1800, root.destroy)
            except Exception as e:
                status.config(text=f"Failed: {e}", fg="#e07070")

        btn_row = tk.Frame(pad, bg=_BG)
        btn_row.pack(fill="x")

        save_btn = tk.Label(
            btn_row, text="  Save key  ", bg=_ACCENT, fg=_FG,
            font=("Segoe UI", 10, "bold"), cursor="hand2", padx=12, pady=6,
        )
        save_btn.pack(side="left")
        save_btn.bind("<Button-1>", lambda e: save())

        cancel_btn = tk.Label(
            btn_row, text="  Cancel  ", bg=_CARD, fg=_FG_DIM,
            font=("Segoe UI", 10), cursor="hand2", padx=12, pady=6,
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
    except Exception as e:
        try:
            from tkinter import messagebox
            messagebox.showerror("secdogie-agent", f"Could not open key dialog:\n{e}")
        except Exception:
            pass


def show_menu() -> list[str] | None:
    """Show the chooser; return the picked argv, or None if closed/cancelled.
    Raises nothing: any failure to build the window returns ["--gui"] so a
    double-clicked exe always does *something* useful."""
    try:
        import tkinter as tk

        root = tk.Tk()
        root.title("secdogie-agent")
        root.overrideredirect(True)  # borderless: the panel IS the window
        root.configure(bg=_BG)
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
            show_key_dialog()
            root.deiconify()

        pad = tk.Frame(root, bg=_BG)
        pad.pack(padx=22, pady=18, fill="both", expand=True)

        header = tk.Frame(pad, bg=_BG)
        header.pack(fill="x")
        tk.Label(header, text="secdogie-agent", bg=_BG, fg=_FG,
                 font=("Segoe UI", 15, "bold")).pack(side="left")
        close = tk.Label(header, text="✕", bg=_BG, fg=_FG_DIM,
                         font=("Segoe UI", 11), cursor="hand2", padx=8)
        close.pack(side="right")
        close.bind("<Button-1>", cancel)
        tk.Label(pad, text="What should it do?", bg=_BG, fg=_FG_DIM,
                 font=("Segoe UI", 10)).pack(anchor="w", pady=(0, 12))

        for choice in MENU_CHOICES:
            card = tk.Frame(pad, bg=_CARD, cursor="hand2")
            card.pack(fill="x", pady=(0, 8), ipadx=4, ipady=4)
            title = tk.Label(card, text=choice.title, bg=_CARD, fg=_FG,
                             font=("Segoe UI", 11, "bold"), anchor="w", padx=12)
            title.pack(fill="x", pady=(6, 0))
            blurb = tk.Label(card, text=choice.blurb, bg=_CARD, fg=_FG_DIM,
                             font=("Segoe UI", 9), anchor="w", padx=12,
                             wraplength=380, justify="left")
            blurb.pack(fill="x", pady=(0, 6))

            widgets = (card, title, blurb)

            def on_enter(_e, ws=widgets):
                for w in ws:
                    w.configure(bg=_CARD_HOVER)

            def on_leave(_e, ws=widgets):
                for w in ws:
                    w.configure(bg=_CARD)

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
