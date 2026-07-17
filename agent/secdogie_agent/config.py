"""Configuration + secret resolution.

Lets users supply their API key (and default model) from a plain file they
fill in, instead of exporting an environment variable every time. Resolution
order, highest priority first:

  1. an explicit value passed on the command line (--api-key / --model)
  2. environment variables (ANTHROPIC_API_KEY / OPENAI_API_KEY / SECDOGIE_MODEL)
  3. a config file (--config PATH, else the first default location found)

Which API-key name is used depends on the resolved provider: the model (or an
explicit --provider) selects the provider, and each provider owns its key name
(ANTHROPIC_API_KEY for Anthropic, OPENAI_API_KEY for OpenAI).

The config file is dotenv-style: `KEY=VALUE` lines, `#` comments, blanks
ignored. Recognized keys: ANTHROPIC_API_KEY, OPENAI_API_KEY, SECDOGIE_MODEL.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import NamedTuple

from .providers import API_KEY_ENV, resolve_model_provider


def _exe_dir() -> Path | None:
    """Directory containing the actual running executable, for a frozen
    single-file build (PyInstaller sets `sys.frozen` and points
    `sys.executable` at the real .exe, not the Python interpreter). None when
    running from source (pip install / python -m), where there is no single
    "exe" to anchor to and the current directory / home are used instead.

    This matters because relying on the *current working directory* silently
    breaks portability: double-clicking the exe, launching it via a shortcut,
    running it "as Administrator", or invoking it from a shell that `cd`'d
    somewhere else can all leave the CWD pointing anywhere -- NOT necessarily
    the folder the exe was unzipped into. Anchoring to sys.executable's real
    location is invariant to all of that.
    """
    if getattr(sys, "frozen", False):
        try:
            return Path(sys.executable).resolve().parent
        except OSError:
            return None
    return None


def default_config_paths() -> list[Path]:
    """Searched in order; the first that exists wins.

    A frozen portable build looks first next to the executable itself: unzip
    it anywhere, drop a `secdogie.env` beside the .exe, and it's found no
    matter how the exe is launched or what the working directory happens to
    be -- no files ever need to touch the user's home directory. Then the
    current directory (running from source, or a config explicitly reached
    via `cd`), then the conventional per-user locations.
    """
    paths: list[Path] = []
    exe_dir = _exe_dir()
    if exe_dir is not None:
        paths.append(exe_dir / "secdogie.env")
    paths.append(Path("secdogie.env"))
    paths.append(Path.home() / ".config" / "secdogie" / "config")
    paths.append(Path.home() / ".secdogie" / "config")
    return paths


# Kept as a module-level snapshot for introspection/back-compat; resolve()
# always recomputes via default_config_paths() so it reflects the process's
# actual frozen state rather than whatever it was at import time.
DEFAULT_CONFIG_PATHS = default_config_paths()


def default_write_target() -> Path:
    """Where --init-config writes when given no explicit path: next to the
    exe for a frozen portable build (the whole install then stays self
    contained in one folder -- nothing under the user's home directory), else
    the conventional per-user config location for a source/pip install."""
    exe_dir = _exe_dir()
    if exe_dir is not None:
        return exe_dir / "secdogie.env"
    return Path.home() / ".config" / "secdogie" / "config"


# Kept for back-compat with any external reference; see default_write_target()
# for the (frozen-aware) logic write_template() actually uses.
USER_CONFIG_PATH = Path.home() / ".config" / "secdogie" / "config"

_TEMPLATE = """\
# secdogie-agent configuration
#
# Fill in the API key for the provider you'll use, then run:
#   secdogie-agent "your task"
# This file may contain a secret -- keep it private (it is created chmod 600).

# Anthropic API key (get one at https://console.anthropic.com/). Used for
# claude-* models -- the default provider.
ANTHROPIC_API_KEY=

# OpenAI API key (get one at https://platform.openai.com/). Used for gpt-* /
# o-series models. Leave blank if you only use Anthropic.
# OPENAI_API_KEY=

# Optional: default model to use (overridable with --model). The model prefix
# picks the provider (claude-* -> Anthropic, gpt-* -> OpenAI); you can also be
# explicit with a provider/model ref such as openai/gpt-5.5.
# SECDOGIE_MODEL=claude-sonnet-5
"""


class ResolvedConfig(NamedTuple):
    api_key: str | None
    model: str | None
    provider: str  # canonical provider id the api_key/model belong to
    env_var: str  # env-var/config key name for this provider's key (for error messages)
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
    cli_provider: str | None = None,
) -> ResolvedConfig:
    """Resolves the provider, API key, and model from CLI args, env, and file."""
    # Parse the config file up front: it can supply both the model and (as the
    # lowest-priority fallback) the provider's API key.
    chosen = Path(config_path) if config_path else _first_existing(default_config_paths())
    file_values = parse_config_file(chosen) if chosen is not None else {}

    # Model: CLI wins, then env, then config file, else leave None.
    model = (
        cli_model
        or os.environ.get("SECDOGIE_MODEL")
        or file_values.get("SECDOGIE_MODEL")
        or None
    )

    # Provider selects which API key name to look for. `bare_model` drops any
    # `provider/` prefix so downstream sends the SDK the plain model id.
    provider, bare_model = resolve_model_provider(model, cli_provider)
    env_var = API_KEY_ENV[provider]

    if cli_api_key:
        api_key: str | None = cli_api_key
        source = "--api-key argument"
    elif os.environ.get(env_var):
        api_key = os.environ[env_var]
        source = f"{env_var} environment variable"
    elif file_values.get(env_var):
        api_key = file_values[env_var]
        source = f"config file {chosen}"
    else:
        api_key = None
        source = "none"

    return ResolvedConfig(
        api_key=api_key,
        model=bare_model,
        provider=provider,
        env_var=env_var,
        api_key_source=source,
    )


def write_template(path: Path | None = None) -> Path:
    """Writes the config template to `path` (default: default_write_target(),
    i.e. next to the exe for a frozen portable build) with owner-only
    permissions. Refuses to clobber an existing file. Returns the path
    written."""
    target = path or default_write_target()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"{target} already exists; edit it directly or delete it first")
    target.write_text(_TEMPLATE, encoding="utf-8")
    try:
        os.chmod(target, 0o600)  # it will hold a secret; best-effort on POSIX
    except (OSError, NotImplementedError):
        pass
    return target
