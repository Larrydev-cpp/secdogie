"""Studio speaks Chinese and English: the person's choice is kept, the
environment and locale decide until they choose, and Studio's own words
follow it."""
from __future__ import annotations

from types import SimpleNamespace

from secdogie_studio.app import Presence, duration_words, greeting, welcome_moment, work_words
from secdogie_studio.companion import explain_errors, rollback_words, try_rules
from secdogie_studio.words import detect, load, pick, save


def test_the_language_follows_the_choice_then_the_environment_then_the_locale(tmp_path):
    assert detect({"SECDOGIE_LANG": "en"}) == "en" and detect({"SECDOGIE_LANG": "zh", "LANG": "en_US"}) == "zh"
    assert detect({"LANG": "en_GB.UTF-8"}) == "en" and detect({"LANG": "zh_CN.UTF-8"}) == "zh"
    assert detect({"LANG": "C.UTF-8"}) == "zh"  # anything but English: Chinese
    assert detect({}, getlocale=lambda: ("English_United States", "1252")) == "en"
    assert load(tmp_path, {"SECDOGIE_LANG": "zh"}) == "zh"
    save(tmp_path, "en")
    assert load(tmp_path, {"SECDOGIE_LANG": "zh"}) == "en"  # the person's choice wins
    assert pick("en", "好", "fine") == "fine" and pick("zh", "好", "fine") == "好"


def test_studios_own_words_follow_the_language():
    assert duration_words(3725, "en") == "1 hour 2 minutes" and duration_words(3725) == "1 小时 2 分钟"
    assert work_words({"active": 1, "pending": 2}, "en") == "1 in progress, 2 queued"
    assert work_words({}, "en") == "Nothing handed to me yet."
    assert greeting(0, "en") and welcome_moment("en").says == "Hello. Let's get to know each other."
    assert rollback_words("x; rollback: none stated", "en") == "no way back was given"
    assert explain_errors(["mesh:mesh"], "en") == ["these rules are for another mesh"]
    assert try_rules({}, "整理周报", "en") == "It passes: I would start right away."


class _Controller:
    def __init__(self, root):
        self.profile = SimpleNamespace(root=root, name=lambda: "")

    def snapshot(self):
        return SimpleNamespace(initialized=False, busy="", node_state="stopped", node_did="", app_did="",
                               operator_did="", listen="", roles=(), log=(), returncode=None, error="")


def test_the_choice_is_kept_beside_the_profile(tmp_path):
    presence = Presence(_Controller(tmp_path), idle=lambda: 10.0, lang="zh")
    assert presence.tick()["lang"] == "zh"
    presence.set_language("en")
    s = presence.tick()
    assert s["lang"] == "en" and s["card"]["says"] == "Hello. Let's get to know each other."
    presence.welcome("short", "short")
    assert presence.tick()["feedback"].startswith("The passphrase needs at least 8 characters")
    presence.set_language("fr")  # not one of ours: nothing changes
    assert presence.tick()["lang"] == "en"
    assert Presence(_Controller(tmp_path), idle=lambda: 10.0).lang == "en"  # read back from ui.json
