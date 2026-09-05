"""Provider registry + model->provider routing.

Canonical providers: "anthropic", "openai", and "openrouter". The agent picks
one per run from (in order): an explicit --provider, a `provider/model` ref
(e.g. "openai/gpt-5.5" or "openrouter/anthropic/claude-sonnet-4"), a key
prefix (`sk-or-` is OpenRouter), or the model-id prefix. Anthropic is the
default when nothing else decides, since it is the reference implementation
and keeps older invocations working.
"""
from __future__ import annotations

from .anthropic_provider import AnthropicProvider
from .base import VisionProvider

ANTHROPIC_PROVIDER_ID = "anthropic"
OPENAI_PROVIDER_ID = "openai"
OPENROUTER_PROVIDER_ID = "openrouter"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Env var / config-file key that holds each provider's API key. Provider
# identity owns its auth key name so config and CLI ask for the right secret.
API_KEY_ENV = {
    ANTHROPIC_PROVIDER_ID: "ANTHROPIC_API_KEY",
    OPENAI_PROVIDER_ID: "OPENAI_API_KEY",
    OPENROUTER_PROVIDER_ID: "OPENROUTER_API_KEY",
}

# Default model per provider when the user names a provider but no model.
DEFAULT_MODELS = {
    ANTHROPIC_PROVIDER_ID: "claude-sonnet-5",
    OPENAI_PROVIDER_ID: "gpt-5.5",
    # OpenRouter keeps the vendor/model id the catalogue uses.
    OPENROUTER_PROVIDER_ID: "anthropic/claude-sonnet-4",
}

# Vision-capable models offered as ready picks in a UI (e.g. secdogie-open's
# model dropdown), most capable first. Not a gate and not exhaustive -- any
# model string still works; this is just so the common choices don't have to be
# typed. The first entry of the default provider is the overall default.
SUGGESTED_MODELS = {
    ANTHROPIC_PROVIDER_ID: ["claude-sonnet-5", "claude-opus-4-8", "claude-haiku-4-5-20251001"],
    OPENAI_PROVIDER_ID: ["gpt-5.5", "gpt-5.4"],
    OPENROUTER_PROVIDER_ID: [
        "anthropic/claude-sonnet-4",
        "openai/gpt-4o",
        "google/gemini-2.5-flash",
    ],
}

# Accepted spellings for --provider and the head of a `provider/model` ref.
_PROVIDER_ALIASES = {
    "anthropic": ANTHROPIC_PROVIDER_ID,
    "claude": ANTHROPIC_PROVIDER_ID,
    "openai": OPENAI_PROVIDER_ID,
    "gpt": OPENAI_PROVIDER_ID,
    "openrouter": OPENROUTER_PROVIDER_ID,
    "or": OPENROUTER_PROVIDER_ID,
}

# Model-id prefixes that route to OpenAI; anything else falls back to Anthropic
# unless an OpenRouter ref / key decided first.
_OPENAI_MODEL_PREFIXES = ("gpt", "chatgpt", "o1", "o3", "o4")


def normalize_provider(value: str | None) -> str | None:
    """Canonical provider id for a user-supplied name, or None if unrecognized."""
    if not value:
        return None
    return _PROVIDER_ALIASES.get(value.strip().lower())


def infer_provider_from_key(api_key: str | None) -> str | None:
    """Guess the provider from a pasted secret. `sk-or-` is OpenRouter."""
    if not api_key:
        return None
    k = api_key.strip()
    if k.startswith("sk-or-"):
        return OPENROUTER_PROVIDER_ID
    if k.startswith("sk-ant-"):
        return ANTHROPIC_PROVIDER_ID
    return None


def _infer_provider(model: str | None) -> str:
    m = (model or "").lower()
    if m.startswith("openrouter/") or m.startswith("or/"):
        return OPENROUTER_PROVIDER_ID
    if m.startswith("claude"):
        return ANTHROPIC_PROVIDER_ID
    if any(m.startswith(p) for p in _OPENAI_MODEL_PREFIXES):
        return OPENAI_PROVIDER_ID
    return ANTHROPIC_PROVIDER_ID


def resolve_model_provider(
    model: str | None, explicit_provider: str | None = None
) -> tuple[str, str | None]:
    """Decide the provider and the model id to send to the SDK.

    Returns (provider_id, bare_model). None for the model means "use the
    provider's default".

    OpenRouter catalogue ids are `vendor/model` (`anthropic/claude-sonnet-4`,
    `openai/gpt-4o`). That first slash is NOT a secdogie provider prefix
    unless the head is literally `openrouter/` / `or/`. Stripping
    `anthropic/` off a saved OpenRouter default used to send
    `claude-sonnet-4` to OpenRouter, which 404s — and the GUI swallowed
    that as "typed a command, nothing happened".
    """
    explicit = normalize_provider(explicit_provider)

    if model and "/" in model:
        head, _, tail = model.partition("/")
        head_id = normalize_provider(head)

        # openrouter/<vendor/model> (or or/<vendor/model>)
        if head_id == OPENROUTER_PROVIDER_ID:
            return OPENROUTER_PROVIDER_ID, (tail or None)

        # Operator chose OpenRouter: keep the vendor/model id intact.
        if explicit == OPENROUTER_PROVIDER_ID:
            return OPENROUTER_PROVIDER_ID, model

        # Native SDK refs: openai/gpt-5.5, anthropic/claude-sonnet-5
        if head_id is not None:
            return (explicit or head_id), (tail or None)

    provider = explicit or _infer_provider(model)
    return provider, model


def make_provider(
    provider_id: str,
    model: str | None,
    api_key: str | None,
    max_tokens: int = 1024,
    proxy: str | None = None,
) -> VisionProvider:
    """Instantiate the provider, defaulting the model when none was given."""
    resolved_model = model or DEFAULT_MODELS[provider_id]
    if provider_id == OPENAI_PROVIDER_ID:
        from .openai_provider import OpenAIProvider

        return OpenAIProvider(
            model=resolved_model, api_key=api_key, max_tokens=max_tokens, proxy=proxy
        )
    if provider_id == OPENROUTER_PROVIDER_ID:
        from .openai_provider import OpenAIProvider

        return OpenAIProvider(
            model=resolved_model,
            api_key=api_key,
            max_tokens=max_tokens,
            proxy=proxy,
            base_url=OPENROUTER_BASE_URL,
            default_headers={
                "HTTP-Referer": "https://github.com/Larrydev-cpp/secdogie",
                "X-Title": "secdogie",
            },
        )
    return AnthropicProvider(
        model=resolved_model, api_key=api_key, max_tokens=max_tokens, proxy=proxy
    )


__all__ = [
    "AnthropicProvider",
    "VisionProvider",
    "ANTHROPIC_PROVIDER_ID",
    "OPENAI_PROVIDER_ID",
    "OPENROUTER_PROVIDER_ID",
    "OPENROUTER_BASE_URL",
    "API_KEY_ENV",
    "DEFAULT_MODELS",
    "SUGGESTED_MODELS",
    "normalize_provider",
    "infer_provider_from_key",
    "resolve_model_provider",
    "make_provider",
]
