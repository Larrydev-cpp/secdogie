"""The Gate page's bridge, without Qt: how the gate thinks about a sentence
(the same path the node takes), and the optional thinker's settings.

- ``think_through`` lays the trace out as the page shows it: the verdict and
  its words, the motives with their probabilities and what each rests on,
  every path (the chosen one, the one asked for, the irreversible ones), the
  letter's paragraphs, the cooling-off and the hour. The draft rules count,
  and an ordinary request just goes ahead.
- The thinker's key is saved only for its owner, never shown again, and
  removing it turns the thinker off.
"""
from __future__ import annotations

import os
import stat
import time

from secdogie_studio.app import Presence
from secdogie_studio.companion import RULES_TEMPLATE, parse_rules, think_through

NOW = time.mktime((2026, 11, 7, 12, 0, 0, 0, 0, -1))


def test_a_sentence_is_thought_through_and_laid_out_for_the_page():
    v = think_through(None, "和他分手了，把我们的照片全删了", "night", now=NOW)
    assert (v["verdict"], v["verdictWords"], v["hour"]) == ("dissuade", "劝一劝", "night")
    assert v["says"] == "「我们的照片」，我想先替你留着。" and v["until"] == "11 月 14 日"
    top = v["motives"][0]
    assert top["name"].startswith("不想再看到它们") and top["p"] > 0.8 and "你说到分开" in top["because"]
    chosen = [o for o in v["options"] if o["chosen"]]
    asked = [o for o in v["options"] if o["asked"]]
    assert chosen[0]["name"] == "封存起来" and asked[0]["undo"] and chosen[0]["eu"] > asked[0]["eu"]
    assert v["paragraphs"] and v["says"] not in v["paragraphs"] and len(v["exchanges"]) >= 3
    day = think_through(None, "和他分手了，把我们的照片全删了", "day", now=NOW)
    assert day["upset"] < v["upset"]  # the small hours weigh on it


def test_the_draft_rules_count_and_an_ordinary_request_goes_ahead():
    r = think_through(parse_rules(RULES_TEMPLATE), "帮我批量注册十个账号", "day", now=NOW)
    assert r["verdict"] == "clarify" and r["rule"] and r["says"].startswith("你定过一条规则")
    plain = think_through(None, "帮我整理一下这周的周报", now=NOW)
    assert plain["verdict"] == "proceed" and not plain["weighty"] and plain["motives"] == []
    assert think_through(None, "  ") == {}


class _Profile:
    def __init__(self, root):
        self.thinker_config = root / "thinker.json"


class _Controller:
    def __init__(self, root):
        self.profile = _Profile(root)


def test_the_thinker_is_saved_for_its_owner_only_and_never_shown_again(tmp_path):
    presence = Presence(_Controller(tmp_path), idle=lambda: 10.0, companion_factory=lambda p, r: None)
    assert presence._thinker_view()["on"] is False
    presence.set_thinker("anthropic", "", "", "sk-ant-api03-0123456789abcdef")
    view = presence._thinker_view()
    assert view["on"] and view["model"] == "claude-opus-5-5" and view["hint"] == "sk-a…cdef"
    assert "0123456789" not in str(view)
    assert stat.S_IMODE(os.stat(tmp_path / "thinker.json").st_mode) == 0o600
    presence.set_thinker("openai", "deepseek-chat", "https://api.deepseek.com", "")  # an empty key keeps the saved one
    view = presence._thinker_view()
    assert view["provider"] == "openai" and view["on"] and view["base"] == "https://api.deepseek.com"
    presence.clear_thinker()
    assert not presence._thinker_view()["on"] and not (tmp_path / "thinker.json").exists()
