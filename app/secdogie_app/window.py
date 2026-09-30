"""The secdogie window: a thin tkinter view over ``model.DialogModel``.

Everything the window decides is decided in the model. This file only draws
``model.messages()`` -- adding what is new and updating what changed, never
redrawing the conversation -- and calls model methods when the operator types
or clicks. It polls the model every ``POLL_MS``; no model or controller code
ever calls into tkinter.

An approval hashes the passphrase (Argon2id, about a second), so it runs on a
worker thread and its result comes back through a queue the poll drains. The
passphrase fields are cleared as soon as they are read.

The window drives one node at a time, chosen in the switcher at the top:
this machine's own, or a node on another machine the operator paired with
(``nodes.NodeHub``); each keeps its own conversation. The "视界" fold shows
the structural view the node sent -- elements and focus, never a screenshot.

``main()`` is the ``secdogie`` command: it starts the local node
(``local.LocalBackend``) and opens the window. On the first run the API key
card is shown first; nothing else is asked.
"""
from __future__ import annotations

import gc
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
    diff_messages,
)
from .nodes import LOCAL, NodeBook, NodeHub

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
    """``hub`` is the ``NodeHub``: the nodes this window drives."""

    def __init__(self, root, hub: NodeHub, *, on_close=None, clock=time.time):
        import tkinter as tk

        self.tk, self.root, self.hub = tk, root, hub
        self._on_close, self._clock = on_close, clock
        self._shown: tuple = ()
        self.cards: dict[str, _Card] = {}
        self._results: queue.Queue = queue.Queue()
        self._passphrase_set = hub.key.is_set
        self._closed = False
        self.wrap = 460
        self.need_key = hub.local.api_key_needed()
        self.provider = None  # the key card's provider choice (a tk.StringVar while the card exists)
        self.view_open = False
        self._view_shown: list | None = None
        root.title("secdogie")
        root.configure(bg=ui.BG)
        root.geometry("640x780")
        root.minsize(440, 520)
        self._build()
        root.protocol("WM_DELETE_WINDOW", self.close)
        if self.need_key:
            self.show_key_card(first_run=True)
        self._chrome()

    @property
    def model(self):
        """The node shown now."""
        return self.hub.model

    # -- layout ---------------------------------------------------------------------------

    def _build(self) -> None:
        tk, root = self.tk, self.root
        head = tk.Frame(root, bg=ui.BG)
        head.pack(fill="x", padx=16, pady=(12, 6))
        tk.Label(head, text="secdogie", bg=ui.BG, fg=ui.FG, font=ui.font(17, bold=True)).pack(side="left")
        self.switcher = _button(tk, head, "本机 ▾", self.show_switcher)
        self.switcher.pack(side="left", padx=(12, 0))
        self.key_button = _button(tk, head, "API key", lambda: self.show_key_card(first_run=False))
        self.key_button.pack(side="right")
        self.view_button = _button(tk, head, "视界 ▸", self.toggle_view)
        self.view_button.pack(side="right", padx=(0, 8))
        self.status = tk.Label(root, text="", bg=ui.BG, fg=ui.MUTED, font=ui.font(11), anchor="w")
        self.status.pack(fill="x", padx=16)

        self.key_panel = tk.Frame(root, bg=ui.SURFACE, highlightthickness=1, highlightbackground=ui.BORDER)
        self.pair_panel = tk.Frame(root, bg=ui.SURFACE, highlightthickness=1, highlightbackground=ui.BORDER)
        self.view_panel = tk.Frame(root, bg=ui.SURFACE, highlightthickness=1, highlightbackground=ui.BORDER)
        self.view_text = tk.Text(self.view_panel, height=12, bg=ui.SURFACE, fg=ui.MUTED, font=ui.font(11, mono=True),
                                 relief="flat", wrap="none", state="disabled", padx=10, pady=8)
        self.view_text.tag_configure("focus", foreground=ui.WARN)
        self.view_text.pack(fill="both", expand=True)

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

        body = self.body = tk.Frame(root, bg=ui.BG)
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
        self.welcome = tk.Label(self.stream, text=WELCOME, bg=ui.BG, fg=ui.MUTED, font=ui.font(12),
                                justify="left", wraplength=self.wrap)
        self.welcome.pack(anchor="w", padx=14, pady=(8, 10))

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
        panel.pack(fill="x", padx=16, pady=(8, 0), before=self.body)
        self.key_entry.focus_set()

    def hide_key_card(self) -> None:
        self.key_panel.pack_forget()

    def save_key(self) -> None:
        key = self.key_entry.get()
        try:
            path = self.hub.local.save_api_key(key, provider=self.provider.get() or None,
                                               model=self.model_entry.get())
        except DialogError as e:
            self.key_status.configure(text=str(e), fg=ERROR)
            return
        self.key_entry.delete(0, "end")
        self.need_key = False
        self.key_status.configure(text=f"已保存：{path}", fg=ui.OK)
        self.root.after(1200, self.hide_key_card)
        self._chrome()

    # -- nodes: the switcher and pairing ---------------------------------------------------------

    def show_switcher(self) -> None:
        menu = self.tk.Menu(self.root, tearoff=0, bg=ui.SURFACE, fg=ui.FG, activebackground=ui.SURFACE_2,
                            activeforeground=ui.FG)
        for key, name in self.hub.choices():
            mark = "• " if key == self.hub.current else "  "
            menu.add_command(label=f"{mark}{name}", command=lambda k=key: self.switch(k))
        menu.add_separator()
        menu.add_command(label="添加远程节点…", command=self.show_pair_card)
        if self.hub.current != LOCAL:
            menu.add_command(label=f"取消配对「{self.hub.name(self.hub.current)}」",
                             command=lambda k=self.hub.current: self.forget(k))
        try:
            menu.tk_popup(self.switcher.winfo_rootx(), self.switcher.winfo_rooty() + self.switcher.winfo_height())
        finally:
            menu.grab_release()

    def switch(self, key: str) -> bool:
        """Show node ``key``: its own conversation, from the start."""
        try:
            self.hub.switch(key)
        except DialogError as e:
            self.error.configure(text=str(e))
            return False
        self.error.configure(text="")
        self._reset_stream()
        return True

    def forget(self, key: str) -> None:
        self.hub.forget(key)
        self._reset_stream()

    def _reset_stream(self) -> None:
        for card in self.cards.values():
            card.frame.destroy()
        self.cards, self._shown, self._view_shown = {}, (), None
        self._passphrase_set = self.hub.key.is_set
        self.model.refresh()
        self._render()
        self._update_view()
        self._chrome()

    def show_pair_card(self) -> None:
        tk, panel = self.tk, self.pair_panel
        for w in panel.winfo_children():
            w.destroy()
        app_did, operator_did = self.hub.pairing_info()
        tk.Label(panel, text="添加远程节点", bg=ui.SURFACE, fg=ui.FG, font=ui.font(14, bold=True)
                 ).pack(anchor="w", padx=14, pady=(12, 2))
        tk.Label(panel, text="在那台机器上运行 secdogie-node，并让它信任这个窗口：",
                 bg=ui.SURFACE, fg=ui.MUTED, font=ui.font(11), justify="left", wraplength=self.wrap
                 ).pack(anchor="w", padx=14)
        self._did_row(panel, "App DID（加到对方的 --apps）", app_did)
        if operator_did:
            self._did_row(panel, "操作员 DID（加到对方的 --operators）", operator_did)
            self.pair_pass1 = self.pair_pass2 = None
        else:
            tk.Label(panel, text="还没有操作员钥：先设置口令（至少 8 位，输两次），对方要信任它才能批准高风险步骤。",
                     bg=ui.SURFACE, fg=ui.WARN, font=ui.font(11), justify="left", wraplength=self.wrap
                     ).pack(anchor="w", padx=14, pady=(8, 2))
            row = tk.Frame(panel, bg=ui.SURFACE)
            row.pack(anchor="w", padx=14)
            self.pair_pass1 = self._secret(row)
            self.pair_pass2 = self._secret(row)
            _button(tk, row, "设置口令", self.set_passphrase_now).pack(side="left", padx=(4, 0))
        tk.Label(panel, text="把那台节点启动时打印的 ready 行粘贴到下面；再加一行它的地址（如 10.0.0.5:7950），"
                             "或它的 rendezvous / 中继记录，每行一条：",
                 bg=ui.SURFACE, fg=ui.MUTED, font=ui.font(11), justify="left", wraplength=self.wrap
                 ).pack(anchor="w", padx=14, pady=(10, 2))
        self.pair_text = tk.Text(panel, height=5, bg=ui.SURFACE_2, fg=ui.FG, insertbackground=ui.FG,
                                 font=ui.font(10, mono=True), relief="flat", highlightthickness=1,
                                 highlightcolor=ui.ACCENT, highlightbackground=ui.BORDER, wrap="char")
        self.pair_text.pack(fill="x", padx=14, pady=(2, 6))
        row = tk.Frame(panel, bg=ui.SURFACE)
        row.pack(fill="x", padx=14)
        tk.Label(row, text="名称", bg=ui.SURFACE, fg=ui.MUTED, font=ui.font(11)).pack(side="left")
        self.pair_name = tk.Entry(row, font=ui.font(12), bg=ui.SURFACE_2, fg=ui.FG, insertbackground=ui.FG,
                                  relief="flat", highlightthickness=1, highlightcolor=ui.ACCENT,
                                  highlightbackground=ui.BORDER, width=24)
        self.pair_name.pack(side="left", padx=(8, 0), ipady=4)
        buttons = tk.Frame(panel, bg=ui.SURFACE)
        buttons.pack(anchor="w", padx=14, pady=(8, 4))
        _button(tk, buttons, "保存并连接", self.pair_now, primary=True).pack(side="left")
        _button(tk, buttons, "取消", self.hide_pair_card).pack(side="left", padx=(8, 0))
        self.pair_status = tk.Label(panel, text="", bg=ui.SURFACE, fg=ERROR, font=ui.font(11), justify="left",
                                    wraplength=self.wrap)
        self.pair_status.pack(anchor="w", padx=14, pady=(0, 12))
        panel.pack(fill="x", padx=16, pady=(8, 0), before=self.body)

    def _did_row(self, parent, label: str, did: str) -> None:
        tk = self.tk
        tk.Label(parent, text=label, bg=ui.SURFACE, fg=ui.MUTED, font=ui.font(11)).pack(anchor="w", padx=14,
                                                                                      pady=(8, 0))
        row = tk.Frame(parent, bg=ui.SURFACE)
        row.pack(fill="x", padx=14)
        e = tk.Entry(row, font=ui.font(10, mono=True), bg=ui.SURFACE_2, fg=ui.FG, relief="flat",
                     readonlybackground=ui.SURFACE_2)
        e.insert(0, did)
        e.configure(state="readonly")
        e.pack(side="left", fill="x", expand=True, ipady=4)
        _button(tk, row, "复制", lambda: self.copy(did)).pack(side="left", padx=(6, 0))

    def _secret(self, parent):
        e = self.tk.Entry(parent, show="\u2022", width=16, font=ui.font(12), bg=ui.SURFACE_2, fg=ui.FG,
                          insertbackground=ui.FG, relief="flat", highlightthickness=1, highlightcolor=ui.ACCENT,
                          highlightbackground=ui.BORDER)
        e.pack(side="left", padx=(0, 6), ipady=4)
        return e

    def copy(self, text: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    def hide_pair_card(self) -> None:
        self.pair_panel.pack_forget()

    def set_passphrase_now(self) -> None:
        p1, p2 = self.pair_pass1.get(), self.pair_pass2.get()
        self.pair_pass1.delete(0, "end")
        self.pair_pass2.delete(0, "end")
        try:
            self.hub.set_passphrase(p1, p2)
        except DialogError as e:
            self.pair_status.configure(text=str(e))
            return
        text, name = self.pair_text.get("1.0", "end"), self.pair_name.get()
        self.show_pair_card()  # now with the operator DID to copy
        self.pair_text.insert("1.0", text.rstrip("\n"))
        self.pair_name.insert(0, name)
        self.pump()

    def pair_now(self) -> None:
        try:
            self.hub.pair(self.pair_text.get("1.0", "end"), self.pair_name.get())
        except DialogError as e:
            self.pair_status.configure(text=str(e))
            return
        self.hide_pair_card()
        self._reset_stream()

    # -- the structural view -------------------------------------------------------------------------

    def toggle_view(self) -> None:
        self.view_open = not self.view_open
        if self.view_open:
            self.view_panel.pack(fill="x", padx=16, pady=(8, 0), before=self.body)
            self._view_shown = None
            self._update_view()
        else:
            self.view_panel.pack_forget()
        self._chrome()

    def _update_view(self) -> None:
        if not self.view_open:
            return
        lines = self.model.view_lines()
        if lines == self._view_shown:
            return
        self._view_shown = lines
        t = self.view_text
        t.configure(state="normal")
        t.delete("1.0", "end")
        for i, line in enumerate(lines):
            t.insert("end", line + "\n", ("focus",) if line.endswith("★") and i else ())
        t.configure(state="disabled")

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
        # The worker holds plain values only: a Tk object it held could be freed
        # on its thread, and Tk must only be touched from the UI thread.
        model, results, ref, key = self.model, self._results, card.msg.ref, card.msg.key

        def work():
            try:
                model.approve(ref, passphrase, again)
                results.put((key, None))
            except DialogError as e:
                results.put((key, str(e)))
            except Exception as e:  # noqa: BLE001 - shown on the card; nothing was signed
                results.put((key, f"出错了，没有签名：{type(e).__name__}: {e}"))

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
        self.hub.refresh_others()
        if self.model.passphrase_set != self._passphrase_set:
            self._passphrase_set = self.model.passphrase_set
            for card in self.cards.values():  # the fields an open approval card asks for have changed
                if card.msg.kind == APPROVAL and card.msg.state == OPEN:
                    card._controls()
        if changed:
            self._render()
            self._update_view()
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
        elsewhere = self.hub.waiting_elsewhere()
        self.switcher.configure(text=f"  {self.hub.name(self.hub.current)} ▾"
                                     + (f" · 其他节点 {elsewhere} 件待处理" if elsewhere else "") + "  ",
                                fg=ui.WARN if elsewhere else self.switcher.fg)
        self.view_button.configure(text=f"  视界 {'▾' if self.view_open else '▸'}  ")
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
            # Free the Tk variable here, on the UI thread, while Tk is alive: left
            # to the garbage collector it could be freed on any thread, after.
            self.provider = None
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
        hub = NodeHub(controller, backend.operator_key, backend.app_identity,
                      NodeBook(backend.home / "nodes.json"), api_keys=ApiKeys())

        def close():
            try:
                hub.close()
            finally:
                backend.stop()

        root = tk.Tk()
        window = Window(root, hub, on_close=close)
        ui.apply_glass(root)
    except BaseException:
        backend.stop()
        raise
    window.run()
    # Tk objects sit in reference cycles; freed by the cyclic collector on some
    # other thread, Tcl aborts. Collect them here, on the thread that made them.
    del window, root
    gc.collect()
    return 0


__all__ = ["POLL_MS", "Window", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
