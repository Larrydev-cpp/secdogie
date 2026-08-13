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
Arbitrary extra KEY=VALUE lines are preserved (for custom/OpenAI-compatible
keys written by the GUI dialog).

**Portable builds (PyInstaller):** when frozen, the first place we look (and
where `--init-config` writes by default) is *next to the .exe*, so a single
folder is fully self-contained no matter what the current working directory is.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from .providers import API_KEY_ENV, resolve_model_provider


def _exe_dir() -> Path | None:
    """Directory containing the frozen executable, or None when running from source."""
    if getattr(sys, "frozen", False):
        # PyInstaller sets sys.executable to the real .exe path.
        return Path(sys.executable).resolve().parent
    return None


def default_config_paths() -> list[Path]:
    """Search order for config files. Frozen builds put the exe-adjacent
    `secdogie.env` first so a portable install works regardless of CWD."""
    paths: list[Path] = []
    exe = _exe_dir()
    if exe is not None:
        paths.append(exe / "secdogie.env")
    # Always also check CWD (useful for source runs and when the user puts a
    # file next to where they launched from).
    paths.append(Path("secdogie.env"))
    paths.append(Path.home() / ".config" / "secdogie" / "config")
    paths.append(Path.home() / ".secdogie" / "config")
    return paths


def default_write_target() -> Path:
    """Where `--init-config` (and the GUI key dialog) should write by default.

    Frozen → next to the exe (portable).
    Source → ~/.config/secdogie/config (conventional).
    """
    exe = _exe_dir()
    if exe is not None:
        return exe / "secdogie.env"
    return Path.home() / ".config" / "secdogie" / "config"


def default_trace_path() -> Path:
    """Where a GUI run writes its audit trace when the user did not pass --trace.

    Frozen → next to the exe (same portable folder as secdogie.env).
    Source → ~/.local/share/secdogie/traces/ (XDG-ish; created on demand).

    Filename includes a UTC timestamp so consecutive runs do not overwrite.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"secdogie-trace-{stamp}.jsonl"
    exe = _exe_dir()
    if exe is not None:
        return exe / name
    base = Path.home() / ".local" / "share" / "secdogie" / "traces"
    return base / name


# Kept for backward-compatible imports; prefer the functions above.
DEFAULT_CONFIG_PATHS = default_config_paths()
USER_CONFIG_PATH = default_write_target()

_TEMPLATE = """\
# secdogie-agent configuration
#
# Fill in the API key for the provider you'll use, then run:
#   secdogie-agent "your task"
# This file may contain a secret -- keep it private (it is created chmod 600).

# Anthropic API key (get one at https://console.anthropic.com/). Used for
# claude-* models -- the default provider.
ANTHROPIC_API_KEY=

# OpenAI API key (get one at https://platform.openai.com/). Also used for
# OpenAI-compatible endpoints (DeepSeek, Groq, local vLLM, etc.) when you pass
# --provider openai and a custom base URL via the SDK / env.
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


def has_configured_api_key() -> bool:
    """True if *any* usable API key is available (env or config file).

    Used by the first-run GUI path: if this is False, we force the key dialog
    before offering a task, instead of failing later with a terminal-only error.
    Checks known provider env vars plus any non-empty *API_KEY* line in config.
    """
    for env_var in API_KEY_ENV.values():
        if os.environ.get(env_var, "").strip():
            return True
    # Also accept a custom env the user may have exported for OpenAI-compat.
    if os.environ.get("OPENAI_API_KEY", "").strip():
        return True

    chosen = _first_existing(default_config_paths())
    if chosen is None:
        return False
    values = parse_config_file(chosen)
    for env_var in API_KEY_ENV.values():
        if values.get(env_var, "").strip():
            return True
    # Custom keys written by the GUI (e.g. DEEPSEEK_API_KEY=...)
    for k, v in values.items():
        if k.endswith("_API_KEY") and v.strip():
            return True
    return False


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
    """Writes the config template to `path` (default: portable location) with
    owner-only permissions. Refuses to clobber an existing file. Returns the
    path written."""
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


def _upsert_line(lines: list[str], env_var: str, value: str) -> list[str]:
    """Replace or append `ENV=value` in a list of config lines."""
    key_line = f"{env_var}={value}"
    found = False
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(f"{env_var}=") or stripped.startswith(f"#{env_var}="):
            new_lines.append(key_line)
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(key_line)
    return new_lines


def write_api_key(
    api_key: str,
    *,
    env_var: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    path: Path | None = None,
) -> Path:
    """Write (or update) an API key into the config file.

    Used by the GUI key dialog so the user never has to open a text editor.
    Accepts either a known `provider` (anthropic/openai) or an arbitrary
    `env_var` name (for custom / OpenAI-compatible keys). Optionally also
    writes SECDOGIE_MODEL.
    """
    target = path or default_write_target()
    target.parent.mkdir(parents=True, exist_ok=True)

    if env_var:
        key_name = env_var.strip()
    elif provider:
        key_name = API_KEY_ENV.get(provider, "ANTHROPIC_API_KEY")
    else:
        key_name = "ANTHROPIC_API_KEY"

    if not key_name or "=" in key_name or " " in key_name:
        raise ValueError(f"invalid env var name: {key_name!r}")

    if target.exists():
        lines = target.read_text(encoding="utf-8").splitlines()
    else:
        lines = _TEMPLATE.splitlines()

    lines = _upsert_line(lines, key_name, api_key.strip())
    if model and model.strip():
        lines = _upsert_line(lines, "SECDOGIE_MODEL", model.strip())

    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(target, 0o600)
    except (OSError, NotImplementedError):
        pass
    return target
