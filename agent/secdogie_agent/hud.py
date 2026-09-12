"""Persistent operator HUD for --gui.

Windowed (console=False) builds used to destroy every tk dialog after Start /
plan approval, then block on the model HTTP call with no window. That is the
'latent background process' report: the exe is alive, nothing is on screen.

This console stays up for the whole run. The agent loop runs on a worker
thread; tkinter stays on the main thread. High-risk confirms are answered
here (never a second root). On Windows the HWND is WS_EX_NOACTIVATE so the
HUD does not steal focus from the app being driven, and it stays on the
taskbar (not a tool window) so it cannot vanish into the tray.
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
        except Exception:
            self._root = None
            return

        root = tk.Tk()
        self._tk = tk
        root.title("secdogie · operator")
        root.configure(bg=ui.BG)
        root.resizable(True, True)
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        pad = tk.Frame(root, bg=ui.BG, padx=14, pady=12)
        pad.pack(fill="both", expand=True)

        head = tk.Frame(pad, bg=ui.BG)
        head.pack(fill="x")
        tk.Label(
            head,
            text="SECDOGIE",
            bg=ui.BG,
            fg=ui.MUTED,
            font=ui.font(9, bold=True),
        ).pack(side="left")
        tk.Label(
            head,
            text="operator",
            bg=ui.BG,
            fg=ui.SUBTLE,
            font=ui.font(9),
        ).pack(side="left", padx=(8, 0))

        self._stop_btn = tk.Button(
            head,
            text="STOP",
            command=self._on_stop,
            bg=ui.DENY,
            fg=ui.BG,
            activebackground=ui.DENY,
            activeforeground=ui.BG,
            relief="flat",
            font=ui.font(9, bold=True),
            padx=12,
            pady=4,
            cursor="hand2",
            highlightthickness=0,
            bd=0,
        )
        self._stop_btn.pack(side="right")

        status_row = tk.Frame(pad, bg=ui.BG)
        status_row.pack(fill="x", pady=(10, 8))
        self._dot = tk.Canvas(
            status_row, width=10, height=10, bg=ui.BG, highlightthickness=0, bd=0
        )
        self._dot.pack(side="left", pady=2)
        self._dot_id = self._dot.create_oval(1, 1, 9, 9, fill=ui.WARN, outline="")
        self._status = tk.Label(
            status_row,
            text="Starting…",
            bg=ui.BG,
            fg=ui.FG,
            font=ui.font(11, bold=True),
            anchor="w",
        )
        self._status.pack(side="left", fill="x", expand=True, padx=(8, 0))

        tk.Label(
            pad,
            text="TASK",
            bg=ui.BG,
            fg=ui.SUBTLE,
            font=ui.font(8, bold=True),
            anchor="w",
        ).pack(fill="x")
        task_lbl = tk.Label(
            pad,
            text=self.task or "(no task)",
            bg=ui.BG,
            fg=ui.FG,
            font=ui.font(10),
            wraplength=400,
            justify="left",
            anchor="w",
        )
        task_lbl.pack(fill="x", pady=(2, 10))

        tk.Label(
            pad,
            text="LOG",
            bg=ui.BG,
            fg=ui.SUBTLE,
            font=ui.font(8, bold=True),
            anchor="w",
        ).pack(fill="x")
        log = scrolledtext.ScrolledText(
            pad,
            height=14,
            wrap="word",
            bg=ui.SURFACE,
            fg=ui.FG,
            insertbackground=ui.FG,
            relief="flat",
            font=ui.font(9, mono=True),
            highlightthickness=1,
            highlightbackground=ui.BORDER,
            highlightcolor=ui.BORDER,
            bd=0,
            state="disabled",
        )
        log.pack(fill="both", expand=True, pady=(4, 8))
        self._log = log

        self._confirm_fr = tk.Frame(pad, bg=ui.SURFACE_2, padx=10, pady=8)
        self._confirm_lbl = tk.Label(
            self._confirm_fr,
            text="",
            bg=ui.SURFACE_2,
            fg=ui.FG,
            font=ui.font(10),
            wraplength=380,
            justify="left",
            anchor="w",
        )
        self._confirm_lbl.pack(fill="x", pady=(0, 8))
        btns = tk.Frame(self._confirm_fr, bg=ui.SURFACE_2)
        btns.pack(anchor="e")
        tk.Button(
            btns,
            text="Skip",
            command=lambda: self._answer(False),
            bg=ui.SURFACE,
            fg=ui.FG,
            relief="flat",
            font=ui.font(9),
            padx=10,
            pady=4,
            highlightthickness=0,
            bd=0,
        ).pack(side="right", padx=(6, 0))
        self._allow_btn = tk.Button(
            btns,
            text="Allow",
            command=lambda: self._answer(True),
            bg=ui.ACCENT,
            fg=ui.ACCENT_FG,
            relief="flat",
            font=ui.font(9, bold=True),
            padx=12,
            pady=4,
            highlightthickness=0,
            bd=0,
        )
        self._allow_btn.pack(side="right")

        self._root = root
        root.update_idletasks()
        w, h = 440, 540
        try:
            sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
            root.geometry(f"{w}x{h}+{max(16, sw - w - 28)}+{max(16, sh - h - 72)}")
        except Exception:
            root.geometry(f"{w}x{h}")
        _no_activate(root)
        root.after(50, self._pump)
        self._append("console ready — this window stays up until you close it")

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
                self._set_status("waiting", "Approve the plan")
                self._allow_btn.configure(text="Looks good — go")
            elif payload.get("kind") == "ask":
                self._set_status("waiting", "The model is asking")
                self._allow_btn.configure(text="Yes, continue")
            elif payload.get("high_risk"):
                self._set_status("waiting", "HIGH-RISK — confirm")
                self._allow_btn.configure(text="Allow")
            else:
                self._set_status("waiting", "Confirm this step")
                self._allow_btn.configure(text="Allow")
            self._confirm_lbl.configure(text=prompt[:800] or "(no prompt)")
            self._confirm_fr.pack(fill="x", pady=(0, 4))
            self._append("waiting for operator: " + prompt.replace("\n", " ")[:180])
            return
        if kind == "stop":
            self._set_status("stopping", "Stopping…")
            self._append("stop requested")
            return
        if kind == "start":
            self._set_status("running", "Starting")
            self._append("task: " + str(payload.get("task") or self.task)[:240])
            return
        if kind == "briefing":
            self._set_status("running", "Plan ready — running")
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
            self._set_status("capture", f"Step {step} · capturing")
            self._append(f"step {step}  capture  {w}×{h}")
            return
        if kind == "model":
            step = payload.get("step")
            self._set_status("model", f"Step {step} · asking the model")
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
            self._set_status("done", "Done")
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
            0: ("done", "Done"),
            2: ("stopped", "Cancelled"),
            3: ("stopped", "Stopped (max steps)"),
            4: ("error", "Cannot capture the screen"),
            5: ("stopped", "Stopped"),
            6: ("error", "Stalled"),
            7: ("error", "Focus failed"),
        }
        phase, text = labels.get(rc, ("error", f"Exit {rc}"))
        if rc == 1:
            phase, text = "error", "The model did not answer"
        self._set_status(phase, text)
        self._append(f"finished  rc={rc}")
        self._hide_confirm(False)
        if self._stop_btn is not None:
            self._stop_btn.configure(
                text="Close", command=self._destroy, bg=ui.ACCENT, fg=ui.ACCENT_FG,
                activebackground=ui.ACCENT, activeforeground=ui.ACCENT_FG,
            )

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
        self._set_status("stopping", "Stopping…")

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


def _no_activate(root) -> None:
    """Windows: stay visible on the taskbar, never steal foreground.

    WS_EX_TOOLWINDOW is deliberately not set — that hides the window from the
    taskbar and is exactly the 'latent background process' look.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()
        GWL_EXSTYLE = -20
        WS_EX_NOACTIVATE = 0x08000000
        WS_EX_TOPMOST = 0x00000008
        SWP_NOSIZE = 0x0001
        SWP_NOMOVE = 0x0002
        SWP_NOACTIVATE = 0x0010
        SWP_SHOWWINDOW = 0x0040
        HWND_TOPMOST = -1
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE, (style | WS_EX_NOACTIVATE | WS_EX_TOPMOST) & ~0x00000080
        )
        user32.SetWindowPos(
            hwnd,
            HWND_TOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )
    except Exception:
        pass
