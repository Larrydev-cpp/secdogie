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
from pathlib import Path
from typing import NamedTuple

from .providers import (
    API_KEY_ENV,
    DEFAULT_MODELS,
    infer_provider_from_key,
    normalize_provider,
    resolve_model_provider,
)


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

# OpenRouter key (https://openrouter.ai/keys). Prefix is sk-or-. Models keep
# the vendor/model id, e.g. anthropic/claude-sonnet-4 or openai/gpt-4o.
# OPENROUTER_API_KEY=

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


def _lookup_key(env_var: str, file_values: dict[str, str], chosen: Path | None) -> tuple[str | None, str]:
    if os.environ.get(env_var, "").strip():
        return os.environ[env_var].strip(), f"{env_var} environment variable"
    if file_values.get(env_var, "").strip():
        where = f"config file {chosen}" if chosen is not None else "config file"
        return file_values[env_var].strip(), where
    return None, "none"


def _available_keys(file_values: dict[str, str], chosen: Path | None) -> list[tuple[str, str, str]]:
    """(provider, key, source) for every non-empty known provider key."""
    found: list[tuple[str, str, str]] = []
    for provider, env_var in API_KEY_ENV.items():
        key, source = _lookup_key(env_var, file_values, chosen)
        if key:
            found.append((provider, key, source))
    # A sk-or- key saved under OPENAI_API_KEY / ANTHROPIC_API_KEY still counts
    # as OpenRouter even if OPENROUTER_API_KEY is empty.
    extra: list[tuple[str, str, str]] = []
    for provider, key, source in found:
        guessed = infer_provider_from_key(key)
        if guessed and guessed != provider:
            extra.append((guessed, key, source))
    return extra + found


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
    if model:
        model = model.strip() or None

    stored_provider = normalize_provider(
        cli_provider
        or os.environ.get("SECDOGIE_PROVIDER")
        or file_values.get("SECDOGIE_PROVIDER")
    )

    # Provider selects which API key name to look for. `bare_model` drops any
    # known `provider/` prefix so downstream sends the SDK the right model id.
    provider, bare_model = resolve_model_provider(model, stored_provider)
    env_var = API_KEY_ENV[provider]

    if cli_api_key:
        api_key: str | None = cli_api_key.strip()
        source = "--api-key argument"
        guessed = infer_provider_from_key(api_key)
        # A pasted OpenRouter key must not be sent to api.anthropic.com just
        # because the model field was left empty (default = anthropic).
        if guessed and not cli_provider and (not model or guessed == provider or infer_provider_from_key(api_key) == guessed):
            if guessed != provider and not cli_model and not stored_provider:
                provider = guessed
                env_var = API_KEY_ENV[provider]
                if bare_model is None:
                    bare_model = DEFAULT_MODELS[provider]
    else:
        api_key, source = _lookup_key(env_var, file_values, chosen)
        if api_key is None:
            # No model / no explicit provider: use whichever key the operator
            # actually saved. This is the GUI first-run path — they paste an
            # OpenAI or OpenRouter key, then hit Start, and used to get a
            # silent "no Anthropic key" exit because the default model is
            # Claude. An *explicit* gpt-* model still refuses an Anthropic
            # key (see test_wrong_providers_key_is_not_reused).
            model_was_explicit = bool(cli_model or os.environ.get("SECDOGIE_MODEL") or file_values.get("SECDOGIE_MODEL"))
            provider_was_explicit = bool(cli_provider or os.environ.get("SECDOGIE_PROVIDER") or file_values.get("SECDOGIE_PROVIDER"))
            if not model_was_explicit and not provider_was_explicit:
                available = _available_keys(file_values, chosen)
                if available:
                    provider, api_key, source = available[0]
                    env_var = API_KEY_ENV.get(provider, env_var)
                    bare_model = DEFAULT_MODELS.get(provider)
            elif not provider_was_explicit:
                # Model is set but its key is missing; if the only saved key
                # is an OpenRouter sk-or- token, route there and keep the
                # vendor/model id (do not strip anthropic/ off it).
                for cand_provider, cand_key, cand_source in _available_keys(file_values, chosen):
                    if infer_provider_from_key(cand_key) == "openrouter" or cand_provider == "openrouter":
                        provider = "openrouter"
                        api_key = cand_key
                        source = cand_source
                        env_var = API_KEY_ENV[provider]
                        # OpenRouter wants vendor/model. If we already stripped
                        # anthropic/ off a Claude id, put it back.
                        if model and "/" in model and normalize_provider(model.split("/", 1)[0]) == "openrouter":
                            bare_model = model.split("/", 1)[1]
                        elif bare_model and "/" not in bare_model and model and "/" in model:
                            bare_model = model
                        elif not bare_model:
                            bare_model = DEFAULT_MODELS[provider]
                        break

        guessed = infer_provider_from_key(api_key)
        if guessed == "openrouter" and provider != "openrouter" and not cli_provider:
            provider = "openrouter"
            env_var = API_KEY_ENV[provider]
            if not bare_model:
                bare_model = DEFAULT_MODELS[provider]

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

    guessed = infer_provider_from_key(api_key)
    if guessed:
        provider = guessed
    if env_var:
        key_name = env_var.strip()
    elif provider:
        key_name = API_KEY_ENV.get(provider, "ANTHROPIC_API_KEY")
    else:
        key_name = "ANTHROPIC_API_KEY"
    if guessed == "openrouter" and not env_var:
        key_name = API_KEY_ENV["openrouter"]

    if not key_name or "=" in key_name or " " in key_name:
        raise ValueError(f"invalid env var name: {key_name!r}")

    if target.exists():
        lines = target.read_text(encoding="utf-8").splitlines()
    else:
        lines = _TEMPLATE.splitlines()

    lines = _upsert_line(lines, key_name, api_key.strip())
    guessed = infer_provider_from_key(api_key)
    persist_provider = provider or guessed
    if persist_provider:
        lines = _upsert_line(lines, "SECDOGIE_PROVIDER", persist_provider)
    model_to_write = (model or "").strip()
    if not model_to_write and persist_provider:
        model_to_write = DEFAULT_MODELS.get(persist_provider, "")
    if model_to_write:
        lines = _upsert_line(lines, "SECDOGIE_MODEL", model_to_write)

    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(target, 0o600)
    except (OSError, NotImplementedError):
        pass
    return target
