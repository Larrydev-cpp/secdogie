from __future__ import annotations

from secdogie_citadel import socratic


def test_accepts_a_clean_instruction():
    r = socratic.review("Open the CAD file and read the current layer name.")
    assert r.accepted
    assert r.reasons == ()


def test_empty_is_revised():
    r = socratic.review("   ")
    assert not r.accepted
    assert any("empty" in reason for reason in r.reasons)


def test_read_only_plus_mutation_is_contradiction():
    r = socratic.review("Keep it read-only but delete the temp files.")
    assert not r.accepted
    assert any("contradiction" in reason for reason in r.reasons)
    assert r.suggestion


def test_chinese_contradiction():
    r = socratic.review("只读检查，然后删除缓存")
    assert not r.accepted
    assert any("contradiction" in reason for reason in r.reasons)


def test_tight_poll_is_flagged():
    assert not socratic.review("poll the endpoint every 1 second").accepted
    assert not socratic.review("每 2 秒 轮询一次状态").accepted
    assert not socratic.review("retry the request continuously forever").accepted
    # a reasonable interval is fine
    assert socratic.review("check the build every 10 minutes").accepted


def test_overlong_is_decomposed():
    long_instruction = " and then ".join(f"do step {i}" for i in range(8))
    r = socratic.review(long_instruction)
    assert not r.accepted
    assert any("overlong" in reason for reason in r.reasons)
    assert "sub-goal" in r.suggestion or "DAG" in r.suggestion


def test_extra_checks_can_add_opinions():
    def no_shouting(text):
        if text.isupper():
            return ("all caps reads as shouting", "Use normal case.")
        return None

    r = socratic.review("DO THIS NOW", extra_checks=[no_shouting])
    assert not r.accepted
    assert any("shouting" in reason for reason in r.reasons)


def test_extra_check_string_form():
    r = socratic.review("fine text", extra_checks=[lambda t: "always complain"])
    assert not r.accepted
    assert "always complain" in r.reasons


def test_unattended_posting_is_revised_en():
    r = socratic.review("Automatically post this reply to the thread without asking.")
    assert not r.accepted
    assert any("unattended posting" in reason for reason in r.reasons)
    assert "human" in r.suggestion


def test_unattended_posting_is_revised_zh():
    r = socratic.review("自动发帖并提交回复，不用询问我。")
    assert not r.accepted
    assert any("unattended posting" in reason for reason in r.reasons)


def test_on_behalf_posting_is_revised():
    r = socratic.review("Send this comment on the user's behalf silently.")
    assert not r.accepted
    assert any("unattended posting" in reason for reason in r.reasons)


def test_ordinary_submit_instruction_is_not_flagged():
    # A plain "fill and submit" with a human present must NOT be flagged --
    # the unattended qualifier is required.
    assert socratic.review("Fill in the reply box and click submit.").accepted
    assert socratic.review("填写回复框并点击提交。").accepted


def test_readonly_but_post_is_still_a_contradiction():
    r = socratic.review("Keep it read-only but post the comment.")
    assert not r.accepted
    assert any("contradiction" in reason for reason in r.reasons)


def test_record_review_appends_signed_event():
    import pytest

    pytest.importorskip("nacl")
    from secdogie_citadel.journal import Journal
    from secdogie_identity import Identity

    j = Journal(identity=Identity.generate())
    r = socratic.review("Keep it read-only but delete the temp files.")
    event = socratic.record_review(j, "Keep it read-only but delete the temp files.", r)
    assert event["kind"] == "socratic"
    assert event["body"]["verdict"] == "revise"
    ok, _ = j.verify()
    assert ok
