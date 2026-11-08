"""Studio speaks Chinese and English: which one, and how to say a thing in it.

The language is the person's choice, kept in ``ui.json`` beside the profile
(it can be chosen before the profile exists). Until they choose, Studio
follows ``SECDOGIE_LANG``, then the system's locale: English only when the
locale says English, Chinese otherwise.

Studio's own words (the interface, its notes, the first meeting) follow the
choice. What the node itself says (a question, a letter) arrives in the
node's words, which are Chinese today.
"""
from __future__ import annotations

import json
import locale
import os
from pathlib import Path

LANGS = ("zh", "en")
DEFAULT = "zh"
UI_FILE = "ui.json"


def pick(lang: str, zh: str, en: str) -> str:
    return en if lang == "en" else zh


def detect(env=None, getlocale=locale.getlocale) -> str:
    env = os.environ if env is None else env
    chosen = env.get("SECDOGIE_LANG", "").strip().lower()
    if chosen in LANGS:
        return chosen
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = env.get(name, "")
        if value:
            return "en" if value.lower().startswith("en") else DEFAULT
    try:
        name = (getlocale()[0] or "").lower()
    except (ValueError, TypeError):
        name = ""
    return "en" if name.startswith("en") or name.startswith("english") else DEFAULT


def load(root: Path, env=None) -> str:
    """The language chosen for the profile at ``root``, else the detected one."""
    try:
        data = json.loads((Path(root) / UI_FILE).read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("lang") in LANGS:
            return data["lang"]
    except (OSError, ValueError):
        pass
    return detect(env)


def save(root: Path, lang: str) -> None:
    if lang not in LANGS:
        raise ValueError(f"unknown language {lang!r}")
    root = Path(root)
    try:
        root.mkdir(parents=True, exist_ok=True)
        (root / UI_FILE).write_text(json.dumps({"lang": lang}) + "\n", encoding="utf-8")
    except OSError:
        pass  # a preference: if it cannot be kept, the choice still holds for this session


__all__ = ["DEFAULT", "LANGS", "UI_FILE", "detect", "load", "pick", "save"]
