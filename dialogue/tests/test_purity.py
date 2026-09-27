"""Red line 1, as a test: the Dialogue App is a consumer, signer and dialogue
peer. No module in the package may import screen capture, OCR, input
injection, raw memory / FFI access, or the Agent's perception layer. It renders
what the Agent sends; it perceives nothing itself."""
from __future__ import annotations

import ast
from pathlib import Path

import secdogie_dialogue

FORBIDDEN = {
    # screen capture / images / OCR
    "PIL", "mss", "pyscreenshot", "cv2", "pytesseract", "easyocr", "Quartz", "AppKit",
    # input injection / GUI automation
    "pyautogui", "pynput", "keyboard", "mouse", "win32api", "win32gui", "win32con", "win32process",
    # raw memory / FFI
    "ctypes", "cffi", "mmap",
    # the Agent's own perception and control
    "secdogie_agent",
}

PKG = Path(secdogie_dialogue.__file__).parent


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module.split(".")[0])
    return out


def test_no_module_imports_perception_capture_injection_or_ffi():
    modules = sorted(PKG.rglob("*.py"))
    assert modules, "package source not found"
    bad = {str(p.relative_to(PKG)): sorted(_imports(p) & FORBIDDEN) for p in modules}
    assert {k: v for k, v in bad.items() if v} == {}


def test_the_scan_would_catch_a_forbidden_import(tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text("import ctypes\nfrom PIL import ImageGrab\nfrom secdogie_agent.perception import x\n")
    assert _imports(probe) & FORBIDDEN == {"ctypes", "PIL", "secdogie_agent"}
