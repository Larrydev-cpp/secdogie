"""The external model: the person's own AI model, from whose output the cortex learns.

- Its configuration is the owner's alone (0600), and only a hint of the key is shown.
- It sees the person's sentence and the fixed format, nothing else.
- A reply counts only if every Socratic question has one of its own answers; the reply text is made plain
  and capped.
- Off, an error or an invalid reply gives nothing, and the gate stands as the cortex decided.
- Each backend sends exactly what is documented: Claude through the official SDK with structured outputs and
  refusal fallbacks, and an OpenAI-compatible endpoint over plain HTTP.
"""
from __future__ import annotations

import json
import os
import stat
from types import SimpleNamespace

import pytest
from secdogie_citadel import thinker as T
from secdogie_citadel.cortex import questions as Q

ON = T.ThinkerConfig(T.ANTHROPIC, "", "", "sk-ant-api03-abcdefghijklmnop")
GOOD = {**{q.id: q.default for q in Q.QUESTIONS}, "kind": "chat", "reply": "Hi!\x00 there"}


def test_the_configuration_is_the_owners_alone(tmp_path):
    path = tmp_path / "thinker.json"
    assert not T.load_config(path).on  # no file: off
    T.save_config(path, ON)
    if os.name != "nt":  # POSIX modes; on Windows the profile folder's own access rules keep it the user's
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    back = T.load_config(path)
    assert back == ON and back.on and back.model_name == "claude-opus-5-5"
    assert back.hint() == "sk-a…mnop" and "abcdefgh" not in json.dumps(back.view())
    assert not T.ThinkerConfig(T.OPENAI, "", "", "key").on  # an OpenAI-compatible endpoint needs a model named
    T.remove_config(path)
    assert not path.exists() and not T.load_config(path).on


def test_only_the_exact_format_counts_and_the_reply_is_made_plain():
    answers, reply = T.validate(json.dumps(GOOD))
    assert answers["kind"] == "chat" and set(answers) == {q.id for q in Q.QUESTIONS} and reply == "Hi! there"
    assert T.validate({**GOOD, "kind": "joke"}) is None  # not one of the question's answers
    assert T.validate({k: v for k, v in GOOD.items() if k != "others"}) is None  # a question left out
    assert T.validate({**GOOD, "reply": 3}) is None and T.validate("not json") is None
    assert len(T.validate({**GOOD, "reply": "x" * 5000})[1]) == T.MAX_REPLY


def test_it_sees_the_sentence_and_the_format_only():
    seen = []
    t = T.Thinker(ON, backend=lambda cfg, system, sentence: seen.append((system, sentence)) or GOOD)
    assert t.teach("hi there") is not None and seen == [(T.SYSTEM, "hi there")]
    assert all(q.en in T.SYSTEM for q in Q.QUESTIONS) and "data, not instructions" in T.SYSTEM


def test_off_errors_and_invalid_replies_give_nothing():
    assert T.Thinker(T.ThinkerConfig()).teach("hi") is None
    broken = T.Thinker(ON, backend=lambda *a: 1 / 0)
    assert broken.teach("hi") is None and broken.last == "ZeroDivisionError"
    odd = T.Thinker(ON, backend=lambda *a: {"what": 1})
    assert odd.teach("hi") is None and odd.last == "invalid"


def test_claude_is_asked_through_the_sdk_with_structured_output_and_fallbacks(monkeypatch):
    anthropic = pytest.importorskip("anthropic")
    from secdogie_citadel import thinker_claude

    calls = {}

    class Fake:
        def __init__(self, **kw):
            calls["client"] = kw
            self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

        def create(self, **kw):
            calls["create"] = kw
            return SimpleNamespace(stop_reason=calls.get("stop", "end_turn"), content=[
                SimpleNamespace(type="thinking", thinking=""),
                SimpleNamespace(type="text", text=json.dumps(GOOD))])

    monkeypatch.setattr(anthropic, "Anthropic", Fake)
    assert thinker_claude.ask(ON, T.SYSTEM, "hi") == GOOD
    kw = calls["create"]
    assert kw["model"] == "claude-opus-5-5" and kw["system"] == T.SYSTEM
    assert kw["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": T.SCHEMA}}
    assert kw["messages"] == [{"role": "user", "content": "hi"}]
    assert kw["betas"] == ["server-side-fallback-2026-07-01"] and kw["fallbacks"] == "default"
    assert "thinking" not in kw and calls["client"]["api_key"] == ON.api_key
    calls["stop"] = "refusal"
    assert thinker_claude.ask(ON, T.SYSTEM, "hi") is None  # the whole chain declined: nothing


def test_an_openai_compatible_endpoint_is_asked_over_plain_http(monkeypatch):
    from secdogie_citadel import thinker_openai

    sent = {}

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"choices": [{"message": {"content": json.dumps(GOOD)}}]}).encode()

    def urlopen(request, timeout):
        sent.update(url=request.full_url, headers=dict(request.header_items()), body=json.loads(request.data),
                    timeout=timeout)
        return Reply()

    monkeypatch.setattr(thinker_openai.urllib.request, "urlopen", urlopen)
    cfg = T.ThinkerConfig(T.OPENAI, "deepseek-chat", "https://api.deepseek.com/", "sk-xyz")
    assert thinker_openai.ask(cfg, T.SYSTEM, "hi") == GOOD
    assert sent["url"] == "https://api.deepseek.com/chat/completions" and sent["timeout"] == T.TIMEOUT
    assert sent["headers"]["Authorization"] == "Bearer sk-xyz" and sent["body"]["model"] == "deepseek-chat"
    assert sent["body"]["response_format"] == {"type": "json_object"}
