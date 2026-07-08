"""A thin wrapper over the `adb` command line -- just the handful of calls the
agent backend needs: take a screenshot, and inject taps/swipes/text/keys.

Nothing here talks to a model or knows about the agent's Action schema; that
mapping lives in backend.py. This layer only turns method calls into `adb`
invocations and surfaces failures as AdbError.
"""
from __future__ import annotations

import shutil
import subprocess

# `input text` uses '%s' to mean a literal space, and the on-device shell
# interprets these metacharacters -- so both must be escaped for typed text to
# arrive intact. (input text is ASCII-only; non-ASCII is handled in backend.py.)
_TEXT_SPACE = "%s"
_SHELL_METACHARS = set(" ()<>|;&*\\~\"'`$#")

# Model-facing key names -> Android keycodes. The vision prompt speaks in
# desktop-ish key names; map the ones that have a phone equivalent, and fall
# back to KEYCODE_<NAME> for anything else (adb accepts named keycodes).
_KEYCODE_ALIASES = {
    "enter": "KEYCODE_ENTER",
    "return": "KEYCODE_ENTER",
    "backspace": "KEYCODE_DEL",
    "delete": "KEYCODE_FORWARD_DEL",
    "del": "KEYCODE_DEL",
    "tab": "KEYCODE_TAB",
    "space": "KEYCODE_SPACE",
    "esc": "KEYCODE_ESCAPE",
    "escape": "KEYCODE_ESCAPE",
    "up": "KEYCODE_DPAD_UP",
    "down": "KEYCODE_DPAD_DOWN",
    "left": "KEYCODE_DPAD_LEFT",
    "right": "KEYCODE_DPAD_RIGHT",
    "home": "KEYCODE_HOME",
    "back": "KEYCODE_BACK",
    "menu": "KEYCODE_MENU",
    "search": "KEYCODE_SEARCH",
    "power": "KEYCODE_POWER",
    "recents": "KEYCODE_APP_SWITCH",
    "appswitch": "KEYCODE_APP_SWITCH",
    "volup": "KEYCODE_VOLUME_UP",
    "voldown": "KEYCODE_VOLUME_DOWN",
}


class AdbError(RuntimeError):
    """An adb invocation failed, timed out, or adb itself isn't installed."""


class Adb:
    def __init__(self, serial: str | None = None, adb_path: str = "adb", timeout: float = 20.0):
        self.serial = serial
        self.adb_path = adb_path
        self.timeout = timeout

    # -- low-level ---------------------------------------------------------
    def _argv(self, args: list[str]) -> list[str]:
        # `-s <serial>` targets one device when several are attached.
        target = ["-s", self.serial] if self.serial else []
        return [self.adb_path, *target, *args]

    def _run(self, args: list[str]) -> bytes:
        """Run `adb <args>` and return raw stdout bytes. Raises AdbError on a
        missing binary, timeout, or non-zero exit."""
        if shutil.which(self.adb_path) is None and "/" not in self.adb_path:
            raise AdbError(
                f"`{self.adb_path}` was not found on PATH. Install the Android "
                "platform-tools (they ship adb) and make sure `adb` runs, or pass "
                "--adb-path. See android/README.md."
            )
        try:
            proc = subprocess.run(
                self._argv(args),
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
        except FileNotFoundError as e:
            raise AdbError(f"could not run adb: {e}") from e
        except subprocess.TimeoutExpired as e:
            raise AdbError(f"adb timed out after {self.timeout}s running: adb {' '.join(args)}") from e
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", "replace").strip()
            raise AdbError(f"adb {' '.join(args)} failed (exit {proc.returncode}): {stderr}")
        return proc.stdout

    def _shell(self, args: list[str]) -> None:
        self._run(["shell", *args])

    # -- device discovery ---------------------------------------------------------
    def list_devices(self) -> list[str]:
        """Serials of devices in the `device` state (not `offline`/`unauthorized`)."""
        out = self._run(["devices"]).decode("utf-8", "replace")
        serials: list[str] = []
        for line in out.splitlines()[1:]:  # first line is the "List of devices attached" header
            line = line.strip()
            if not line or "\t" not in line:
                continue
            serial, state = line.split("\t", 1)
            if state.strip() == "device":
                serials.append(serial.strip())
        return serials

    # -- capture ---------------------------------------------------------
    def screencap_png(self) -> bytes:
        """PNG bytes of the current screen. Uses `exec-out` so the binary PNG
        isn't corrupted by the shell's pty line-ending translation."""
        png = self._run(["exec-out", "screencap", "-p"])
        if not png:
            raise AdbError("screencap returned no data (is the device screen on and unlocked?)")
        return png

    def ui_dump(self) -> str:
        """The current window's UI-automator view hierarchy as XML: the widget
        tree (bounds, text, resource-id, clickable, ...) that lets us target
        real elements instead of guessing pixels. `uiautomator dump /dev/tty`
        prints the XML to stdout followed by a status line, so slice to the
        `<hierarchy>...</hierarchy>` span."""
        raw = self._run(["exec-out", "uiautomator", "dump", "/dev/tty"]).decode("utf-8", "replace")
        start = raw.find("<hierarchy")
        end = raw.rfind("</hierarchy>")
        if start < 0 or end < 0:
            raise AdbError("uiautomator dump returned no hierarchy (some screens block dumping, e.g. secure views)")
        return raw[start : end + len("</hierarchy>")]

    # -- input ---------------------------------------------------------
    def tap(self, x: int, y: int) -> None:
        self._shell(["input", "tap", str(x), str(y)])

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 200) -> None:
        self._shell(["input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)])

    def long_press(self, x: int, y: int, duration_ms: int = 600) -> None:
        # There's no dedicated long-tap in `input`; a zero-distance swipe held
        # for a while is the standard way to trigger a press-and-hold.
        self.swipe(x, y, x, y, duration_ms)

    def text(self, s: str) -> None:
        self._shell(["input", "text", _encode_text(s)])

    def keyevent(self, key: str, longpress: bool = False) -> None:
        code = _resolve_keycode(key)
        args = ["input", "keyevent"]
        if longpress:
            args.append("--longpress")
        args.append(code)
        self._shell(args)

    def open_uri(self, uri: str) -> None:
        self._shell(["am", "start", "-a", "android.intent.action.VIEW", "-d", uri])


def _encode_text(s: str) -> str:
    """Escape a string for `adb shell input text` (ASCII only)."""
    out = []
    for ch in s:
        if ch == " ":
            out.append(_TEXT_SPACE)
        elif ch in _SHELL_METACHARS:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def _resolve_keycode(key: str) -> str:
    k = key.strip().lower()
    if k in _KEYCODE_ALIASES:
        return _KEYCODE_ALIASES[k]
    if len(k) == 1 and k.isalpha():
        return f"KEYCODE_{k.upper()}"
    if len(k) == 1 and k.isdigit():
        return f"KEYCODE_{k}"
    # Already a keycode name, or an unknown name adb will reject clearly.
    return key if key.upper().startswith("KEYCODE_") else f"KEYCODE_{key.upper()}"
