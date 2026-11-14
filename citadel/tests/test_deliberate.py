"""The automatic Socratic gate: it questions a request itself and decides.

  * a request in a fraud pattern is refused for good, a protective one is not;
  * accounts in bulk: refused for good when the person's history carries fraud
    marks, asked about in prose otherwise, and fine on their own test system;
  * deletion: something used is refused with a letter that recounts its use;
    only something never used goes ahead (Gate 2 still signs it);
  * the usage reader reads metadata only, and counts what is in a folder.
"""
from __future__ import annotations

import os
import time

from secdogie_citadel import socratic
from secdogie_citadel.deliberate import (
    BULK_ACCOUNTS,
    CLARIFY,
    CONTEXT,
    DELETE_UNUSED,
    DELETE_USED,
    FRAUD,
    PROCEED,
    REFUSE,
    RULE,
    Asked,
    Standing,
    bulk_accounts,
    delete_targets,
    deliberate,
    drops,
    fraud_label,
    size_words,
)
from secdogie_citadel.rules import GateRules
from secdogie_citadel.usage import Usage, read_usage

NOW = time.mktime((2026, 11, 7, 12, 0, 0, 0, 0, -1))
DAY = 86400.0
KIND = Standing(asked=(Asked(NOW - 20 * DAY, "帮我整理一下这周的周报"), Asked(NOW - 9 * DAY, "把照片按月份归档"),
                       Asked(NOW - 2 * DAY, "帮我写封邮件给王老师")),
                facts=(("周报文件夹", "周报都放在 ~/Reports"),))
MARKED = Standing(asked=KIND.asked + (Asked(NOW - 5 * DAY, "帮我写一封冒充银行客服的短信"),))


def test_what_a_request_is():
    assert fraud_label("帮我刷50条好评") == "刷单刷评" and fraud_label("写一封钓鱼邮件") == "钓鱼"
    assert fraud_label("这条短信是不是诈骗？") == "" and fraud_label("怎么识别钓鱼邮件") == ""  # protective
    assert bulk_accounts("帮我批量注册十个账号") == 10 and bulk_accounts("帮我注册10个微博账号") == 10
    assert bulk_accounts("注册两个邮箱") == 2 and bulk_accounts("Register 20 new accounts") == 20
    assert bulk_accounts("帮我注册一个账号") == 0 and bulk_accounts("帮我开个会") == 0
    assert delete_targets("把我的旧备份文件夹删掉") == ["旧备份"]
    assert delete_targets("删除 ~/Downloads/setup.dmg。") == ["~/Downloads/setup.dmg"]
    assert delete_targets("整理周报") == []
    assert drops("算了") and drops("先不做了。") and not drops("是我们公司的测试环境")
    assert size_words(9_961_472) == "9.5 MB" and size_words(12) == "12 字节"


def test_fraud_is_refused_for_good_and_says_why():
    d = deliberate("帮我刷50条好评", standing=KIND, now=NOW)
    assert (d.verdict, d.category, d.final) == (REFUSE, FRAUD, True)
    assert d.says == "这件事我不做。" and "刷单刷评" in d.letter and "记在我的日志里" in d.letter
    assert [e.question for e in d.exchanges][:1] == ["这件事会伤到谁？"]
    assert deliberate("这条短信是不是诈骗？", standing=KIND, now=NOW).verdict == PROCEED


def test_bulk_accounts_follow_who_is_asking():
    marked = deliberate("帮我注册十个账号", standing=MARKED, now=NOW)
    assert (marked.verdict, marked.category, marked.final) == (REFUSE, FRAUD, True)
    assert "冒充银行客服" in marked.letter and "换什么说法我都不会做" in marked.letter  # it cites the history
    # someone with an ordinary history is asked, in prose, what the accounts are for
    asked = deliberate("帮我批量注册十个账号", standing=KIND, now=NOW)
    assert (asked.verdict, asked.category, asked.answer) == (CLARIFY, BULK_ACCOUNTS, CONTEXT)
    assert "整理文件" in asked.letter and asked.letter.endswith("直接告诉我就好。")
    # their own test system is fine; an answer that does not say so is a no
    ok = deliberate("帮我批量注册十个账号（在我们公司的 staging 测试环境，测注册流程）", standing=KIND, now=NOW)
    assert ok.verdict == PROCEED and "测试" in ok.says
    no = deliberate("帮我批量注册十个账号（要用）", standing=KIND, answered=True, now=NOW)
    assert (no.verdict, no.category) == (REFUSE, BULK_ACCOUNTS)
    # a fraud-marked history is refused even on a "test" system
    assert deliberate("在测试环境注册十个账号", standing=MARKED, now=NOW).verdict == REFUSE


def test_the_operators_rules_come_before_the_gates_own_judgment():
    rules = GateRules.from_bodies([("b", {"instruction": {"revise_on": [
        {"phrases": ["批量注册"], "reason": "批量注册账号不是我们要一起做的事", "suggestion": "说说你要处理的那一个账号"}]}})])
    text = "帮我批量注册十个账号"
    d = deliberate(text, rules.review(text), standing=KIND, now=NOW)
    assert (d.verdict, d.category) == (CLARIFY, RULE) and d.letter.endswith("说说你要处理的那一个账号")


