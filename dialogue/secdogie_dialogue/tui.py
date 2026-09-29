"""The Dialogue App's screen (Textual): a thin view over ``AppController``.

Layout (DESIGN.zh.md §3): the conversation on the left; on the right the
structural view above the Gate 2 console; a status line and one command line
at the bottom. Everything shown comes from the controller's plain-text views
and is rendered as ``rich.text.Text`` -- never parsed as markup -- because it
quotes node text, which quotes arbitrary applications' UI.

Commands (type into the bottom line, Enter):

    <text> / <n>          answer the oldest open question (free text, or option n)
    /approve  /deny       the Gate 2 challenge on screen (approve asks for the
                          operator passphrase; the key is dropped after signing)
    /confirm  /dismiss    the memory offer on screen
    /goal <task>          add a goal        /stop|/pause|/resume <goal_id>
    /retract <memory_id>  retract a memory  /resync   /quit

``/approve`` and ``/confirm`` act on exactly the item displayed when the
command was typed, never on "whatever is first" -- if it went away meanwhile,
nothing is signed. There is no key binding that approves.

Optional: install with the ``[tui]`` extra. Only this module imports Textual.
"""
from __future__ import annotations

from collections.abc import Callable

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from .app import AppController, AppError
from .guard import GuardRefusal, review_challenge

REFRESH_EVERY = 0.2

HELP = ("answer: type text or an option number · /approve /deny · /confirm /dismiss · "
        "/goal <task> · /stop|/pause|/resume <id> · /retract <id> · /resync · /quit")


def _text(lines: list[str]) -> Text:
    return Text("\n".join(lines))


