"""Configuration + secret resolution.

Lets users supply their API key (and default model) from a plain file they
fill in, instead of exporting an environment variable every time. Resolution
order, highest priority first:

  1. an explicit value passed on the command line (--api-key / --model)
  2. the ANTHROPIC_API_KEY / SECDOGIE_MODEL environment variables
  3. a config file (--config PATH, else the first default location found)

The config file is dotenv-style: `KEY=VALUE` lines, `#` comments, blanks
ignored. Recognized keys: ANTHROPIC_API_KEY, SECDOGIE_MODEL.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import NamedTuple

# Searched in order; the first that exists wins. `secdogie.env` in the current
# directory is handy next to a downloaded single-file binary; the ~/.config
# path is the conventional per-user location.
DEFAULT_CONFIG_PATHS = [
    Path("secdogie.env"),
    Path.home() / ".config" / "secdogie" / "config",
    Path.home() / ".secdogie" / "config",
]

# Where --init-config writes its template.
USER_CONFIG_PATH = Path.home() / ".config" / "secdogie" / "config"

_TEMPLATE = """\
# secdogie-agent configuration
#
# Fill in your API key below, then run:  secdogie-agent "your task"
# This file may contain a secret -- keep it private (it is created chmod 600).

# Your Anthropic API key (get one at https://console.anthropic.com/).
ANTHROPIC_API_KEY=

# Optional: default model to use (overridable with --model).
# SECDOGIE_MODEL=claude-sonnet-5
"""


class ResolvedConfig(NamedTuple):
    api_key: str | None
    model: str | None
    api_key_source: str  # human-readable, for logging/error messages (never the key itself)


def parse_config_file(path: Path) -> dict[str, str]:
    """Parses a dotenv-style file into a dict. Missing file -> empty dict."""
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return values
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            values[key] = val
    return values


def _first_existing(paths: list[Path]) -> Path | None:
    for p in paths:
        if p.is_file():
            return p
    return None


def resolve(
    cli_api_key: str | None = None,
    cli_model: str | None = None,
    config_path: str | None = None,
) -> ResolvedConfig:
    """Resolves the API key and model from CLI args, env, and config file."""
    file_values: dict[str, str] = {}
    source = "none"

    if cli_api_key:
        api_key: str | None = cli_api_key
        source = "--api-key argument"
    elif os.environ.get("ANTHROPIC_API_KEY"):
        api_key = os.environ["ANTHROPIC_API_KEY"]
        source = "ANTHROPIC_API_KEY environment variable"
    else:
        chosen = Path(config_path) if config_path else _first_existing(DEFAULT_CONFIG_PATHS)
        if chosen is not None:
            file_values = parse_config_file(chosen)
            api_key = file_values.get("ANTHROPIC_API_KEY") or None
            if api_key:
                source = f"config file {chosen}"
            else:
                api_key = None
        else:
            api_key = None

    # Model: CLI wins, then env, then config file, else leave None (caller
    # applies its own default).
    model = (
        cli_model
        or os.environ.get("SECDOGIE_MODEL")
        or file_values.get("SECDOGIE_MODEL")
        or None
    )

    return ResolvedConfig(api_key=api_key, model=model, api_key_source=source)


def write_template(path: Path | None = None) -> Path:
    """Writes the config template to `path` (default USER_CONFIG_PATH) with
    owner-only permissions. Refuses to clobber an existing file. Returns the
    path written."""
    target = path or USER_CONFIG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"{target} already exists; edit it directly or delete it first")
    target.write_text(_TEMPLATE, encoding="utf-8")
    try:
        os.chmod(target, 0o600)  # it will hold a secret; best-effort on POSIX
    except (OSError, NotImplementedError):
        pass
    return target
