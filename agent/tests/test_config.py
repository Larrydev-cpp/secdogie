from pathlib import Path

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
