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


# --- review -> revise -> review (deliberate) -------------------------------

from secdogie_citadel.socratic import (  # noqa: E402
    CONTRADICTION,
    OVERLONG,
    POLLING,
    UNATTENDED_POSTING,
    deliberate,
    review,
)


def _accepted_revision(text):
    d = deliberate(text)
    assert d.outcome == "accept" and d.revised, (text, d)
    # the accepted text passes the very same review -- no check was loosened
    assert review(d.text).accepted
    # and the original still would not
    assert not review(text).accepted
    return d


def test_plural_seconds_are_now_caught():
    r = review("check the inbox every 2 seconds")
    assert POLLING in r.codes


def test_polling_becomes_backoff():
    d = _accepted_revision("check the inbox every 2 seconds forever")
    assert "backoff" in d.text and "forever" not in d.text and "every 2 seconds" not in d.text
    zh = _accepted_revision("每 1 秒不停轮询订单状态")
    assert "退避" in zh.text and "不停" not in zh.text


def test_unattended_posting_gains_a_confirmation():
    d = _accepted_revision("post the weekly report to the team channel automatically without asking")
    assert "ask the user to confirm" in d.text and "automatically" not in d.text
    zh = _accepted_revision("自动替我回复所有评论")
    assert "先请用户确认" in zh.text and "自动" not in zh.text


def test_read_only_plus_change_is_sequenced_with_confirmation():
    d = _accepted_revision("read-only: look at the config and delete the stale entries")
    assert "only after the user explicitly confirms" in d.text
    zh = _accepted_revision("只读检查配置,然后删除过期项")
    assert "明确确认后再做" in zh.text


def test_overlong_is_split_into_ordered_steps():
    text = " and then ".join(f"step {i}" for i in range(9))
    d = deliberate(text)
    assert d.outcome == "decompose" and d.subgoals == tuple(f"step {i}" for i in range(9))
    long_text = " ".join(f"Sentence number {i} describes one part of the job." for i in range(30))
    d2 = deliberate(long_text)
    assert d2.outcome == "decompose" and len(d2.subgoals) >= 2
    assert all(len(s) <= 600 for s in d2.subgoals)


def test_only_unrewritable_findings_need_input():
    assert deliberate("   ").outcome == "needs_input"
    critic = lambda text: "the critic disagrees"  # noqa: E731
    d = deliberate("do the thing", extra_checks=[critic])
    assert d.outcome == "needs_input" and d.reasons == ("the critic disagrees",)
    clean = deliberate("open the settings page and read the version")
    assert clean.outcome == "accept" and not clean.revised


def test_codes_name_the_findings():
    r = review("read-only, but post it automatically, poll every 1 s")
    assert {CONTRADICTION, UNATTENDED_POSTING, POLLING} <= set(r.codes)
    assert OVERLONG not in r.codes