class PassphraseScreen(ModalScreen[bytes | None]):
    """Asks for the operator passphrase for one signature."""

    BINDINGS = [Binding("escape", "cancel", "cancel")]

    def __init__(self, prompt: str):
        super().__init__()
        self._prompt = prompt

    def compose(self) -> ComposeResult:
        with Vertical(id="pass-box"):
            yield Static(Text(self._prompt), id="pass-prompt")
            yield Input(password=True, placeholder="operator passphrase", id="passphrase")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(event.value.encode("utf-8") if event.value else None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class DialogueApp(App):
    """``unlock_with(passphrase) -> Identity`` opens the operator keystore
    (None when no keystore is configured: /approve then refuses)."""

    TITLE = "secdogie dialogue"
    CSS = """
    #main { height: 1fr; }
    #left { width: 1fr; border: round $primary; }
    #right { width: 1fr; }
    #view-box { height: 3fr; border: round $secondary; }
    #gate-box { height: 2fr; border: round $warning; }
    #status { height: 1; background: $boost; }
    #events { height: 3; color: $text-muted; }
    #pass-box { width: 70; height: auto; border: thick $warning; padding: 1 2; background: $panel; }
    PassphraseScreen { align: center middle; }
    """
    BINDINGS = [Binding("ctrl+q", "quit", "quit")]

    def __init__(self, controller: AppController, *, unlock_with: Callable[[bytes], object] | None = None):
        super().__init__()
        self.ctl = controller
        self.unlock_with = unlock_with
        self._seen_version = -1
        self._shown_challenge: str | None = None
        self._shown_memory: str | None = None
        self.message = ""  # the last command's outcome, shown on the status line

    def compose(self) -> ComposeResult:
        with Horizontal(id="main"):
            with VerticalScroll(id="left"):
                yield Static(Text(""), id="convo")
            with Vertical(id="right"):
                with VerticalScroll(id="view-box"):
                    yield Static(Text(""), id="view")
                with VerticalScroll(id="gate-box"):
                    yield Static(Text(""), id="gate")
        yield Static(Text(""), id="events")
        yield Static(Text(""), id="status")
        yield Input(placeholder=HELP, id="command")

    def on_mount(self) -> None:
        self.query_one("#left").border_title = "① dialogue"
        self.query_one("#view-box").border_title = "② structural view (no pixels)"
        self.query_one("#gate-box").border_title = "③ Gate 2 · memory"
        self.query_one("#command", Input).focus()
        self.set_interval(REFRESH_EVERY, self.refresh_panels)
        self.refresh_panels()

    # -- rendering -------------------------------------------------------------

    def refresh_panels(self) -> None:
        self.ctl.tick()
        if self.ctl.version != self._seen_version:
            self._seen_version = self.ctl.version
            self.query_one("#convo", Static).update(_text(self.ctl.conversation_lines() or ["(no dialogue yet)"]))
            self.query_one("#view", Static).update(_text(self.ctl.inspector_lines()))
            self.query_one("#events", Static).update(_text(list(self.ctl.events())[-3:]))
        self.query_one("#gate", Static).update(_text(self._gate_lines()))  # the expiry countdown moves
        status = self.ctl.status_line() + (f"  ·  {self.message}" if self.message else "")
        self.query_one("#status", Static).update(Text(status))

    def _gate_lines(self) -> list[str]:
        challenges, memories = self.ctl.challenges(), self.ctl.memories()
        lines: list[str] = []
        if challenges:
            pc = challenges[0]
            self._shown_challenge = pc.challenge.challenge_id
            lines += self.ctl.challenge_lines(pc)
            lines.append("  type /approve or /deny" if pc.review.signable else "  type /deny")
            if len(challenges) > 1:
                lines.append(f"  (+{len(challenges) - 1} more waiting)")
        else:
            self._shown_challenge = None
            lines.append("no Gate 2 challenge waiting")
        lines.append("")
        if memories:
            m = memories[0]
            self._shown_memory = m.packet.memory_id
            lines += self.ctl.memory_lines(m)
            lines.append("  [/confirm] remember it  [/dismiss] leave it unconfirmed" if m.confirmable
                         else "  cannot be confirmed: [/dismiss]")
        else:
            self._shown_memory = None
            lines.append("no memory waiting for confirmation")
        return lines

    # -- commands --------------------------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        line = event.value.strip()
        event.input.value = ""
        if not line:
            return
        try:
            self.message = self.run_command(line) or ""
        except (AppError, GuardRefusal, ValueError) as e:
            self.message = f"refused: {e}"
        self.refresh_panels()

    def run_command(self, line: str) -> str | None:
        if not line.startswith("/"):
            return self._answer(line)
        cmd, _, arg = line[1:].partition(" ")
        arg = arg.strip()
        if cmd == "approve":
            return self._approve()
        if cmd == "deny":
            cid = self._require(self._shown_challenge, "no challenge on screen")
            self.ctl.deny(cid)
            return f"denied {cid}"
        if cmd == "confirm":
            mid = self._require(self._shown_memory, "no memory offer on screen")
            self.ctl.confirm_memory(mid)
            return "confirmation sent"
        if cmd == "dismiss":
            self.ctl.dismiss_memory(self._require(self._shown_memory, "no memory offer on screen"))
            return "left unconfirmed"
        if cmd == "goal":
            pkt = self.ctl.add_goal(self._require(arg, "usage: /goal <task>"))
            return f"goal {pkt.goal_id} requested"
        if cmd in ("stop", "pause", "resume"):
            getattr(self.ctl, cmd)(self._require(arg, f"usage: /{cmd} <goal_id>"))
            return f"{cmd} requested"
        if cmd == "retract":
            self.ctl.retract_memory(self._require(arg, "usage: /retract <memory_id>"))
            return "retraction requested"
        if cmd == "resync":
            self.ctl.request_resync()
            return "asked for a full view"
        if cmd == "quit":
            self.exit()
            return None
        raise AppError(f"unknown command /{cmd}; {HELP}")

    @staticmethod
    def _require(value, why: str):
        if not value:
            raise AppError(why)
        return value

    def _answer(self, line: str) -> str:
        pending = self.ctl.conversation.pending()
        if not pending:
            raise AppError("no open question to answer")
        probe = pending[0]
        if line.isdigit() and probe.suggested_options:
            self.ctl.answer(probe.probe_id, option=int(line))
        else:
            self.ctl.answer(probe.probe_id, line)
        return "answer sent"

    def _approve(self) -> str | None:
        cid = self._require(self._shown_challenge, "no challenge on screen")
        if self.unlock_with is None:
            raise AppError("no operator keystore configured; start with --operator-keystore")
        # Review before asking for the passphrase: nothing unsignable prompts.
        pc = next((p for p in self.ctl.challenges() if p.challenge.challenge_id == cid), None)
        if pc is None:
            raise AppError("that challenge is no longer pending")
        review = review_challenge(pc.challenge, peer_did=self.ctl.peer_did)
        if not review.signable:
            raise GuardRefusal("; ".join(review.problems))

        def signed(passphrase: bytes | None) -> None:
            if not passphrase:
                self.message = "approval cancelled; nothing signed"
            else:
                try:
                    self.ctl.approve(cid, lambda: self.unlock_with(passphrase))
                    self.message = f"approved {cid}"
                except Exception as e:  # noqa: BLE001 - wrong passphrase, expiry, ...: nothing was signed
                    self.message = f"not signed: {e}"
            self.refresh_panels()

        self.push_screen(PassphraseScreen(f"Sign Gate 2 challenge {cid}? Operator passphrase:"), signed)
        return None


def run_tui(controller: AppController, *, unlock_with=None) -> None:
    DialogueApp(controller, unlock_with=unlock_with).run()


__all__ = ["DialogueApp", "PassphraseScreen", "run_tui", "HELP"]
