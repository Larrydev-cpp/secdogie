"""The secdogie window: a thin tkinter view over ``model.DialogModel``.

Everything the window decides is decided in the model. This file only draws
``model.messages()`` -- adding what is new and updating what changed, never
redrawing the conversation -- and calls model methods when the operator types
or clicks. It polls the model every ``POLL_MS``; no model or controller code
ever calls into tkinter.

An approval hashes the passphrase (Argon2id, about a second), so it runs on a
worker thread and its result comes back through a queue the poll drains. The
passphrase fields are cleared as soon as they are read.

``main()`` is the ``secdogie`` command: it starts the local node
(``local.LocalBackend``) and opens the window. On the first run the API key
card is shown first; nothing else is asked.
"""
from __future__ import annotations

import queue
import sys
import threading
import time

from secdogie_agent import theme as ui

from .local import AlreadyRunning, ApiKeys, LocalBackend
from .model import (
    APPROVAL,
    MEMORY,
    NOTICE,
    OPEN,
    PROBE,
    STATE_LABELS,
    WELCOME,
    YOU,
    DialogError,
    DialogModel,
    diff_messages,
)

POLL_MS = 150
ERROR = "#ff6b6b"
PROVIDERS = (("自动识别", ""), ("Anthropic", "anthropic"), ("OpenAI", "openai"), ("OpenRouter", "openrouter"))
CARD_TITLES = {PROBE: "secdogie 在问你", APPROVAL: "高风险步骤：需要你批准", MEMORY: "要记住这条吗"}


def _button(tk, parent, text, command, *, primary=False, danger=False):
    """A flat, clickable label (native buttons ignore colours on macOS).
    ``.invoke()`` runs the command, as a tk.Button's does."""
    bg = ui.ACCENT if primary else ui.SURFACE_2
    fg = ui.ACCENT_FG if primary else (ui.DENY if danger else ui.FG)
    b = tk.Label(parent, text=f"  {text}  ", bg=bg, fg=fg, font=ui.font(12, bold=primary), cursor="hand2",
                 padx=10, pady=6)
    b.enabled, b.fg = True, fg

    def invoke(_e=None):
        if b.enabled:
            command()

    b.invoke = invoke
    b.bind("<Button-1>", invoke)
    return b


def _say(label, text: str, fg: str | None = None) -> None:
    """Show ``text`` in ``label``; an empty label takes no room."""
    label.configure(text=text, **({"fg": fg} if fg else {}))
    if text:
        label.pack(anchor="w")
    else:
        label.pack_forget()


def _set_enabled(b, enabled: bool) -> None:
    b.enabled = enabled
    b.configure(cursor="hand2" if enabled else "arrow", fg=b.fg if enabled else ui.SUBTLE)


