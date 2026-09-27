"""Structural guard: the perception package takes no screenshots and touches no
other process's memory.

Perception is AX + the Direct Inspection Buffer only. These checks keep it that
way mechanically, so a later change cannot quietly add a frame grab, an image
library, or a process-memory read to the perception path:

  * no module in ``secdogie_agent/perception`` imports an image, screen-capture,
    or native-call library;
  * no code there names a capture API or a process-memory API (docstrings, which
    explain what is *not* done, are exempt);
  * importing the package in a fresh interpreter loads none of those libraries.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import secdogie_agent.perception as perception

PACKAGE_DIR = Path(perception.__file__).parent

BANNED_MODULES = {
    # images / vision
    "PIL", "cv2", "numpy", "imageio", "skimage",
    # screen capture / desktop automation
    "mss", "pyautogui", "pyscreenshot", "Quartz", "AppKit", "win32gui", "win32ui", "uiautomation",
    # native calls (the route to process-memory APIs)
    "ctypes", "cffi",
}

BANNED_NAMES = {
    # capture APIs
    "ImageGrab", "CGWindowListCreateImage", "BitBlt", "PrintWindow", "screencapture", "grab_screen",
    # process-memory APIs
    "ReadProcessMemory", "WriteProcessMemory", "OpenProcess", "process_vm_readv",
    "process_vm_writev", "ptrace",
}


def _modules():
    files = sorted(PACKAGE_DIR.glob("*.py"))
    assert files, "perception package has no modules?"
    return files


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def test_no_image_capture_or_native_call_imports():
    offenders = []
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            for name in names:
                if name.split(".")[0] in BANNED_MODULES:
                    offenders.append(f"{path.name}: import {name}")
    assert offenders == []


def test_no_capture_or_process_memory_api_is_named_in_code():
    offenders = []
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in BANNED_NAMES:
                offenders.append(f"{path.name}: {node.id}")
            elif isinstance(node, ast.Attribute) and node.attr in BANNED_NAMES:
                offenders.append(f"{path.name}: .{node.attr}")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and id(node) not in docstrings:
                if any(bad in node.value for bad in BANNED_NAMES) or "/proc/" in node.value:
                    offenders.append(f"{path.name}: {node.value!r}")
    assert offenders == []


def test_importing_perception_loads_no_image_or_capture_library():
    probe = (
        "import sys, secdogie_agent.perception\n"
        f"banned = {sorted(BANNED_MODULES)!r}\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] in banned))\n"
    )
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"