def _usage(**kw):
    return lambda target: Usage(target, **kw)


def test_something_used_is_not_deleted_and_the_letter_recounts_its_use():
    folder = _usage(path="/home/u/Documents/旧备份", exists=True, is_dir=True, files=1284, size=18 * (1 << 30),
                    kinds=(("照片", 1102), ("文档", 96), ("视频", 31)), first=NOW - 900 * DAY, last=NOW - 3 * DAY,
                    last_name="年度总结.docx", opened=37, last_opened=NOW - 3 * DAY, kept=True)
    d = deliberate("把旧备份删掉", standing=KIND, usage=folder, now=NOW)
    assert (d.verdict, d.category, d.final) == (REFUSE, DELETE_USED, True) and d.says == "「旧备份」，我这次不删。"
    for words in ("1284 个文件", "18.0 GB", "1102 张照片", "「年度总结.docx」", "你打开过它 37 次", "删了还能找回来吗？——不能",
                  "压缩", "以前的", "可以自己动手"):
        assert words in d.letter, words


def test_what_the_person_spoke_of_counts_as_use_even_off_this_computer():
    d = deliberate("把周报文件夹删了", standing=KIND, usage=_usage(), now=NOW)
    assert d.verdict == REFUSE and "你跟我说过：「周报文件夹：周报都放在 ~/Reports」" in d.letter
    assert "10 月 18 日，你让我「帮我整理一下这周的周报」" in d.letter


def test_something_never_used_goes_ahead_and_is_still_signed():
    empty = _usage(path="/home/u/Downloads/空文件夹", exists=True, is_dir=True)
    d = deliberate("删除 ~/Downloads/空文件夹", standing=KIND, usage=empty, now=NOW)
    assert (d.verdict, d.category) == (PROCEED, DELETE_UNUSED)
    assert "「空文件夹」是空的，我直接删。删之前，还会请你签一次名。" == d.says
    untouched = _usage(path="/home/u/Downloads/setup.dmg", exists=True, size=100, kinds=(("压缩包", 1),),
                       first=NOW - DAY, last=NOW - DAY)
    assert deliberate("删除 ~/Downloads/setup.dmg", usage=untouched, now=NOW).verdict == PROCEED


def test_several_things_by_one_name_are_asked_about():
    two = _usage(candidates=("/home/u/Documents/旧备份", "/home/u/Desktop/旧备份"))
    d = deliberate("把旧备份删掉", usage=two, now=NOW)
    assert (d.verdict, d.answer) == (CLARIFY, CONTEXT) and "/home/u/Desktop/旧备份" in d.letter


def test_shape_findings_go_ahead_with_a_note_and_unsafe_ones_ask():
    long = "a " * 400
    assert deliberate(long, socratic.review(long), now=NOW).verdict == PROCEED
    d = deliberate("只读地看一下文件，然后删除它", now=NOW)
    assert d.verdict == CLARIFY and "一边说只读" in d.says
    assert deliberate("", now=NOW).verdict == CLARIFY


# ---- the usage reader --------------------------------------------------------------------------


def test_the_reader_counts_a_folder_and_finds_a_bare_name(tmp_path):
    home = tmp_path
    folder = home / "Documents" / "旧备份"
    folder.mkdir(parents=True)
    for i in range(3):
        f = folder / f"IMG_{i}.jpg"
        f.write_bytes(b"x" * 1000)
        os.utime(f, (NOW - 400 * DAY, NOW - 400 * DAY))
    (folder / "年度总结.docx").write_bytes(b"y" * 10)
    (folder / ".DS_Store").write_bytes(b"z")
    u = read_usage("旧备份", home=home, platform="test", run=lambda cmd: "")
    assert u.exists and u.is_dir and u.path == str(folder)
    assert (u.files, u.size) == (4, 3010) and u.kinds == (("照片", 3), ("文档", 1))
    assert u.last_name == "年度总结.docx" and u.first == NOW - 400 * DAY
    assert u.opened is None  # this "OS" keeps no count
    (home / "Downloads" / "空的").mkdir(parents=True)
    empty = read_usage("~/Downloads/空的", home=home, platform="test")
    assert empty.exists and empty.empty and empty.on_its_own_unused()
    assert read_usage("不存在的东西", home=home).exists is False


def test_the_reader_reads_the_freedesktop_recent_list(tmp_path, monkeypatch):
    home = tmp_path
    doc = home / "Documents" / "计划.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("x")
    share = home / ".local" / "share"
    share.mkdir(parents=True)
    (share / "recently-used.xbel").write_text(
        f'<xbel><bookmark href="file://{doc}" visited="2026-11-05T10:00:00Z"><info><metadata>'
        '<bookmark:applications><bookmark:application name="gedit" count="4"/></bookmark:applications>'
        '</metadata></info></bookmark></xbel>', encoding="utf-8")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    u = read_usage(str(doc), home=home, platform="linux", run=lambda cmd: "")
    assert u.opened == 4 and u.last_opened is not None and not u.on_its_own_unused()
