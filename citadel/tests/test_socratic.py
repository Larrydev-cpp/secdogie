"""Gate 1's review only looks at the shape of a request: is there anything in it, is it so long it is really
several goals. What a request means is the cortex's to understand; nothing here reads words."""
from __future__ import annotations

from secdogie_citadel import socratic
from secdogie_citadel.journal import Journal
from secdogie_identity import Allowlist, Identity


def test_accepts_a_clean_instruction():
    r = socratic.review("Open the CAD file and read the current layer name.")
    assert r.accepted and r.reasons == ()


def test_empty_is_revised():
    r = socratic.review("   ")
    assert not r.accepted and any("empty" in reason for reason in r.reasons)


def test_overlong_is_decomposed():
    r = socratic.review("整理下载文件夹，" * 100)
    assert not r.accepted and any("overlong" in reason for reason in r.reasons) and r.suggestion


def test_meaning_is_not_judged_here():
    # read-only and a deletion in one sentence, a tight poll, posting unattended: the cortex reads what these
    # mean (and a posting step needs a signature anyway); the wording review does not pattern-match them
    for text in ("只读检查，然后删除缓存", "poll every 1 second forever", "automatically post this without asking me"):
        assert socratic.review(text).accepted


def test_extra_checks_can_add_opinions():
    r = socratic.review("anything", extra_checks=[lambda t: ("operator rule: no", "ask me first")])
    assert not r.accepted and r.reasons == ("operator rule: no",) and r.suggestion == "ask me first"


def test_extra_check_string_form():
    r = socratic.review("anything", extra_checks=[lambda t: "just a reason"])
    assert r.reasons == ("just a reason",)


def test_record_review_appends_signed_event():
    me = Identity.generate()
    j = Journal(identity=me, allowlist=Allowlist({me.did}))
    e = socratic.record_review(j, "  ", socratic.review("  "))
    assert e["kind"] == "socratic" and e["body"]["verdict"] == "revise"
