from secdogie_policy import contains_phrase, normalize_phrase


def test_normal_form_is_idempotent():
    for s in ["Mass  Register", "ＭＡＳＳ", "  a  ", "批量注册", "Straße", "ﬁle"]:
        n = normalize_phrase(s)
        assert normalize_phrase(n) == n


def test_only_ascii_letters_are_lowered():
    assert normalize_phrase("ÀB") == "Àb"


def test_contains_phrase_matches_normalized_text():
    assert contains_phrase("Please MASS   Register 50 accounts", "mass register")
    assert contains_phrase("请帮我批量注册账号", "批量注册")
    assert not contains_phrase("register one account", "mass register")
    assert not contains_phrase("anything", "   ")


def test_citadel_normalizes_phrases_exactly_like_the_policy_package():
    """citadel/rules.py carries its own copy (citadel does not depend on this
    package); the two must agree on every vector input."""
    import json
    from pathlib import Path

    import pytest

    rules = pytest.importorskip("secdogie_citadel.rules")
    vectors = json.loads((Path(__file__).resolve().parents[2] / "fixtures" / "vectors" / "policy.json")
                         .read_text(encoding="utf-8"))
    for case in vectors["normalize"]:
        assert rules.normalize_phrase(case["input"]) == normalize_phrase(case["input"]), case["input"]
