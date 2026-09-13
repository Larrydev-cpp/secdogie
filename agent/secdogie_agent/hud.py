"""Persistent operator HUD for --gui.

Windowed (console=False) builds used to destroy every tk dialog after Start /
plan approval, then block on the model HTTP call with no window. That is the
'latent background process' report: the exe is alive, nothing is on screen.

This console stays up for the whole run. The agent loop runs on a worker
thread; tkinter stays on the main thread. High-risk confirms are answered
here (never a second root). On Windows the HWND is WS_EX_NOACTIVATE so the
HUD does not steal focus from the app being driven, and it stays on the
taskbar (not a tool window) so it cannot vanish into the tray. On Darwin
the Aqua utility style is noActivates for the same reason.

Tk() is inside the import try: CI / ssh sessions have no DISPLAY, and a
headless construct must not TclError.
"""
from __future__ import annotations

import queue
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from . import theme as ui


class OperatorBridge:
    """Thread-safe queue between the worker loop and the HUD (or tests)."""

    def __init__(self) -> None:
        self.events: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._finished = threading.Event()
        self.rc = 1

    def should_stop(self) -> bool:
        return self._stop.is_set()

    def request_stop(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        self.events.put(("stop", {}))

    def emit(self, kind: str, payload: dict | None = None) -> None:
        if kind == "finished":
            try:
                self.rc = int((payload or {}).get("rc", self.rc))
            except (TypeError, ValueError):
                pass
            self._finished.set()
        self.events.put((kind, payload or {}))

    def confirm(
        self,
        prompt: str,
        high_risk: bool = False,
        *,
        timeout: float = 600.0,
        kind: str = "action",
        extra: dict | None = None,
    ) -> bool:
        if self._stop.is_set():
            return False
        reply = threading.Event()
        box: dict[str, Any] = {
            "ok": False,
            "done": reply,
            "prompt": prompt,
            "high_risk": high_risk,
            "kind": kind,
        }
        if extra:
            box.update(extra)
        self.events.put(("confirm", box))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._stop.is_set():
                return False
            if reply.wait(0.1):
                return bool(box["ok"])
        return False

    def answer(self, box: dict, ok: bool) -> None:
        box["ok"] = bool(ok)
        done = box.get("done")
        if done is not None:
            done.set()


class OperatorHud:
    """Tk operator console. Construct on the main thread."""

    def __init__(self, task: str, *, bridge: OperatorBridge | None = None) -> None:
        self.task = (task or "").strip()
        self.bridge = bridge or OperatorBridge()
        self._root = None
        self._status = None
        self._dot = None
        self._log = None
        self._stop_btn = None
        self._grant_btn = None
        self._pad_lbl = None
        self._confirm_fr = None
        self._confirm_lbl = None
        self._pending: dict | None = None
        self._phase = "starting"
        self._closed = False
        self._build()

    # -- loop wiring ----------------------------------------------------------

    def attach(self, config) -> None:
        prev_stop = config.should_stop

        def _stop() -> bool:
            if self.bridge.should_stop():
                return True
            return bool(prev_stop()) if prev_stop is not None else False

        config.should_stop = _stop
        config.on_event = self.bridge.emit
        config.approve_plan = self.approve_plan
        config.approve_action = self.approve_action
        config.ask_operator = self.ask
        config.notify_operator = self.notify

    def should_stop(self) -> bool:
        return self.bridge.should_stop()

    def emit(self, kind: str, payload: dict | None = None) -> None:
        self.bridge.emit(kind, payload)

    def approve_plan(self, task: str, plan: str) -> bool:
        return self.bridge.confirm(
            plan, high_risk=False, kind="plan", extra={"task": task, "plan": plan}
        )

    def approve_action(self, prompt: str, high_risk: bool = False) -> bool:
        return self.bridge.confirm(prompt, high_risk=high_risk, kind="action")

    def ask(self, question: str) -> bool:
        return self.bridge.confirm(question, high_risk=False, kind="ask")

    def notify(self, title: str, message: str) -> None:
        self.bridge.emit("error", {"title": title, "message": message})

    def run_worker(self, fn: Callable[[], int]) -> int:
        """Run `fn` on a worker; block the main thread on the HUD."""
        box = {"rc": 1}

        def _go() -> None:
            try:
                box["rc"] = int(fn())
            except Exception as e:  # pragma: no cover - surfaced in the HUD
                box["rc"] = 1
                self.bridge.emit("error", {"title": "secdogie-agent", "message": str(e)})
            finally:
                if not self.bridge._finished.is_set():
                    self.bridge.emit("finished", {"rc": box["rc"]})

        worker = threading.Thread(target=_go, name="secdogie-loop", daemon=True)
        worker.start()
        if self._root is None:
            worker.join(timeout=3600)
            return int(box["rc"])
        try:
            self._root.mainloop()
        finally:
            self.bridge.request_stop()
            worker.join(timeout=2.0)
        return int(box["rc"])

    # -- window ---------------------------------------------------------------

    def _build(self) -> None:
        try:
            import tkinter as tk
            from tkinter import scrolledtext

            root = tk.Tk()
        except Exception:
            self._root = None
            return

        self._tk = tk
        root.title("secdogie · 操作台")
        root.configure(bg=ui.BG)
        root.resizable(True, True)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        pad = tk.Frame(root, bg=ui.BG, padx=16, pady=14)
        pad.pack(fill="both", expand=True)

        head = tk.Frame(pad, bg=ui.BG)
        head.pack(fill="x")
        titles = tk.Frame(head, bg=ui.BG)
        titles.pack(side="left", fill="x", expand=True)
        tk.Label(
            titles,
            text="secdogie",
            bg=ui.BG,
            fg=ui.FG,
            font=ui.font(18, bold=True),
        ).pack(anchor="w")
        tk.Label(
            titles,
            text="操作台  operator",
            bg=ui.BG,
            fg=ui.MUTED,
            font=ui.font(11),
        ).pack(anchor="w")

        stop_wrap = tk.Frame(head, bg=ui.DENY, height=ui.TAP, width=88)
        stop_wrap.pack(side="right")
        stop_wrap.pack_propagate(False)
        self._stop_btn = tk.Button(
            stop_wrap,
            text="停止",
            command=self._on_stop,
            bg=ui.DENY,
            fg="#ffffff",
            activebackground=ui.DENY,
            activeforeground="#ffffff",
            relief="flat",
            font=ui.font(15, bold=True),
            cursor="hand2",
            highlightthickness=0,
            bd=0,
        )
        self._stop_btn.pack(fill="both", expand=True)

        status_card = tk.Frame(pad, bg=ui.SURFACE, padx=14, pady=12)
        status_card.pack(fill="x", pady=(14, 8))
        status_row = tk.Frame(status_card, bg=ui.SURFACE)
        status_row.pack(fill="x")
        self._dot = tk.Canvas(
            status_row, width=12, height=12, bg=ui.SURFACE, highlightthickness=0, bd=0
        )
        self._dot.pack(side="left", pady=2)
        self._dot_id = self._dot.create_oval(2, 2, 10, 10, fill=ui.WARN, outline="")
        self._status = tk.Label(
            status_row,
            text="正在启动…  Starting",
            bg=ui.SURFACE,
            fg=ui.FG,
            font=ui.font(13, bold=True),
            anchor="w",
        )
        self._status.pack(side="left", fill="x", expand=True, padx=(8, 0))

        self._pad_lbl = tk.Label(
            status_card,
            text="",
            bg=ui.SURFACE,
            fg=ui.MUTED,
            font=ui.font(11),
            anchor="w",
            wraplength=360,
            justify="left",
        )
        self._pad_lbl.pack(fill="x", pady=(8, 0))

        if sys.platform == "darwin":
            grant_wrap = tk.Frame(status_card, bg=ui.ACCENT, height=ui.TAP)
            grant_wrap.pack(fill="x", pady=(10, 0))
            grant_wrap.pack_propagate(False)
            self._grant_btn = tk.Button(
                grant_wrap,
                text="授权  Grant",
                command=self._on_grant,
                bg=ui.ACCENT,
                fg=ui.ACCENT_FG,
                activebackground=ui.ACCENT,
                activeforeground=ui.ACCENT_FG,
                relief="flat",
                font=ui.font(15, bold=True),
                cursor="hand2",
                highlightthickness=0,
                bd=0,
            )
            self._grant_btn.pack(fill="both", expand=True)
            self._refresh_pad_chip()

        task_card = tk.Frame(pad, bg=ui.SURFACE, padx=14, pady=12)
        task_card.pack(fill="x", pady=(0, 8))
        tk.Label(
            task_card,
            text="任务  TASK",
            bg=ui.SURFACE,
            fg=ui.SUBTLE,
            font=ui.font(10, bold=True),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            task_card,
            text=self.task or "（无任务）",
            bg=ui.SURFACE,
            fg=ui.FG,
            font=ui.font(13),
            wraplength=400,
            justify="left",
            anchor="w",
        ).pack(fill="x", pady=(4, 0))

        log_card = tk.Frame(pad, bg=ui.SURFACE, padx=10, pady=10)
        log_card.pack(fill="both", expand=True, pady=(0, 8))
        tk.Label(
            log_card,
            text="日志  LOG",
            bg=ui.SURFACE,
            fg=ui.SUBTLE,
            font=ui.font(10, bold=True),
            anchor="w",
        ).pack(fill="x", padx=4)
        log = scrolledtext.ScrolledText(
            log_card,
            height=14,
            wrap="word",
            bg=ui.SURFACE,
            fg=ui.FG,
            insertbackground=ui.FG,
            relief="flat",
            font=ui.font(11, mono=True),
            highlightthickness=0,
            bd=0,
            state="disabled",
        )
        log.pack(fill="both", expand=True, pady=(4, 0))
        self._log = log

        self._confirm_fr = tk.Frame(pad, bg=ui.SURFACE_2, padx=14, pady=12)
        self._confirm_lbl = tk.Label(
            self._confirm_fr,
            text="",
            bg=ui.SURFACE_2,
            fg=ui.FG,
            font=ui.font(13),
            wraplength=380,
            justify="left",
            anchor="w",
        )
        self._confirm_lbl.pack(fill="x", pady=(0, 10))
        btns = tk.Frame(self._confirm_fr, bg=ui.SURFACE_2)
        btns.pack(fill="x")
        skip_wrap = tk.Frame(btns, bg=ui.SURFACE, height=ui.TAP, width=96)
        skip_wrap.pack(side="right", padx=(8, 0))
        skip_wrap.pack_propagate(False)
        tk.Button(
            skip_wrap,
            text="跳过",
            command=lambda: self._answer(False),
            bg=ui.SURFACE,
            fg=ui.FG,
            relief="flat",
            font=ui.font(14),
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        ).pack(fill="both", expand=True)
        allow_wrap = tk.Frame(btns, bg=ui.ACCENT, height=ui.TAP, width=128)
        allow_wrap.pack(side="right")
        allow_wrap.pack_propagate(False)
        self._allow_btn = tk.Button(
            allow_wrap,
            text="允许",
            command=lambda: self._answer(True),
            bg=ui.ACCENT,
            fg=ui.ACCENT_FG,
            relief="flat",
            font=ui.font(15, bold=True),
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self._allow_btn.pack(fill="both", expand=True)

        self._root = root
        root.update_idletasks()
        w, h = 420, 620
        try:
            sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
            root.geometry(f"{w}x{h}+{max(16, sw - w - 28)}+{max(16, sh - h - 72)}")
        except Exception:
            root.geometry(f"{w}x{h}")
        ui.apply_glass(root)
        ui.apply_hud_behavior(root)
        root.after(50, self._pump)
        self._append("console ready — 此窗口会一直留在屏幕上直到你关闭")

    def _refresh_pad_chip(self) -> None:
        if self._pad_lbl is None:
            return
        try:
            from . import desktop_ax

            g = desktop_ax.query_pad_grants()
        except Exception:
            return
        pad = str(g.get("pad") or "?")
        ax = "开" if g.get("accessibility") else "关"
        rec = "开" if g.get("screen_recording") else "关"
        self._pad_lbl.configure(text=f"触控板 {pad}  ·  辅助功能 {ax}  ·  屏幕录制 {rec}")

    def _on_grant(self) -> None:
        try:
            from . import desktop_ax

            g = desktop_ax.request_pad_grants()
        except Exception as e:
            self._append(f"grant failed: {e}")
            return
        pad = g.get("pad") or "?"
        self._append(
            f"grant  pad={pad}  ax={'on' if g.get('accessibility') else 'off'}  "
            f"screen={'on' if g.get('screen_recording') else 'off'}"
        )
        detail = str(g.get("detail") or "")
        if detail:
            self._append(detail)
        self._refresh_pad_chip()
        self._set_status("running", detail or f"pad {pad}")

    def _pump(self) -> None:
        if self._root is None or self._closed:
            return
        try:
            while True:
                kind, payload = self.bridge.events.get_nowait()
                self._handle(kind, payload)
        except queue.Empty:
            pass
        try:
            self._root.after(50, self._pump)
        except Exception:
            pass

    def _handle(self, kind: str, payload: dict) -> None:
        if kind == "confirm":
            self._pending = payload
            prompt = str(payload.get("prompt") or "")
            if payload.get("kind") == "plan":
                prompt = payload.get("plan") or prompt
                self._set_status("waiting", "批准计划  Approve the plan")
                self._allow_btn.configure(text="看起来好 — 开始")
            elif payload.get("kind") == "ask":
                self._set_status("waiting", "模型在问  The model is asking")
                self._allow_btn.configure(text="是，继续")
            elif payload.get("high_risk"):
                self._set_status("waiting", "高风险 — 确认  HIGH-RISK")
                self._allow_btn.configure(text="允许")
            else:
                self._set_status("waiting", "确认这一步  Confirm")
                self._allow_btn.configure(text="允许")
            self._confirm_lbl.configure(text=prompt[:800] or "(no prompt)")
            self._confirm_fr.pack(fill="x", pady=(0, 4))
            self._append("waiting for operator: " + prompt.replace("\n", " ")[:180])
            return
        if kind == "stop":
            self._set_status("stopping", "正在停止…")
            self._append("stop requested")
            return
        if kind == "start":
            self._set_status("running", "启动中")
            self._append("task: " + str(payload.get("task") or self.task)[:240])
            return
        if kind == "briefing":
            self._set_status("running", "计划就绪 — 运行中")
            text = str(payload.get("text") or "")
            if text:
                self._append(text.replace("\n", " / ")[:240])
            return
        if kind == "busy":
            msg = str(payload.get("message") or "Working…")
            self._set_status("model", msg)
            self._append(msg)
            return
        if kind == "capture":
            w, h = payload.get("width"), payload.get("height")
            step = payload.get("step")
            source = str(payload.get("source") or "")
            if source == "ax-pad":
                self._set_status("capture", f"Step {step} · AX 触控板")
                self._append(f"step {step}  ax-pad  {w}×{h}")
            else:
                self._set_status("capture", f"Step {step} · capturing")
                self._append(f"step {step}  capture  {w}×{h}")
            return
        if kind == "model":
            step = payload.get("step")
            self._set_status("model", f"Step {step} · 询问模型")
            return
        if kind == "action":
            step = payload.get("step")
            k = payload.get("kind")
            why = payload.get("reasoning") or ""
            self._set_status("action", f"Step {step} · {k}")
            line = f"step {step}  {k}"
            if why:
                line += "  — " + str(why)[:160]
            self._append(line)
            return
        if kind == "result":
            self._append(
                f"step {payload.get('step')}  result  {payload.get('result') or ''}"[:240]
            )
            return
        if kind == "done":
            summary = str(payload.get("summary") or "done")
            self._set_status("done", "完成")
            self._append("done: " + summary[:240])
            return
        if kind == "error":
            title = str(payload.get("title") or "error")
            msg = str(payload.get("message") or "")
            self._set_status("error", title)
            self._append(f"error: {title} — {msg}"[:400])
            return
        if kind == "finished":
            rc = payload.get("rc")
            self._on_finished(int(rc) if rc is not None else 1)
            return
        self._append(f"{kind}  {payload}")

    def _on_finished(self, rc: int) -> None:
        self.bridge.rc = rc
        labels = {
            0: ("done", "完成  Done"),
            2: ("stopped", "已取消"),
            3: ("stopped", "已停（步数用尽）"),
            4: ("error", "无法构建触控板 / 无法截屏"),
            5: ("stopped", "已停止"),
            6: ("error", "卡住了"),
            7: ("error", "前台焦点失败"),
        }
        phase, text = labels.get(rc, ("error", f"Exit {rc}"))
        if rc == 1:
            phase, text = "error", "模型没有回答"
        self._set_status(phase, text)
        self._append(f"finished  rc={rc}")
        self._hide_confirm(False)
        if self._stop_btn is not None:
            self._stop_btn.configure(
                text="关闭",
                command=self._destroy,
                bg=ui.ACCENT,
                fg=ui.ACCENT_FG,
                activebackground=ui.ACCENT,
                activeforeground=ui.ACCENT_FG,
            )
            try:
                self._stop_btn.master.configure(bg=ui.ACCENT)
            except Exception:
                pass

    def _answer(self, ok: bool) -> None:
        box = self._pending
        self._pending = None
        self._hide_confirm(False)
        if box is not None:
            self.bridge.answer(box, ok)
            self._append("operator: " + ("allow" if ok else "skip"))

    def _hide_confirm(self, _ok: bool) -> None:
        if self._confirm_fr is not None:
            try:
                self._confirm_fr.pack_forget()
            except Exception:
                pass

    def _on_stop(self) -> None:
        if self.bridge._finished.is_set():
            self._destroy()
            return
        self.bridge.request_stop()
        if self._pending is not None:
            self._answer(False)
        self._set_status("stopping", "正在停止…")

    def _on_close(self) -> None:
        self.bridge.request_stop()
        if self._pending is not None:
            self._answer(False)
        self._destroy()

    def _destroy(self) -> None:
        if self._closed:
            return
        self._closed = True
        root = self._root
        self._root = None
        if root is None:
            return
        try:
            root.destroy()
        except Exception:
            pass

    def _set_status(self, phase: str, text: str) -> None:
        self._phase = phase
        colors = {
            "starting": ui.WARN,
            "running": ui.OK,
            "capture": ui.ACCENT,
            "model": ui.WARN,
            "action": ui.OK,
            "waiting": ui.WARN,
            "stopping": ui.MUTED,
            "stopped": ui.MUTED,
            "done": ui.OK,
            "error": ui.DENY,
        }
        if self._status is not None:
            self._status.configure(text=text)
        if self._dot is not None:
            try:
                self._dot.itemconfigure(self._dot_id, fill=colors.get(phase, ui.ACCENT))
            except Exception:
                pass

    def _append(self, line: str) -> None:
        log = self._log
        if log is None:
            return
        stamp = time.strftime("%H:%M:%S")
        try:
            log.configure(state="normal")
            log.insert("end", f"{stamp}  {line}\n")
            log.see("end")
            log.configure(state="disabled")
        except Exception:
            pass
