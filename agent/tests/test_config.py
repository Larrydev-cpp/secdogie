
import pytest
from secdogie_agent import config as config_mod


def test_cli_api_key_wins(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    r = config_mod.resolve(cli_api_key="cli-key")
    assert r.api_key == "cli-key"
    assert "--api-key" in r.api_key_source


def test_env_key_used_when_no_cli(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    r = config_mod.resolve()
    assert r.api_key == "env-key"
    assert "environment" in r.api_key_source


def test_config_file_used_when_no_cli_or_env(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SECDOGIE_MODEL", raising=False)
    cfg = tmp_path / "secdogie.env"
    cfg.write_text('ANTHROPIC_API_KEY = "file-key"\nSECDOGIE_MODEL=my-model\n# comment\n')
    r = config_mod.resolve(config_path=str(cfg))
    assert r.api_key == "file-key"  # quotes and spaces stripped
    assert r.model == "my-model"
    assert str(cfg) in r.api_key_source


def test_no_key_anywhere(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Point default search at an empty dir by using an explicit missing path.
    r = config_mod.resolve(config_path=str(tmp_path / "nope.env"))
    assert r.api_key is None


def test_model_precedence_cli_over_env(monkeypatch):
    monkeypatch.setenv("SECDOGIE_MODEL", "env-model")
    r = config_mod.resolve(cli_api_key="k", cli_model="cli-model")
    assert r.model == "cli-model"


def test_openai_model_selects_openai_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "oai-key")
    r = config_mod.resolve(cli_model="gpt-5.5")
    assert r.provider == "openai"
    assert r.env_var == "OPENAI_API_KEY"
    assert r.api_key == "oai-key"
    assert "OPENAI_API_KEY" in r.api_key_source


def test_wrong_providers_key_is_not_reused(monkeypatch):
    # An Anthropic key must not satisfy an OpenAI model, and vice versa.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ak")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    r = config_mod.resolve(cli_model="gpt-5.5")
    assert r.provider == "openai"
    assert r.api_key is None


def test_provider_slash_model_ref_strips_prefix_and_picks_key(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("SECDOGIE_MODEL", raising=False)
    cfg = tmp_path / "secdogie.env"
    cfg.write_text("OPENAI_API_KEY=file-oai\n")
    r = config_mod.resolve(cli_model="openai/gpt-5.5", config_path=str(cfg))
    assert r.provider == "openai"
    assert r.model == "gpt-5.5"  # provider/ prefix stripped for the SDK
    assert r.api_key == "file-oai"


def test_explicit_provider_flag_overrides_inference(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "oai")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SECDOGIE_MODEL", raising=False)
    r = config_mod.resolve(cli_provider="openai")
    assert r.provider == "openai"
    assert r.api_key == "oai"


def test_parse_ignores_blanks_and_comments(tmp_path):
    p = tmp_path / "c"
    p.write_text("\n# a comment\n\nANTHROPIC_API_KEY=abc\nGARBAGE LINE NO EQUALS\n")
    vals = config_mod.parse_config_file(p)
    assert vals == {"ANTHROPIC_API_KEY": "abc"}


def test_parse_missing_file_is_empty(tmp_path):
    assert config_mod.parse_config_file(tmp_path / "does-not-exist") == {}


def test_write_template_creates_private_file(tmp_path):
    target = tmp_path / "cfg"
    written = config_mod.write_template(target)
    assert written == target
    assert "ANTHROPIC_API_KEY=" in target.read_text()
    # 0600 on POSIX
    mode = target.stat().st_mode & 0o777
    assert mode == 0o600


def test_write_template_refuses_to_clobber(tmp_path):
    target = tmp_path / "cfg"
    target.write_text("existing")
    with pytest.raises(FileExistsError):
        config_mod.write_template(target)
    assert target.read_text() == "existing"  # untouched


def test_has_configured_api_key_false_when_empty(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(config_mod, "default_config_paths", lambda: [tmp_path / "missing.env"])
    assert config_mod.has_configured_api_key() is False


def test_has_configured_api_key_true_from_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-present")
    assert config_mod.has_configured_api_key() is True


def test_has_configured_api_key_true_from_file(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    p = tmp_path / "secdogie.env"
    p.write_text("OPENAI_API_KEY=sk-file\n", encoding="utf-8")
    monkeypatch.setattr(config_mod, "default_config_paths", lambda: [p])
    assert config_mod.has_configured_api_key() is True


def test_has_configured_api_key_custom_suffix(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    p = tmp_path / "secdogie.env"
    p.write_text("DEEPSEEK_API_KEY=sk-ds\n", encoding="utf-8")
    monkeypatch.setattr(config_mod, "default_config_paths", lambda: [p])
    assert config_mod.has_configured_api_key() is True


def test_write_api_key_creates_and_updates(tmp_path):
    target = tmp_path / "secdogie.env"
    path = config_mod.write_api_key("sk-aaa", provider="anthropic", path=target)
    assert path == target
    text = target.read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY=sk-aaa" in text
    assert "SECDOGIE_PROVIDER=anthropic" in text
    assert "SECDOGIE_MODEL=" in text

    config_mod.write_api_key("sk-bbb", provider="anthropic", model="claude-x", path=target)
    text = target.read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY=sk-bbb" in text
    assert "SECDOGIE_MODEL=claude-x" in text
    assert text.count("ANTHROPIC_API_KEY=") == 1


def test_openai_key_alone_is_enough_without_model(monkeypatch, tmp_path):
    """GUI first-run: user pastes an OpenAI key, leaves model blank, hits Start."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("SECDOGIE_MODEL", raising=False)
    monkeypatch.delenv("SECDOGIE_PROVIDER", raising=False)
    cfg = tmp_path / "secdogie.env"
    cfg.write_text("OPENAI_API_KEY=sk-oai-only\n", encoding="utf-8")
    r = config_mod.resolve(config_path=str(cfg))
    assert r.api_key == "sk-oai-only"
    assert r.provider == "openai"


def test_openrouter_key_prefix_routes_even_under_openai_name(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("SECDOGIE_MODEL", raising=False)
    monkeypatch.delenv("SECDOGIE_PROVIDER", raising=False)
    cfg = tmp_path / "secdogie.env"
    cfg.write_text("OPENAI_API_KEY=sk-or-v1-abc123456\n", encoding="utf-8")
    r = config_mod.resolve(config_path=str(cfg))
    assert r.api_key.startswith("sk-or-")
    assert r.provider == "openrouter"
    assert r.model  # default OpenRouter vendor/model id


def test_write_sk_or_key_stores_openrouter(tmp_path):
    target = tmp_path / "secdogie.env"
    config_mod.write_api_key("sk-or-v1-secret", provider="openai", path=target)
    text = target.read_text(encoding="utf-8")
    assert "OPENROUTER_API_KEY=sk-or-v1-secret" in text
    assert "SECDOGIE_PROVIDER=openrouter" in text


def test_saved_openrouter_config_keeps_vendor_model(monkeypatch, tmp_path):
    """After the GUI Save-key path, Start must send vendor/model to OpenRouter."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("SECDOGIE_MODEL", raising=False)
    monkeypatch.delenv("SECDOGIE_PROVIDER", raising=False)
    target = tmp_path / "secdogie.env"
    config_mod.write_api_key("sk-or-v1-secret", provider="openrouter", path=target)
    r = config_mod.resolve(config_path=str(target))
    assert r.provider == "openrouter"
    assert r.api_key == "sk-or-v1-secret"
    assert r.model and "/" in r.model, r.model
    assert not r.model.startswith("openrouter/")
