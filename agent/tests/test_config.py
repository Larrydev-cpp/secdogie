"""Config resolution + portable write-target tests."""
from pathlib import Path

from secdogie_agent import config as config_mod


def test_parse_config_file_basic(tmp_path: Path):
    p = tmp_path / "cfg"
    p.write_text(
        "# comment\n\nANTHROPIC_API_KEY=sk-test\nSECDOGIE_MODEL=claude-sonnet-5\n",
        encoding="utf-8",
    )
    vals = config_mod.parse_config_file(p)
    assert vals["ANTHROPIC_API_KEY"] == "sk-test"
    assert vals["SECDOGIE_MODEL"] == "claude-sonnet-5"


def test_parse_missing_file(tmp_path: Path):
    assert config_mod.parse_config_file(tmp_path / "nope") == {}


def test_resolve_cli_wins(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("SECDOGIE_MODEL", raising=False)
    r = config_mod.resolve(cli_api_key="from-cli", cli_model="claude-x")
    assert r.api_key == "from-cli"
    assert r.model == "claude-x"
    assert r.api_key_source == "--api-key argument"


def test_resolve_env_over_file(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    p = tmp_path / "secdogie.env"
    p.write_text("ANTHROPIC_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    r = config_mod.resolve()
    assert r.api_key == "from-env"
    assert "environment" in r.api_key_source


def test_write_api_key_creates_and_updates(tmp_path: Path):
    target = tmp_path / "secdogie.env"
    path = config_mod.write_api_key("sk-aaa", provider="anthropic", path=target)
    assert path == target
    text = target.read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY=sk-aaa" in text

    config_mod.write_api_key("sk-bbb", provider="anthropic", model="claude-x", path=target)
    text = target.read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY=sk-bbb" in text
    assert "SECDOGIE_MODEL=claude-x" in text
    assert text.count("ANTHROPIC_API_KEY=") == 1


def test_has_configured_api_key_false_when_empty(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(config_mod, "default_config_paths", lambda: [tmp_path / "missing.env"])
    assert config_mod.has_configured_api_key() is False


def test_has_configured_api_key_true_from_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-present")
    assert config_mod.has_configured_api_key() is True


def test_has_configured_api_key_true_from_file(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    p = tmp_path / "secdogie.env"
    p.write_text("OPENAI_API_KEY=sk-file\n", encoding="utf-8")
    monkeypatch.setattr(config_mod, "default_config_paths", lambda: [p])
    assert config_mod.has_configured_api_key() is True


def test_has_configured_api_key_custom_suffix(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    p = tmp_path / "secdogie.env"
    p.write_text("DEEPSEEK_API_KEY=sk-ds\n", encoding="utf-8")
    monkeypatch.setattr(config_mod, "default_config_paths", lambda: [p])
    assert config_mod.has_configured_api_key() is True
