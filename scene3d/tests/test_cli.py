import secdogie_scene3d.cli as cli_mod
from conftest import FakeModel

from secdogie_scene3d.aggregate import SceneAnalysis


def _stub_heavy(monkeypatch, captured):
    """Replace model construction / file IO / the pipeline so a CLI run reaches
    the argument-wiring logic without an SDK, network, or real image file."""
    monkeypatch.setattr(cli_mod.model_mod, "build_model_pool",
                        lambda provider, model, keys: [FakeModel("{}")])

    def fake_make(provider, model, key):
        captured["aggregator_model"] = model
        captured["aggregator_provider"] = provider
        return FakeModel("{}")

    monkeypatch.setattr(cli_mod.model_mod, "make_scene_model", fake_make)
    monkeypatch.setattr(cli_mod, "load_viewpoint",
                        lambda path, label=None: captured.setdefault("views", []).append((label, path)))
    monkeypatch.setattr(cli_mod, "analyze_scene",
                        lambda *a, **k: SceneAnalysis(raw_text="", data={"summary": "ok"}, observations=[]))


def test_cross_provider_aggregator_is_rejected(capsys):
    rc = cli_mod.main(["--api-key", "sk-test", "--model", "claude-sonnet-5",
                       "--aggregator-model", "gpt-5.5", "front.png"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "aggregator-model" in err and "openai" in err and "anthropic" in err


def test_same_provider_aggregator_is_accepted(monkeypatch, capsys):
    captured = {}
    _stub_heavy(monkeypatch, captured)
    rc = cli_mod.main(["--api-key", "sk-test", "--model", "claude-sonnet-5",
                       "--aggregator-model", "claude-opus-4-8", "front.png"])
    assert rc == 0
    assert captured["aggregator_model"] == "claude-opus-4-8"
    assert captured["aggregator_provider"] == "anthropic"


def test_provider_prefixed_aggregator_model_is_stripped(monkeypatch):
    captured = {}
    _stub_heavy(monkeypatch, captured)
    rc = cli_mod.main(["--api-key", "sk-test", "--model", "claude-sonnet-5",
                       "--aggregator-model", "anthropic/claude-opus-4-8", "front.png"])
    assert rc == 0
    # the provider/ prefix must be stripped before it reaches the SDK
    assert captured["aggregator_model"] == "claude-opus-4-8"


def test_default_aggregator_uses_worker_model(monkeypatch):
    captured = {}
    _stub_heavy(monkeypatch, captured)
    rc = cli_mod.main(["--api-key", "sk-test", "--model", "claude-sonnet-5", "front.png"])
    assert rc == 0
    assert captured["aggregator_model"] == "claude-sonnet-5"
