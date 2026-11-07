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
