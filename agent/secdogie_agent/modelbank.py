"""A `model/` folder next to the exe: choosing a model is dropping in a file.

Picking a model used to mean knowing a flag (`--model claude-…`) or editing a
dotenv file — fine in a terminal, tedious for someone who just downloaded a
single .exe. This makes the model slot an *empty seat the folder fills*: put a
file in `model/`, and that's the model. Two files, and the launcher shows them
as a choice. No files, and nothing changes — the built-in default still runs.

The file format is deliberately forgiving, because the point is not having to
learn one. All four of these name the same model:

    model/claude-opus-4-8            (empty file: the NAME is the model)
    model/fast.txt                   containing: claude-opus-4-8
    model/fast.txt                   containing: SECDOGIE_MODEL=claude-opus-4-8
    model/fast.env                   containing both that and an API key line

A preset may also carry its provider's API key (`ANTHROPIC_API_KEY=…`,
`OPENAI_API_KEY=…`), which is what makes switching *across* providers one
click instead of also re-plumbing a secret. Those files hold a secret like any
other config file — see write_example_dir(), which creates them chmod 600.

The preset's **name** (the filename without extension) is a first-class handle:
`--model fast` resolves through this folder before it is treated as a model id,
so short names you choose beat model ids you have to remember.

Everything here is pure except find_dir()/load_dir()/write_example_dir(), which
only touch the filesystem — no display, no platform APIs, so it all tests
headless.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Recognized keys inside a preset file. SECDOGIE_MODEL names the model; the
# other two let a preset carry the key its provider needs.
MODEL_KEY = "SECDOGIE_MODEL"
KEY_NAMES = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")

# The folder name looked for next to the executable / in the working directory.
DIR_NAME = "model"

# Files in the folder that are notes for humans, not presets.
_IGNORED_SUFFIXES = (".md", ".readme")
_IGNORED_STEMS = ("readme", "read me", "_readme")


class AmbiguousModel(Exception):
    """Several presets are available and nothing said which one to use.

    Raised instead of silently picking one: with `fast` and `careful` sitting
    side by side, guessing alphabetically would quietly run the wrong model and
    bill the wrong rate. `names` is what to offer the user.
    """

    def __init__(self, names: list[str]):
        self.names = names
        super().__init__(
            "several models are set up (" + ", ".join(names) + "); choose one with "
            "--model <name>, or rename one of the files to 'default'"
        )


@dataclass(frozen=True)
class ModelPreset:
    """One file in the model folder."""

    name: str                 # the filename without its extension
    model: str | None         # the model id it names, if any
    values: dict[str, str]    # every recognized key/value it carried (API keys, ...)
    path: Path | None = None

    @property
    def api_keys(self) -> dict[str, str]:
        return {k: v for k, v in self.values.items() if k in KEY_NAMES and v}


# -- parsing (pure) ------------------------------------------------------------


def looks_like_model_id(text: str) -> bool:
    """Whether a string is plausibly a model id rather than a nickname.

    This is what lets an empty file named `claude-opus-4-8` mean that model
    while an empty file named `fast` doesn't silently become a model called
    "fast" and fail at the API with a baffling error. The prefixes mirror the
    routing table in providers/__init__.py -- keep them in step.
    """
    t = text.strip().lower()
    if not t:
        return False
    if "/" in t:  # a provider/model ref like openai/gpt-5.5
        t = t.partition("/")[2].strip()
    return t.startswith(("claude", "gpt", "chatgpt", "o1", "o3", "o4"))


def parse_preset(name: str, text: str, path: Path | None = None) -> ModelPreset:
    """Read one preset file's contents. Never raises: anything unrecognized
    yields a preset with no model, which falls back to the normal default
    rather than inventing a model id nobody serves."""
    values: dict[str, str] = {}
    bare: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key:
                values[key] = val
        elif bare is None:
            # A line that is just a model id -- the "I pasted the name in" case.
            bare = line

    model = values.get(MODEL_KEY) or bare
    if not model and looks_like_model_id(name):
        model = name  # an empty file whose NAME is the model id
    return ModelPreset(name=name, model=(model or None), values=values, path=path)


def preset_name(filename: str) -> str:
    """The handle for a file: its name without the extension. `claude-opus-4-8`
    and `claude-opus-4-8.txt` are the same preset."""
    return Path(filename).stem.strip()


def is_preset_file(filename: str) -> bool:
    """Whether a filename in the folder is a preset rather than a note or a
    hidden/OS file. Keeps a README next to the presets from becoming one."""
    p = Path(filename)
    if not p.name or p.name.startswith(".") or p.name.startswith("_"):
        return False
    if p.suffix.lower() in _IGNORED_SUFFIXES:
        return False
    return p.stem.strip().lower() not in _IGNORED_STEMS


def pick(presets: list[ModelPreset], requested: str | None = None) -> ModelPreset | None:
    """Choose a preset. `requested` matches a preset name first, then a model id
    (so `--model claude-opus-4-8` finds the file that carries it and picks up its
    API key too).

    With nothing requested: one preset is unambiguous and wins; a preset named
    `default` wins over its siblings; more than one and nothing to break the tie
    raises AmbiguousModel rather than guessing. No presets -> None, and the
    caller's normal defaults apply.
    """
    if requested:
        want = requested.strip().lower()
        for p in presets:
            if p.name.strip().lower() == want:
                return p
        for p in presets:
            if p.model and p.model.strip().lower() == want:
                return p
        return None

    # Auto-picking only considers files that actually name a model: a preset
    # carrying nothing but an API key (or a template nobody filled in) is not a
    # candidate, and must not turn a folder with one real choice into an
    # ambiguous one.
    candidates = usable(presets)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    for p in candidates:
        if p.name.strip().lower() == "default":
            return p
    raise AmbiguousModel([p.name for p in candidates])


def usable(presets: list[ModelPreset]) -> list[ModelPreset]:
    """Presets that actually name a model -- the ones worth offering or picking."""
    return [p for p in presets if p.model]


def names(presets: list[ModelPreset]) -> list[str]:
    """Preset names, for a menu or an error message."""
    return [p.name for p in presets]


# -- the folder (filesystem, still headless) -----------------------------------


def candidate_dirs(explicit: str | None = None, exe_dir: Path | None = None) -> list[Path]:
    """Where a model folder may live, most specific first: an explicit
    --model-dir, then beside the running .exe (so the folder you see next to the
    download is the one that counts), then the working directory, then the
    per-user config location."""
    dirs: list[Path] = []
    if explicit:
        dirs.append(Path(explicit))
    if exe_dir is not None:
        dirs.append(Path(exe_dir) / DIR_NAME)
    dirs.append(Path(DIR_NAME))
    dirs.append(Path.home() / ".config" / "secdogie" / DIR_NAME)
    return dirs


def find_dir(explicit: str | None = None, exe_dir: Path | None = None) -> Path | None:
    """First candidate directory that exists, or None."""
    for d in candidate_dirs(explicit, exe_dir):
        try:
            if d.is_dir():
                return d
        except OSError:
            continue
    return None


def load_dir(directory: Path | None) -> list[ModelPreset]:
    """Every preset in `directory`, sorted by name. A missing directory, an
    unreadable file, or a subdirectory is skipped rather than fatal: a broken
    file in the folder must not stop the agent from running."""
    if directory is None:
        return []
    try:
        entries = sorted(directory.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []

    presets: list[ModelPreset] = []
    for entry in entries:
        try:
            if not entry.is_file() or not is_preset_file(entry.name):
                continue
            text = entry.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        presets.append(parse_preset(preset_name(entry.name), text, entry))
    return presets


def load(explicit: str | None = None, exe_dir: Path | None = None) -> list[ModelPreset]:
    """Presets from the first model folder that exists."""
    return load_dir(find_dir(explicit, exe_dir))


_README = """\
Drop a file in this folder to choose the model. That's the whole mechanism.

