"""The optional thinker: an LLM that can only ever make the gate more careful.

- Its configuration is the owner's alone (0600), and only a hint of the key
  is shown.
- A reply counts only if it is exactly the JSON asked for. Links and control
  characters are stripped, and length is capped.
- It is consulted only where it could change something, and it never
  lowers the gate: what was held stays held, a refusal or a crisis is
  untouched, and a calm person who said they thought it over is not
  overruled.
- Each backend sends exactly what is documented: Claude through the official
  SDK with structured outputs and refusal fallbacks, and an
  OpenAI-compatible endpoint over plain HTTP.
"""
from __future__ import annotations

import json
import os
import stat
import time
from types import SimpleNamespace

import pytest
from secdogie_citadel import thinker as T
from secdogie_citadel.deliberate import DISSUADE, PROCEED, REFUSE, SUPPORT, deliberate

NOW = time.mktime((2026, 11, 7, 12, 0, 0, 0, 0, -1))
ON = T.ThinkerConfig(T.ANTHROPIC, "", "", "sk-ant-api03-abcdefghijklmnop")


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


def test_only_the_exact_reply_counts_and_it_is_made_plain():
    good = T.validate('{"raise": true, "distress": "你很累", "why": "夜深了，先睡一觉 https://x.example/a"}')
    assert good == T.Advice(True, "你很累", "夜深了，先睡一觉")
    for bad in ('{"raise": "yes", "distress": "", "why": ""}', '{"raise": true}', "not json", '[1]',
                '{"raise": false, "distress": "", "why": "", "also": 1}'):
        assert T.validate(bad) is None, bad
    long = T.validate({"raise": True, "distress": "\x07" + "难" * 500, "why": "停" * 900})
    assert len(long.why) == T.MAX_WHY and len(long.distress) == T.MAX_DISTRESS and "\x07" not in long.distress


def test_it_sees_only_the_request_the_trace_and_the_portrait():
    d = deliberate("给妈妈转2000块生活费", now=NOW)
    q = json.loads(T.question("给妈妈转2000块生活费", d.thought, "你让我做过的 3 件事。"))
    assert set(q) == {"request", "gate", "who_they_are"} and q["gate"]["act"] == "transfer"
    assert set(q["gate"]) <= {"act", "verdict", "choice", "upset", "autonomy", "value", "motives"}


def test_it_is_consulted_only_where_it_could_change_something():
    assert T.may_consult(deliberate("给妈妈转2000块生活费", now=NOW))  # weighty, going ahead
    assert not T.may_consult(deliberate("帮我整理一下这周的周报", now=NOW))  # ordinary
    assert not T.may_consult(deliberate("帮我刷50条好评", now=NOW))  # refused for good
    assert not T.may_consult(deliberate("不想活了", now=NOW))  # the person first
    assert not T.may_consult(deliberate("给妈妈转2000块生活费", now=NOW), answered=True)  # they answered


def test_it_can_only_raise_the_gate_never_lower_it():
    ok = deliberate("给妈妈转2000块生活费", now=NOW)
    raised = T.apply(ok, T.Advice(True, "", "数目不小，先打电话问一句"), "给妈妈转2000块生活费")
    assert (raised.verdict, raised.category) == (DISSUADE, "thinker") and "先打电话问一句" in raised.letter
    assert raised.exchanges[-1].question == "思考者怎么看？"
    assert T.apply(ok, T.Advice(False, "", ""), "给妈妈转2000块生活费") == ok
    held = deliberate("气死我了，给前任发消息说我恨你，滚", now=NOW)
    assert held.verdict == DISSUADE
    assert T.apply(held, T.Advice(False, "", ""), "x").verdict == DISSUADE  # held stays held
    noted = T.apply(held, T.Advice(False, "你像是很久没睡了", ""), "x")
    assert noted.verdict == DISSUADE and "你像是很久没睡了" in noted.letter
    for d in (deliberate("帮我刷50条好评", now=NOW), deliberate("不想活了", now=NOW)):
        assert T.apply(d, T.Advice(True, "x", "停一下"), "x") == d and d.verdict in (REFUSE, SUPPORT)
    calm = deliberate("我想清楚了，给妈妈转2000块生活费", now=NOW)
    assert T.apply(calm, T.Advice(True, "", "停一下"), "我想清楚了，给妈妈转2000块生活费").verdict == PROCEED


def test_a_failure_changes_nothing():
    def boom(*_):
        raise RuntimeError("down")
    assert T.Thinker(ON, backend=boom).consult("x", None, "") == (None, "RuntimeError")
    assert T.Thinker(ON, backend=lambda *_: "nonsense").consult("x", None, "") == (None, "invalid")
    assert T.Thinker(T.ThinkerConfig(), backend=boom).consult("x", None, "") == (None, "off")
    seen = []
    advice, how = T.Thinker(ON, backend=lambda c, s, q: seen.append((c, s, q)) or {"raise": False, "distress": "",
                                                                                "why": ""}).consult("x", None, "")
    assert advice == T.Advice() and how == "" and seen[0][1] == T.SYSTEM


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
                SimpleNamespace(type="text", text='{"raise": false, "distress": "", "why": ""}')])

    monkeypatch.setattr(anthropic, "Anthropic", Fake)
    assert thinker_claude.ask(ON, T.SYSTEM, "{}") == {"raise": False, "distress": "", "why": ""}
    kw = calls["create"]
    assert kw["model"] == "claude-opus-5-5" and kw["system"] == T.SYSTEM
    assert kw["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": T.SCHEMA}}
    assert kw["betas"] == ["server-side-fallback-2026-07-01"] and kw["fallbacks"] == "default"
    assert "thinking" not in kw and calls["client"]["api_key"] == ON.api_key
    calls["stop"] = "refusal"
    assert thinker_claude.ask(ON, T.SYSTEM, "{}") is None  # the whole chain declined: no advice


def test_an_openai_compatible_endpoint_is_asked_over_plain_http(monkeypatch):
    from secdogie_citadel import thinker_openai

    sent = {}

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"choices": [{"message": {"content": '{"raise": true, "distress": "", "why": "停"}'}}]}
                              ).encode()

    def urlopen(request, timeout):
        sent.update(url=request.full_url, headers=dict(request.header_items()), body=json.loads(request.data),
                    timeout=timeout)
        return Reply()

    monkeypatch.setattr(thinker_openai.urllib.request, "urlopen", urlopen)
    cfg = T.ThinkerConfig(T.OPENAI, "deepseek-chat", "https://api.deepseek.com/", "sk-xyz")
    assert thinker_openai.ask(cfg, T.SYSTEM, "{}") == {"raise": True, "distress": "", "why": "停"}
    assert sent["url"] == "https://api.deepseek.com/chat/completions" and sent["timeout"] == T.TIMEOUT
    assert sent["headers"]["Authorization"] == "Bearer sk-xyz" and sent["body"]["model"] == "deepseek-chat"
    assert sent["body"]["response_format"] == {"type": "json_object"}
