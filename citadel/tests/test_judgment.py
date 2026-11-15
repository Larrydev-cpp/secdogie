"""The automatic Socratic gate's judgment: it reads the person and weighs
what is at stake.

- Weighty steps (deleting what is dear or in use, wiping a disk, accounts in
  bulk) are never met with "are you sure?". They are dissuaded, with a
  cooling-off, or they go ahead.
- A question is only ever about *what* is meant, and only when it is worth
  the interruption.
"""
from __future__ import annotations

import time

from secdogie_citadel import judgment
from secdogie_citadel.deliberate import (
    BULK_ACCOUNTS,
    CLARIFY,
    CONSIDERED,
    DELETE_DEAR,
    DELETE_USED,
    DISSUADE,
    DOUBT,
    PROCEED,
    REFUSE,
    WIPE,
    Asked,
    Standing,
    deliberate,
)
from secdogie_citadel.usage import Usage

NOW = time.mktime((2026, 11, 7, 12, 0, 0, 0, 0, -1))
NIGHT = time.mktime((2026, 11, 7, 2, 10, 0, 0, 0, -1))
DAY = 86400.0
KIND = Standing(asked=(Asked(NOW - 20 * DAY, "帮我整理一下这周的周报"), Asked(NOW - 9 * DAY, "把照片按月份归档"),
                       Asked(NOW - 2 * DAY, "帮我写封邮件给王老师")))
BREAKUP = "和他分手了，把我们的照片全删了"


def test_ordinary_requests_go_ahead_without_a_word_more():
    for text in ("帮我整理一下这周的周报", "看看磁盘还剩多少空间", "清理下载文件夹里的安装包"):
        d = deliberate(text, standing=KIND, now=NOW)
        assert (d.verdict, d.says) == (PROCEED, f"好，我开始做「{text}」。"), text


def test_deleting_memories_in_distress_is_dissuaded_never_confirmed():
    d = deliberate(BREAKUP, standing=KIND, now=NIGHT)
    assert (d.verdict, d.category, d.final) == (DISSUADE, DELETE_DEAR, False)
    assert "？" not in d.says and d.says == "「我们的照片」，我想先替你留着。"
    for words in ("分手", "很难受", "这么晚了", "我认识的你", "封存", "「把我们的照片封存起来」", "压缩、加密",
                  "11 月 14 日", "删之前我会请你签名", "不愿意在你难过的时候"):
        assert words in d.letter, words
    assert d.until == NIGHT + 7 * DAY and d.reading["distressed"] and d.reading["dear"] == "照片"


def test_the_cooling_off_is_remembered_then_honoured():
    held = Asked(NIGHT, BREAKUP, DISSUADE, DELETE_DEAR, NIGHT + 7 * DAY)
    standing = Standing(asked=KIND.asked + (held,))
    # inside the week: a gentle reminder of what was agreed
    soon = deliberate("把我们的照片全删了", standing=standing, now=NIGHT + 2 * DAY)
    assert soon.verdict == DISSUADE and soon.says.startswith("我们说好等到 11 月 14 日") and "还有 5 天" in soon.letter
    # after the week, calm and still wanting it: it goes ahead, and Gate 2 still asks for the signature
    later = deliberate("把我们的照片删了", standing=standing, now=NIGHT + 8 * DAY)
    assert (later.verdict, later.category) == (PROCEED, CONSIDERED) and "请你签名" in later.says
    # after the week but still hurting: held back again, more briefly
    again = deliberate("又吵架了，把我们的照片全删了", standing=standing, now=NIGHT + 8 * DAY)
    assert again.verdict == DISSUADE and again.says.startswith("过了这些天")


def test_a_disk_is_not_wiped_before_a_backup_and_never_with_a_yes_no_question():
    d = deliberate("格式化D盘", standing=KIND, now=NOW)
    assert (d.verdict, d.category) == (DISSUADE, WIPE) and "先备份" in d.letter and "？" not in d.says
    ok = deliberate("格式化D盘（已经备份过了）", standing=KIND, answered=True, now=NOW)
    assert (ok.verdict, ok.category) == (PROCEED, WIPE) and "请你签名" in ok.says
    assert deliberate("格式化新买的移动硬盘", standing=KIND, now=NOW).verdict == PROCEED
    assert deliberate("清空回收站", standing=KIND, now=NOW).verdict == PROCEED


def test_accounts_in_bulk_follow_who_is_asking():
    assert deliberate("帮我批量注册十个账号", standing=KIND, now=NOW).verdict == DISSUADE
    assert deliberate("在公司的 staging 测试环境注册十个测试账号", standing=KIND, now=NOW).verdict == PROCEED
    marked = Standing(asked=KIND.asked + (Asked(NOW - DAY, "帮我写一封冒充银行客服的短信"),))
    assert deliberate("帮我批量注册十个账号", standing=marked, now=NOW).verdict == REFUSE


def test_one_question_only_about_what_is_meant():
    which = deliberate("把那个删了", standing=KIND, now=NOW)
    assert (which.verdict, which.category) == (CLARIFY, DOUBT) and "「那个」" in which.says
    assert deliberate("帮我删一下", standing=KIND, now=NOW).says.startswith("你想删的是什么")
    assert deliberate("帮我发个邮件说明天请假", standing=KIND, now=NOW).says.startswith("要发给谁")
    boss = deliberate("给老板发邮件说我明天请假", standing=KIND, now=NOW)
    assert boss.verdict == PROCEED and boss.says.endswith("发出去之前，我会先给你看一眼。")


def test_an_answer_that_names_the_thing_still_meets_what_protects_it():
    folder = lambda target: Usage(target, path="/home/u/旧备份", exists=True, is_dir=True, files=40, size=1 << 30,  # noqa: E731
                                  kinds=(("文档", 40),), first=NOW - 400 * DAY, last=NOW - 5 * DAY, opened=12)
    d = deliberate("把那个删了（旧备份）", standing=KIND, usage=folder, answered=True, now=NOW)
    assert (d.verdict, d.category) == (DISSUADE, DELETE_USED)


def test_asking_is_worth_it_only_when_it_is_worth_more_than_the_interruption():
    ticket = "帮我买一张明天去上海的高铁票"
    assert deliberate(ticket, standing=KIND, now=NOW).says == "这次花多少钱以内？"  # money, first time: worth a question
    assert deliberate(ticket, standing=KIND, now=NOW, attention=judgment.FLOW).verdict == PROCEED  # not while in flow
    before = Standing(asked=KIND.asked + (Asked(NOW - 30 * DAY, "帮我买一张后天去上海的高铁票", PROCEED),))
    again = deliberate(ticket, standing=before, now=NOW)
    assert again.verdict == PROCEED and again.says.endswith("付款之前，会请你签名。")  # done before: no question


def test_the_judgment_is_deterministic_and_journals_what_it_rests_on():
    a = deliberate("帮我发个邮件说明天请假", standing=KIND, now=NOW)
    b = deliberate("帮我发个邮件说明天请假", standing=KIND, now=NOW)
    assert a == b
    body = a.journal_body("帮我发个邮件说明天请假")
    assert body["assessment"]["acts"] == ["send"] and body["assessment"]["asks"] is True
    assert body["assessment"]["doubts"][0][0] == "who"
    held = deliberate(BREAKUP, standing=KIND, now=NIGHT).journal_body(BREAKUP)
    assert held["verdict"] == DISSUADE and held["until"] == NIGHT + 7 * DAY and held["reading"]["late"] is True
    assert BULK_ACCOUNTS  # imported for completeness of the categories