The file's NAME is the handle you type after --model; what's inside it is the
model. Any of these work:

  claude-opus-4-8           an empty file -- the filename is the model id
  fast.txt                  containing just:  claude-haiku-4-5
  careful.txt               containing:       SECDOGIE_MODEL=claude-opus-4-8

  * one file  -> that model is used, nothing to choose
  * several   -> the start menu offers them; from a terminal pass --model <name>
                 (or name one of the files 'default')
  * none      -> the built-in default is used

A preset can also carry the API key its provider needs, so switching between
providers is one file and not also a key hunt:

  openai.txt   containing:  SECDOGIE_MODEL=gpt-5.5
                            OPENAI_API_KEY=sk-...

Files with a key in them are secrets -- this folder's example was created
readable only by you, and anything you add here deserves the same care.
"""

# Leading underscore: is_preset_file() skips it, so the example we ship can sit
# in the folder as a thing to copy without ever being treated as a choice.
_EXAMPLE_NAME = "_example.txt"
_EXAMPLE = """\
# Copy this file to a name of your choosing (the filename is the name you pass
# to --model) and uncomment a line below. This file itself is ignored -- names
# starting with _ are not presets.

# SECDOGIE_MODEL=claude-opus-4-8
# SECDOGIE_MODEL=claude-sonnet-5
# SECDOGIE_MODEL=claude-haiku-4-5

# If this model's provider needs a key you haven't set anywhere else, add it
# here -- keep this file private if you do.
# ANTHROPIC_API_KEY=
"""


def write_example_dir(directory: Path) -> Path:
    """Create the model folder with a README and one commented-out example, so
    it exists and explains itself before anyone reads the docs. Never clobbers
    a file that is already there. Returns the directory."""
    directory.mkdir(parents=True, exist_ok=True)
    readme = directory / "README.md"
    if not readme.exists():
        readme.write_text(_README, encoding="utf-8")
    example = directory / _EXAMPLE_NAME
    if not example.exists():
        example.write_text(_EXAMPLE, encoding="utf-8")
        try:
            os.chmod(example, 0o600)  # it invites a key; best-effort on POSIX
        except (OSError, NotImplementedError):
            pass
    return directory
