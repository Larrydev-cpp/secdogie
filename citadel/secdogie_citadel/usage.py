"""How a thing on this computer has been used: the facts behind "you still use this".

Before the companion deletes anything, it looks at how the thing has lived
on this computer:

  * what it is, and what is inside it (files, total size, which kinds);
  * since when (the earliest change inside) and until when (the latest
    change, and which file it was);
  * how often the person opened it, where the OS keeps count (macOS
    Spotlight's use count, the freedesktop recently-used list);
  * whether it was ever changed after it arrived, or was copied in with its
    own dates (kept on purpose), or arrived and was never touched again.

Metadata only. Nothing is opened, read or sent anywhere. The walk stops after
``max_entries`` entries or ``budget`` seconds, skips what it cannot read and
never follows a symbolic link. The result (``Usage``) is plain data for
``deliberate.py``, which turns it into words.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

KINDS = (
    ("照片", {"jpg", "jpeg", "png", "heic", "heif", "gif", "webp", "raw", "cr2", "cr3", "nef", "arw", "dng", "tif",
              "tiff", "bmp"}),
    ("视频", {"mp4", "mov", "avi", "mkv", "m4v", "wmv", "webm"}),
    ("音乐", {"mp3", "m4a", "flac", "wav", "aac", "ogg"}),
    ("文档", {"doc", "docx", "pdf", "txt", "md", "pages", "key", "numbers", "xls", "xlsx", "ppt", "pptx", "csv",
              "rtf", "odt", "ods", "odp", "epub"}),
    ("代码", {"py", "js", "ts", "go", "rs", "java", "c", "cc", "cpp", "h", "swift", "kt", "rb", "php", "html", "css",
              "json", "yaml", "yml", "toml", "sh", "ipynb"}),
    ("压缩包", {"zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz", "dmg", "iso"}),
)
JUNK = {".ds_store", "thumbs.db", "desktop.ini", ".localized"}
ARRIVAL_SLACK = 120.0  # seconds: written within this of arriving = never changed after it arrived

# Where a bare name ("旧备份") is looked for, two levels deep.
LOOK_IN = ("", "Desktop", "Documents", "Downloads", "Pictures", "Music", "Movies", "Videos", "桌面", "文档", "下载",
           "图片", "音乐", "视频")
_PATH = re.compile(r"(~[/\\][^\s，。；、「」“”\"']*|/[^\s，。；、「」“”\"']+|[A-Za-z]:\\[^\s，。；、「」“”\"']*)")


@dataclass(frozen=True)
class Usage:
    target: str  # what the person called it
    path: str = ""  # where it is on this computer; "" when it is not here
    exists: bool = False
    is_dir: bool = False
    files: int = 0  # files inside a folder (junk such as .DS_Store not counted)
    capped: bool = False  # the walk stopped early: there is more than counted
    size: int = 0  # bytes
    kinds: tuple[tuple[str, int], ...] = ()  # ("照片", 1102), most first
    first: float | None = None  # the earliest change inside
    last: float | None = None  # the latest change inside
    last_name: str = ""  # what changed last
    opened: int | None = None  # times opened as the OS counts them; None: the OS does not say
    last_opened: float | None = None
    touched: bool = False  # something was changed after it arrived
    kept: bool = False  # something was copied in with its own, older dates: put here on purpose
    unknown_arrival: bool = False  # this OS does not say when files arrived
    candidates: tuple[str, ...] = field(default=())  # several things answer to this name

    @property
    def empty(self) -> bool:
        return self.exists and (self.files == 0 if self.is_dir else self.size == 0)

    def on_its_own_unused(self) -> bool:
        """Never used, as far as the computer can tell: empty, or it arrived
        and was never opened, changed or kept on purpose. When the OS cannot
        say when files arrived, nothing counts as unused but an empty thing."""
        if not self.exists:
            return False
        if self.empty:
            return True
        if self.unknown_arrival or self.opened or self.touched or self.kept:
            return False
        return True


def _kind(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    for label, exts in KINDS:
        if ext in exts:
            return label
    return "其他"


def _born(st) -> float | None:
    born = getattr(st, "st_birthtime", None)
    if born is None and sys.platform == "win32":
        born = st.st_ctime  # creation time on Windows
    return float(born) if born else None


def _mac_opened(path: Path, run) -> tuple[int | None, float | None]:
    out = run(["mdls", "-raw", "-name", "kMDItemUseCount", "-name", "kMDItemLastUsedDate", str(path)])
    if not out:
        return None, None
    parts = out.split("\0")
    count = int(parts[0]) if parts and parts[0].strip().isdigit() else None
    last = None
    if len(parts) > 1 and parts[1].strip() not in ("", "(null)"):
        try:
            last = time.mktime(time.strptime(parts[1].strip()[:19], "%Y-%m-%d %H:%M:%S")) - time.timezone
        except ValueError:
            last = None
    return count, last


def _xdg_opened(path: Path, home: Path) -> tuple[int | None, float | None]:
    """Opens recorded in the freedesktop recently-used list, for ``path`` and anything under it."""
    xbel = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share") / "recently-used.xbel"
    try:
        text = xbel.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, None
    root = str(path)
    count, last = 0, None
    for m in re.finditer(r"<bookmark\b([^>]*)>(.*?)</bookmark>", text, re.S):
        href = re.search(r'href="([^"]+)"', m.group(1))
        if not href:
            continue
        where = unquote(urlparse(href.group(1)).path)
        if where != root and not where.startswith(root.rstrip("/") + "/"):
            continue
        counts = [int(c) for c in re.findall(r'count="(\d+)"', m.group(2))]
        count += sum(counts) or 1
        visited = re.search(r'visited="([^"]+)"', m.group(1))
        if visited:
            try:
                t = time.mktime(time.strptime(visited.group(1)[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
                last = t if last is None else max(last, t)
            except ValueError:
                pass
    return count, last


def _run(cmd: list[str]) -> str:
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=2.0, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""


def resolve(target: str, *, home: Path) -> list[Path]:
    """Where ``target`` is on this computer: a path as written, or a bare name
    looked for in the usual places (two levels deep)."""
    target = target.strip().strip("「」“”\"'")
    if not target:
        return []
    if _PATH.fullmatch(target) or os.sep in target or "/" in target:
        if target.startswith("~"):
            p = home / target[1:].lstrip("/\\")
        else:
            p = Path(target)
            p = p if p.is_absolute() else home / p
        return [p] if os.path.lexists(p) else []
    found: list[Path] = []
    for base in LOOK_IN:
        root = home / base if base else home
        for depth_root in [root, *([c for c in _children(root)] if base else [])]:
            p = depth_root / target
            if os.path.lexists(p) and p not in found:
                found.append(p)
    return found


def _children(root: Path) -> list[Path]:
    try:
        return [c for c in root.iterdir() if c.is_dir() and not c.is_symlink() and not c.name.startswith(".")][:200]
    except OSError:
        return []


def read_usage(target: str, *, home: Path | None = None, max_entries: int = 5000, budget: float = 1.5,
               platform: str = sys.platform, run=_run, now=time.time) -> Usage:
    home = Path(home) if home is not None else Path.home()
    places = resolve(target, home=home)
    if not places:
        return Usage(target)
    if len(places) > 1:
        return Usage(target, candidates=tuple(str(p) for p in places))
    path = places[0]
    try:
        st = path.lstat()
    except OSError:
        return Usage(target, str(path))
    is_dir = path.is_dir() and not path.is_symlink()
    files = size = 0
    kinds: dict[str, int] = {}
    first = last = None
    last_name = ""
    touched = kept = unknown = False
    capped = False
    deadline = now() + budget

    def note_file(p: Path, s, born_hint: float | None = None) -> None:
        nonlocal files, size, first, last, last_name, touched, kept, unknown
        files += 1
        size += s.st_size
        kinds[_kind(p.name)] = kinds.get(_kind(p.name), 0) + 1
        m = float(s.st_mtime)
        first = m if first is None else min(first, m)
        if last is None or m > last:
            last, last_name = m, p.name
        born = _born(s) or born_hint
        if born is None:
            unknown = True
        elif m - born > ARRIVAL_SLACK:
            touched = True
        elif born - m > ARRIVAL_SLACK:
            kept = True

    if is_dir:
        seen = 0
        for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
            dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
            for name in filenames:
                seen += 1
                if seen > max_entries or now() > deadline:
                    capped = True
                    break
                if name.lower() in JUNK or name.startswith("._"):
                    continue
                p = Path(dirpath) / name
                try:
                    s = p.lstat()
                except OSError:
                    continue
                if os.path.islink(p):
                    continue
                note_file(p, s)
            if capped:
                break
    elif not path.is_symlink():
        hint = None
        if _born(st) is None and platform.startswith("linux"):  # GNU stat reads the birth time where the FS keeps it
            out = run(["stat", "-c", "%W", str(path)]).strip()
            hint = float(out) if out.isdigit() and int(out) > 0 else None
        note_file(path, st, hint)

    opened, last_opened = None, None
    if platform == "darwin":
        opened, last_opened = _mac_opened(path, run)
    elif platform.startswith("linux"):
        opened, last_opened = _xdg_opened(path, home)
    return Usage(
        target, str(path), True, is_dir, files, capped, size,
        tuple(sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0]))), first, last, last_name if is_dir else "",
        opened, last_opened, touched, kept, unknown and not touched and not kept,
    )


def targets_in(text: str) -> list[str]:
    """Paths written out in ``text`` (~/…, /…, C:\\…)."""
    return [m.group(1).rstrip("。，；、.,;") for m in _PATH.finditer(text)]


__all__ = ["KINDS", "Usage", "read_usage", "resolve", "targets_in"]
