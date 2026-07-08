import sys
import types

import pytest

from secdogie_agent.providers import (
    ANTHROPIC_PROVIDER_ID,
    DEFAULT_MODELS,
    OPENAI_PROVIDER_ID,
    make_provider,
    normalize_provider,
    resolve_model_provider,
)


@pytest.mark.parametrize(
    "model,expected",
    [
        ("claude-sonnet-5", ANTHROPIC_PROVIDER_ID),
        ("claude-opus-4-8", ANTHROPIC_PROVIDER_ID),
        ("gpt-5.5", OPENAI_PROVIDER_ID),
        ("gpt-4o", OPENAI_PROVIDER_ID),
        ("o3", OPENAI_PROVIDER_ID),
        ("o1-mini", OPENAI_PROVIDER_ID),
        ("chatgpt-4o-latest", OPENAI_PROVIDER_ID),
        ("some-unknown-model", ANTHROPIC_PROVIDER_ID),  # default keeps back-compat
        (None, ANTHROPIC_PROVIDER_ID),
    ],
)
def test_infer_provider_from_model_prefix(model, expected):
    provider, bare = resolve_model_provider(model)
    assert provider == expected
    assert bare == model  # no provider/ prefix -> model unchanged


def test_provider_slash_model_ref_strips_prefix():
    provider, bare = resolve_model_provider("openai/gpt-5.5")
    assert provider == OPENAI_PROVIDER_ID
    assert bare == "gpt-5.5"


def test_anthropic_ref_strips_prefix():
    provider, bare = resolve_model_provider("anthropic/claude-sonnet-5")
    assert provider == ANTHROPIC_PROVIDER_ID
    assert bare == "claude-sonnet-5"


def test_explicit_provider_overrides_model_prefix():
    # user forces Anthropic even though the model id looks like OpenAI's
    provider, bare = resolve_model_provider("gpt-5.5", explicit_provider="anthropic")
    assert provider == ANTHROPIC_PROVIDER_ID
    assert bare == "gpt-5.5"


def test_unknown_ref_head_is_not_a_provider():
    # "foo/" is not a known provider, so the whole string is kept and inferred
    provider, bare = resolve_model_provider("foo/bar")
    assert provider == ANTHROPIC_PROVIDER_ID
    assert bare == "foo/bar"


def test_normalize_provider_aliases():
    assert normalize_provider("OpenAI") == OPENAI_PROVIDER_ID
    assert normalize_provider("gpt") == OPENAI_PROVIDER_ID
    assert normalize_provider("claude") == ANTHROPIC_PROVIDER_ID
    assert normalize_provider("nope") is None
    assert normalize_provider(None) is None


def _install_fake_sdk(monkeypatch, name, client_attr):
    """Put a fake provider SDK module in sys.modules so the provider ctor can
    build a client without the real package installed."""
    mod = types.ModuleType(name)

    class _Client:
        def __init__(self, api_key=None):
            self.api_key = api_key

    setattr(mod, client_attr, _Client)
    monkeypatch.setitem(sys.modules, name, mod)


def test_make_provider_openai_applies_default_model(monkeypatch):
    _install_fake_sdk(monkeypatch, "openai", "OpenAI")
    from secdogie_agent.providers.openai_provider import OpenAIProvider

    p = make_provider(OPENAI_PROVIDER_ID, None, "sk-x")
    assert isinstance(p, OpenAIProvider)
    assert p.model == DEFAULT_MODELS[OPENAI_PROVIDER_ID]


def test_make_provider_anthropic(monkeypatch):
    _install_fake_sdk(monkeypatch, "anthropic", "Anthropic")
    from secdogie_agent.providers.anthropic_provider import AnthropicProvider

    p = make_provider(ANTHROPIC_PROVIDER_ID, "claude-sonnet-5", "sk-y")
    assert isinstance(p, AnthropicProvider)
    assert p.model == "claude-sonnet-5"
