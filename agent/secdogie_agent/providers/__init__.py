"""Provider registry + model->provider routing.

Two canonical providers, mirroring how a multi-provider agent framework names
them: "anthropic" and "openai". The agent picks one per run from (in order):
an explicit --provider, a `provider/model` ref (e.g. "openai/gpt-5.5"), or the
model-id prefix. Anthropic is the default when nothing else decides, since it
is the reference implementation and keeps older invocations working.
"""
from __future__ import annotations

from .anthropic_provider import AnthropicProvider
from .base import VisionProvider

ANTHROPIC_PROVIDER_ID = "anthropic"
OPENAI_PROVIDER_ID = "openai"

# Env var / config-file key that holds each provider's API key. Provider
# identity owns its auth key name so config and CLI ask for the right secret.
API_KEY_ENV = {
    ANTHROPIC_PROVIDER_ID: "ANTHROPIC_API_KEY",
    OPENAI_PROVIDER_ID: "OPENAI_API_KEY",
}

# Default model per provider when the user names a provider but no model.
DEFAULT_MODELS = {
    ANTHROPIC_PROVIDER_ID: "claude-sonnet-5",
    OPENAI_PROVIDER_ID: "gpt-5.5",
}

# Accepted spellings for --provider and the head of a `provider/model` ref.
_PROVIDER_ALIASES = {
    "anthropic": ANTHROPIC_PROVIDER_ID,
    "claude": ANTHROPIC_PROVIDER_ID,
    "openai": OPENAI_PROVIDER_ID,
    "gpt": OPENAI_PROVIDER_ID,
}

# Model-id prefixes that route to OpenAI; anything else falls back to Anthropic.
_OPENAI_MODEL_PREFIXES = ("gpt", "chatgpt", "o1", "o3", "o4")


def normalize_provider(value: str | None) -> str | None:
    """Canonical provider id for a user-supplied name, or None if unrecognized."""
    if not value:
        return None
    return _PROVIDER_ALIASES.get(value.strip().lower())


def _infer_provider(model: str | None) -> str:
    m = (model or "").lower()
    if m.startswith("claude"):
        return ANTHROPIC_PROVIDER_ID
    if any(m.startswith(p) for p in _OPENAI_MODEL_PREFIXES):
        return OPENAI_PROVIDER_ID
    return ANTHROPIC_PROVIDER_ID


def resolve_model_provider(
    model: str | None, explicit_provider: str | None = None
) -> tuple[str, str | None]:
    """Decide the provider and strip any `provider/` prefix off the model.

    Returns (provider_id, bare_model). `bare_model` is the model string to send
    to the SDK (without the `provider/` prefix); None means "use the provider's
    default model".
    """
    bare = model
    ref_provider: str | None = None
    if model and "/" in model:
        head, _, tail = model.partition("/")
        ref_provider = normalize_provider(head)
        if ref_provider is not None:
            bare = tail or None

    provider = normalize_provider(explicit_provider) or ref_provider or _infer_provider(bare)
    return provider, bare


def make_provider(
    provider_id: str,
    model: str | None,
    api_key: str | None,
    max_tokens: int = 1024,
) -> VisionProvider:
    """Instantiate the provider, defaulting the model when none was given."""
    resolved_model = model or DEFAULT_MODELS[provider_id]
    if provider_id == OPENAI_PROVIDER_ID:
        from .openai_provider import OpenAIProvider

        return OpenAIProvider(model=resolved_model, api_key=api_key, max_tokens=max_tokens)
    return AnthropicProvider(model=resolved_model, api_key=api_key, max_tokens=max_tokens)


__all__ = [
    "AnthropicProvider",
    "VisionProvider",
    "ANTHROPIC_PROVIDER_ID",
    "OPENAI_PROVIDER_ID",
    "API_KEY_ENV",
    "DEFAULT_MODELS",
    "normalize_provider",
    "resolve_model_provider",
    "make_provider",
]