class _Card:
    """The widgets for one message. Plain messages are one label; cards also
    have a controls row, rebuilt when what they offer changes."""

    def __init__(self, win, msg):
        tk = win.tk
        self.win, self.msg = win, msg
        self.controls = None
        self.pass1 = self.pass2 = None
        self.buttons: list = []
        outer = self.frame = tk.Frame(win.stream, bg=ui.BG)
        outer.pack(fill="x", padx=14, pady=5)
        if msg.kind == YOU:
            self.body = tk.Label(outer, text=msg.text, bg=ui.SURFACE_2, fg=ui.FG, font=ui.font(13), justify="left",
                                 wraplength=win.wrap, padx=12, pady=8)
            self.body.pack(anchor="e")
            return
        if msg.kind not in CARD_TITLES:
            fg, size = (ui.MUTED, 11) if msg.kind == NOTICE else (ui.FG, 12)
            self.body = tk.Label(outer, text=msg.text, bg=ui.BG, fg=fg, font=ui.font(size), justify="left",
                                 wraplength=win.wrap)
            self.body.pack(anchor="w")
            return
        box = tk.Frame(outer, bg=ui.SURFACE, highlightthickness=1,
                       highlightbackground=ui.WARN if msg.kind == APPROVAL else ui.BORDER)
        box.pack(fill="x")
        self.box = box
        tk.Label(box, text=CARD_TITLES[msg.kind], bg=ui.SURFACE, fg=ui.WARN if msg.kind == APPROVAL else ui.MUTED,
                 font=ui.font(11, bold=True)).pack(anchor="w", padx=12, pady=(10, 2))
        self.body = tk.Label(box, text=msg.text, bg=ui.SURFACE, fg=ui.FG, font=ui.font(13, bold=True),
                             justify="left", wraplength=win.wrap - 30)
        self.body.pack(anchor="w", padx=12)
        self.details = []
        for line in msg.detail:
            d = tk.Label(box, text=line, bg=ui.SURFACE, fg=ui.MUTED, font=ui.font(11), justify="left",
                         wraplength=win.wrap - 30)
            d.pack(anchor="w", padx=12)
            self.details.append(d)
        # how the card ended, and what went wrong: shown only when there is something to say
        self.footer = tk.Frame(box, bg=ui.SURFACE)
        self.footer.pack(fill="x", padx=12, pady=(4, 10))
        self.state = tk.Label(self.footer, text="", bg=ui.SURFACE, fg=ui.MUTED, font=ui.font(11), justify="left")
        self.error = tk.Label(self.footer, text="", bg=ui.SURFACE, fg=ERROR, font=ui.font(11), justify="left",
                              wraplength=win.wrap - 30)
        self.update(msg, force=True)

    def labels(self):
        return [self.body, *getattr(self, "details", [])]

    # -- state ---------------------------------------------------------------------------

    def update(self, msg, *, force=False) -> None:
        old, self.msg = self.msg, msg
        if msg.kind not in CARD_TITLES:
            return
        if force or (old.state, old.actions) != (msg.state, msg.actions):
            self._controls()
        _say(self.state, "" if msg.state == OPEN else STATE_LABELS.get(msg.state, msg.state),
             ui.OK if msg.state in ("approved", "remembered", "answered") else ui.MUTED)
        if msg.state != OPEN:
            _say(self.error, "")

    def _controls(self) -> None:
        tk, win, msg = self.win.tk, self.win, self.msg
        if self.controls is not None:
            self.controls.destroy()
        self.controls = None
        self.pass1 = self.pass2 = None
        self.buttons = []
        self.countdown = None
        if msg.state != OPEN:
            return
        row = self.controls = tk.Frame(self.box, bg=ui.SURFACE)
        row.pack(fill="x", padx=12, pady=(8, 0), before=self.footer)
        if msg.kind == PROBE:
            for i, option in enumerate(msg.options, 1):
                b = _button(tk, row, option, lambda i=i: win.answer_option(self, i))
                b.pack(side="left", padx=(0, 6), pady=2)
                self.buttons.append(b)
            tk.Label(row, text="或者在下面的输入框里回答", bg=ui.SURFACE, fg=ui.SUBTLE,
                     font=ui.font(11)).pack(side="left", padx=4)
        elif msg.kind == APPROVAL:
            if "approve" in msg.actions:
                first = not win.model.passphrase_set
                self.pass1 = self._entry(row, "设置口令（至少 8 位）" if first else "口令")
                if first:
                    self.pass2 = self._entry(row, "再输一次口令")
                    self.pass2.bind("<Return>", lambda e: win.approve(self))
                else:
                    self.pass1.bind("<Return>", lambda e: win.approve(self))
            buttons = tk.Frame(row, bg=ui.SURFACE)
            buttons.pack(anchor="w", pady=(6, 0))
            if "approve" in msg.actions:
                b = _button(tk, buttons, "批准并签名", lambda: win.approve(self), primary=True)
                b.pack(side="left", padx=(0, 6))
                self.buttons.append(b)
            b = _button(tk, buttons, "拒绝", lambda: win.deny(self), danger=True)
            b.pack(side="left", padx=(0, 6))
            self.buttons.append(b)
            self.countdown = tk.Label(buttons, text="", bg=ui.SURFACE, fg=ui.MUTED, font=ui.font(11))
            self.countdown.pack(side="left", padx=6)
        elif msg.kind == MEMORY:
            if "remember" in msg.actions:
                b = _button(tk, row, "记住", lambda: win.remember(self), primary=True)
                b.pack(side="left", padx=(0, 6))
                self.buttons.append(b)
            b = _button(tk, row, "不记", lambda: win.skip(self))
            b.pack(side="left")
            self.buttons.append(b)

    def _entry(self, parent, label):
        tk = self.win.tk
        tk.Label(parent, text=label, bg=ui.SURFACE, fg=ui.MUTED, font=ui.font(11)).pack(anchor="w")
        e = tk.Entry(parent, show="•", width=32, font=ui.font(13), bg=ui.SURFACE_2, fg=ui.FG,
                     insertbackground=ui.FG, relief="flat", highlightthickness=1, highlightcolor=ui.ACCENT,
                     highlightbackground=ui.BORDER)
        e.pack(anchor="w", ipady=5, pady=(2, 4))
        return e

    def read_passphrases(self) -> tuple[str, str | None]:
        """What was typed, and the fields emptied right away."""
        p1 = self.pass1.get() if self.pass1 is not None else ""
        p2 = self.pass2.get() if self.pass2 is not None else None
        for e in (self.pass1, self.pass2):
            if e is not None:
                e.delete(0, "end")
        return p1, p2

    def busy(self, text: str) -> None:
        for b in self.buttons:
            _set_enabled(b, False)
        _say(self.error, text, ui.MUTED)

    def failed(self, text: str) -> None:
        _say(self.error, text, ERROR)
        self._controls()  # fresh controls: enabled, and the right fields for the passphrase state

    def tick(self, now: float) -> None:
        if self.countdown is not None and self.msg.expires_at:
            self.countdown.configure(text=f"{max(0, int(self.msg.expires_at - now))} 秒内不批准即视为拒绝")


