"""Red lines for Studio, as a test: no screen capture or image processing of
any kind (perception is structural), and nothing that starts at login, runs as
a service or schedules itself (the node lives and dies with the window)."""
from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "secdogie_studio"

FORBIDDEN = [
    (r"screenshot|ImageGrab|grabWindow|\.grab\(|\bmss\b|\bPIL\b|\bcv2\b|pytesseract|pyautogui",
     "screen capture or image processing"),
    (r"LaunchAgents|LaunchDaemons|launchctl|systemctl|crontab|schtasks|CurrentVersion\\\\Run|"
     r"\\.config/autostart|Start Menu\\\\Programs\\\\Startup|RegisterServiceCtrlHandler",
     "autostart, services or scheduling"),
    (r"DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP|start_new_session|setsid|os\.fork|daemon\(",
     "detaching the node from the window"),
]


def _code(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    text = re.sub(r'"""[\s\S]*?"""', "", text)  # docstrings may name what the code never does
    return re.sub(r"#.*", "", text)


def test_no_source_file_crosses_a_red_line():
    found = []
    for f in sorted(SRC.rglob("*.py")):
        code = _code(f)
        for rx, what in FORBIDDEN:
            if re.search(rx, code):
                found.append(f"{f.name}: {what}")
    assert found == []


def test_the_scan_would_catch_a_violation():
    probe = "QScreen.grabWindow(0); subprocess.Popen(cmd, start_new_session=True); run('launchctl load')"
    hits = [what for rx, what in FORBIDDEN if re.search(rx, probe)]
    assert hits == ["screen capture or image processing", "autostart, services or scheduling",
                    "detaching the node from the window"]