class Window:
    def __init__(self, root, model: DialogModel, *, on_close=None, clock=time.time):
        import tkinter as tk

        self.tk, self.root, self.model = tk, root, model
        self._on_close, self._clock = on_close, clock
        self._shown: tuple = ()
        self.cards: dict[str, _Card] = {}
        self._results: queue.Queue = queue.Queue()
        self._passphrase_set = model.passphrase_set
        self._closed = False
        self.wrap = 460
        self.need_key = model.api_key_needed()
        root.title("secdogie")
        root.configure(bg=ui.BG)
        root.geometry("640x780")
        root.minsize(440, 520)
        self._build()
        root.protocol("WM_DELETE_WINDOW", self.close)
        if self.need_key:
            self.show_key_card(first_run=True)
        self._chrome()

    # -- layout ---------------------------------------------------------------------------

    def _build(self) -> None:
        tk, root = self.tk, self.root
        head = tk.Frame(root, bg=ui.BG)
        head.pack(fill="x", padx=16, pady=(12, 6))
        tk.Label(head, text="secdogie", bg=ui.BG, fg=ui.FG, font=ui.font(17, bold=True)).pack(side="left")
        self.key_button = _button(tk, head, "API key", lambda: self.show_key_card(first_run=False))
        self.key_button.pack(side="right")
        self.status = tk.Label(root, text="", bg=ui.BG, fg=ui.MUTED, font=ui.font(11), anchor="w")
        self.status.pack(fill="x", padx=16)

        self.key_panel = tk.Frame(root, bg=ui.SURFACE, highlightthickness=1, highlightbackground=ui.BORDER)

        foot = self.foot = tk.Frame(root, bg=ui.BG)
        foot.pack(side="bottom", fill="x", padx=16, pady=(6, 14))
        self.hint = tk.Label(foot, text="", bg=ui.BG, fg=ui.MUTED, font=ui.font(11), anchor="w")
        self.hint.pack(fill="x")
        row = tk.Frame(foot, bg=ui.BG)
        row.pack(fill="x", pady=(4, 0))
        self.composer = tk.Entry(row, font=ui.font(14), bg=ui.SURFACE, fg=ui.FG, insertbackground=ui.FG,
                                 disabledbackground=ui.SURFACE, disabledforeground=ui.SUBTLE, relief="flat",
                                 highlightthickness=1, highlightcolor=ui.ACCENT, highlightbackground=ui.BORDER)
        self.composer.pack(side="left", fill="x", expand=True, ipady=9)
        self.composer.bind("<Return>", lambda e: self.send())
        self.send_button = _button(tk, row, "发送", self.send, primary=True)
        self.send_button.pack(side="left", padx=(8, 0))
        self.stop_button = _button(tk, row, "停止", self.stop, danger=True)
        self.stop_button.pack(side="left", padx=(8, 0))
        self.error = tk.Label(foot, text="", bg=ui.BG, fg=ERROR, font=ui.font(11), anchor="w", justify="left")
        self.error.pack(fill="x")

        body = tk.Frame(root, bg=ui.BG)
        body.pack(fill="both", expand=True, pady=(6, 0))
        self.canvas = tk.Canvas(body, bg=ui.BG, highlightthickness=0)
        bar = tk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.stream = tk.Frame(self.canvas, bg=ui.BG)
        self._stream_id = self.canvas.create_window((0, 0), window=self.stream, anchor="nw")
        self.stream.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._resized)
        root.bind_all("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-e.delta / 120) or -e.delta, "units"))
        root.bind_all("<Button-4>", lambda e: self.canvas.yview_scroll(-3, "units"))
        root.bind_all("<Button-5>", lambda e: self.canvas.yview_scroll(3, "units"))
        tk.Label(self.stream, text=WELCOME, bg=ui.BG, fg=ui.MUTED, font=ui.font(12), justify="left",
                 wraplength=self.wrap).pack(anchor="w", padx=14, pady=(8, 10))

    def _resized(self, event) -> None:
        self.canvas.itemconfigure(self._stream_id, width=event.width)
        wrap = max(260, event.width - 120)
        if wrap != self.wrap:
            self.wrap = wrap
            for card in self.cards.values():
                for label in card.labels():
                    label.configure(wraplength=wrap if card.msg.kind not in CARD_TITLES else wrap - 30)

    # -- the API key card ---------------------------------------------------------------------

    def show_key_card(self, *, first_run: bool) -> None:
        tk, panel = self.tk, self.key_panel
        for w in panel.winfo_children():
            w.destroy()
        tk.Label(panel, text="先填一个 API key" if first_run else "更换 API key", bg=ui.SURFACE, fg=ui.FG,
                 font=ui.font(14, bold=True)).pack(anchor="w", padx=14, pady=(12, 2))
        tk.Label(panel, text="secdogie 用你自己的模型 key 看屏幕、做决定。key 只存在这台电脑上，不上传。",
                 bg=ui.SURFACE, fg=ui.MUTED, font=ui.font(11), justify="left", wraplength=self.wrap
                 ).pack(anchor="w", padx=14)
        self.provider = tk.StringVar(value="")
        row = tk.Frame(panel, bg=ui.SURFACE)
        row.pack(anchor="w", padx=14, pady=(8, 2))
        for label, value in PROVIDERS:
            tk.Radiobutton(row, text=label, variable=self.provider, value=value, bg=ui.SURFACE, fg=ui.FG,
                           selectcolor=ui.SURFACE_2, activebackground=ui.SURFACE, activeforeground=ui.FG,
                           font=ui.font(11)).pack(side="left", padx=(0, 10))
        tk.Label(panel, text="API key", bg=ui.SURFACE, fg=ui.MUTED, font=ui.font(11)).pack(anchor="w", padx=14)
        self.key_entry = tk.Entry(panel, show="•", font=ui.font(13), bg=ui.SURFACE_2, fg=ui.FG,
                                  insertbackground=ui.FG, relief="flat", highlightthickness=1,
                                  highlightcolor=ui.ACCENT, highlightbackground=ui.BORDER)
        self.key_entry.pack(fill="x", padx=14, pady=(4, 4), ipady=7)
        self.key_entry.bind("<Return>", lambda e: self.save_key())
        tk.Label(panel, text="模型（可不填）", bg=ui.SURFACE, fg=ui.MUTED, font=ui.font(11)).pack(anchor="w", padx=14)
        self.model_entry = tk.Entry(panel, font=ui.font(12), bg=ui.SURFACE_2, fg=ui.FG, insertbackground=ui.FG,
                                    relief="flat", highlightthickness=1, highlightcolor=ui.ACCENT,
                                    highlightbackground=ui.BORDER)
        self.model_entry.pack(fill="x", padx=14, pady=(2, 6), ipady=5)
        buttons = tk.Frame(panel, bg=ui.SURFACE)
        buttons.pack(anchor="w", padx=14, pady=(2, 4))
        _button(tk, buttons, "保存", self.save_key, primary=True).pack(side="left")
        if not first_run:
            _button(tk, buttons, "取消", self.hide_key_card).pack(side="left", padx=(8, 0))
        self.key_status = tk.Label(panel, text="", bg=ui.SURFACE, fg=ui.MUTED, font=ui.font(11), justify="left",
                                   wraplength=self.wrap)
        self.key_status.pack(anchor="w", padx=14, pady=(0, 12))
        panel.pack(fill="x", padx=16, pady=(8, 0), after=self.status)
        self.key_entry.focus_set()

    def hide_key_card(self) -> None:
        self.key_panel.pack_forget()

    def save_key(self) -> None:
        key = self.key_entry.get()
        try:
            path = self.model.save_api_key(key, provider=self.provider.get() or None,
                                           model=self.model_entry.get())
        except DialogError as e:
            self.key_status.configure(text=str(e), fg=ERROR)
            return
        self.key_entry.delete(0, "end")
        self.need_key = False
        self.key_status.configure(text=f"已保存：{path}", fg=ui.OK)
        self.root.after(1200, self.hide_key_card)
        self._chrome()

    # -- the operator's actions --------------------------------------------------------------------

    def _attempt(self, fn, *args, card: _Card | None = None) -> bool:
        try:
            fn(*args)
        except DialogError as e:
            if card is not None:
                card.failed(str(e))
            else:
                self.error.configure(text=str(e))
            return False
        self.error.configure(text="")
        self.pump()
        return True

    def send(self) -> None:
        text = self.composer.get()
        if self._attempt(self.model.send, text):
            self.composer.delete(0, "end")

    def stop(self) -> None:
        self._attempt(self.model.stop)

    def answer_option(self, card: _Card, option: int) -> None:
        self._attempt(lambda: self.model.answer(card.msg.ref, option=option), card=card)

    def deny(self, card: _Card) -> None:
        self._attempt(self.model.deny, card.msg.ref, card=card)

    def remember(self, card: _Card) -> None:
        self._attempt(self.model.remember, card.msg.ref, card=card)

    def skip(self, card: _Card) -> None:
        self._attempt(self.model.skip, card.msg.ref, card=card)

    def approve(self, card: _Card) -> None:
        passphrase, again = card.read_passphrases()
        card.busy("正在解锁并签名……")
        key = card.msg.key

        def work():
            try:
                self.model.approve(card.msg.ref, passphrase, again)
                self._results.put((key, None))
            except DialogError as e:
                self._results.put((key, str(e)))
            except Exception as e:  # noqa: BLE001 - shown on the card; nothing was signed
                self._results.put((key, f"出错了，没有签名：{type(e).__name__}: {e}"))

        threading.Thread(target=work, daemon=True, name="secdogie-approve").start()

    # -- keeping up with the model ------------------------------------------------------------------

    def pump(self) -> None:
        """One round: approval results, then whatever changed in the model."""
        while True:
            try:
                key, error = self._results.get_nowait()
            except queue.Empty:
                break
            card = self.cards.get(key)
            if card is not None and error:
                card.failed(error)
            elif card is not None:
                _say(card.error, "")
        changed = self.model.refresh()
        if self.model.passphrase_set != self._passphrase_set:
            self._passphrase_set = self.model.passphrase_set
            for card in self.cards.values():  # the fields an open approval card asks for have changed
                if card.msg.kind == APPROVAL and card.msg.state == OPEN:
                    card._controls()
        if changed:
            self._render()
        now = self._clock()
        for card in self.cards.values():
            if card.msg.kind == APPROVAL and card.msg.state == OPEN:
                card.tick(now)
        self._chrome()

    def _render(self) -> None:
        msgs = self.model.messages()
        added, changed = diff_messages(self._shown, msgs)
        at_bottom = self.canvas.yview()[1] >= 0.98
        for m in changed:
            if m.key in self.cards:
                self.cards[m.key].update(m)
        for m in added:
            self.cards[m.key] = _Card(self, m)
        self._shown = msgs
        if added and (at_bottom or any(m.kind in CARD_TITLES or m.kind == YOU for m in added)):
            self.root.after_idle(lambda: self.canvas.yview_moveto(1.0))

    def _chrome(self) -> None:
        self.status.configure(text=self.model.status())
        if self.need_key:
            self.hint.configure(text="先在上面填好 API key")
            self.composer.configure(state="disabled")
            _set_enabled(self.send_button, False)
        else:
            self.hint.configure(text=self.model.composer_hint())
            self.composer.configure(state="normal")
            _set_enabled(self.send_button, True)
        _set_enabled(self.stop_button, bool(self.model.active_goals()))

    def _poll(self) -> None:
        if self._closed:
            return
        try:
            self.pump()
        finally:
            self.root.after(POLL_MS, self._poll)

    def run(self) -> None:
        self.root.after(POLL_MS, self._poll)
        self.root.mainloop()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._on_close is not None:
                self._on_close()
        finally:
            self.root.destroy()


def main(argv=None) -> int:
    """``secdogie``: start the local node and open the window."""
    try:
        import tkinter as tk
        from tkinter import messagebox
    except ImportError:
        print("secdogie needs tkinter (the Tk GUI toolkit that ships with Python).", file=sys.stderr)
        return 2
    try:
        backend = LocalBackend()
    except AlreadyRunning:
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("secdogie", "secdogie 已经打开了。")
        root.destroy()
        return 1
    except Exception as e:  # noqa: BLE001 - say what failed instead of vanishing
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("secdogie", f"secdogie 没能启动：\n{type(e).__name__}: {e}")
        root.destroy()
        return 1
    try:
        controller = backend.start()
        root = tk.Tk()
        window = Window(root, DialogModel(controller, backend.operator_key, ApiKeys()), on_close=backend.stop)
        ui.apply_glass(root)
    except BaseException:
        backend.stop()
        raise
    window.run()
    return 0


__all__ = ["POLL_MS", "Window", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
