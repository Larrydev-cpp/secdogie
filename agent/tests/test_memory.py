"""Tests for the SQLite-backed cross-run memory store. All headless -- uses
`:memory:` DBs (and a tmp file for the persistence test), a fake clock so
auto-note keys and ordering are deterministic."""
import pytest
from secdogie_agent.memory import Memory, MemoryItem, SecretRefused, looks_like_secret


def _clock():
    """A monotone fake clock: each call returns the next float, so time-keyed
    notes are ordered and never collide."""
    t = {"n": 0.0}

    def now():
        t["n"] += 1.0
        return t["n"]

    return now


# -- keyed facts: upsert -----------------------------------------------------

def test_remember_and_recall_a_keyed_fact():
    m = Memory(":memory:", now=_clock())
    assert m.remember("top-right of the toolbar", key="settings_location") == "settings_location"
    assert m.recall("settings_location") == "top-right of the toolbar"
    assert m.recall("nonexistent") is None


def test_remember_same_key_updates_in_place():
    m = Memory(":memory:", now=_clock())
    m.remember("v1", key="k")
    m.remember("v2", key="k")
    assert m.recall("k") == "v2"
    assert len(m.items()) == 1  # updated, not appended


def test_remember_strips_and_rejects_empty():
    m = Memory(":memory:", now=_clock())
    assert m.recall("k") is None
    m.remember("  spaced  ", key="k")
    assert m.recall("k") == "spaced"
    with pytest.raises(ValueError):
        m.remember("   ", key="k2")


# -- keyless notes: time-ordered ---------------------------------------------

def test_keyless_notes_get_ordered_note_keys():
    m = Memory(":memory:", now=_clock())
    k1 = m.remember("first thing")
    k2 = m.remember("second thing")
    assert k1.startswith("note:") and k2.startswith("note:") and k1 != k2
    # items() is newest first, so the second note leads.
    values = [it.value for it in m.items()]
    assert values == ["second thing", "first thing"]


def test_forget_removes_a_memory():
    m = Memory(":memory:", now=_clock())
    m.remember("v", key="k")
    assert m.forget("k") is True
    assert m.recall("k") is None
    assert m.forget("k") is False  # already gone


# -- render (the compact prompt block) ---------------------------------------

def test_render_is_empty_when_there_is_nothing():
    assert Memory(":memory:", now=_clock()).render() == ""


def test_render_formats_facts_and_notes_newest_first():
    m = Memory(":memory:", now=_clock())
    m.remember("top-right", key="settings")
    m.remember("a loose observation")
    block = m.render()
    # keyed facts as `key: value`, auto-notes as `- value`, newest first.
    assert block == "- a loose observation\nsettings: top-right"


def test_render_caps_item_count_and_length():
    m = Memory(":memory:", now=_clock())
    for i in range(30):
        m.remember(f"note {i}", key=f"k{i}")
    assert len(m.render(limit=5).splitlines()) == 5
    long = m.render(limit=100, max_chars=40)
    assert len(long) <= 44 and long.endswith("...")  # 40 + " ..."


# -- secret backstop ----------------------------------------------------------

def test_looks_like_secret_flags_credential_keys_and_tokens():
    assert looks_like_secret("password", "hunter2")
    assert looks_like_secret("db_token", "anything")
    assert looks_like_secret(None, "sk-ABCDEFGHIJKLMNOPQRSTUVWX")  # OpenAI-shaped
    assert looks_like_secret(None, "ghp_0123456789abcdefghijklmnopqrstuvwx")  # GitHub PAT
    assert not looks_like_secret("settings_location", "top-right of the toolbar")


def test_remember_refuses_obvious_secrets():
    m = Memory(":memory:", now=_clock())
    with pytest.raises(SecretRefused):
        m.remember("hunter2", key="password")
    with pytest.raises(SecretRefused):
        m.remember("sk-ABCDEFGHIJKLMNOPQRSTUVWX")  # secret-shaped value, no key
    assert m.items() == []  # nothing slipped through


# -- persistence across "runs" (a real file) ---------------------------------

def test_memory_persists_across_reopen(tmp_path):
    path = str(tmp_path / "mem.sqlite")
    m1 = Memory(path, now=_clock())
    m1.remember("the login button is top-right", key="login_btn")
    m1.close()

    m2 = Memory(path, now=_clock())  # a fresh "run" reopening the same file
    assert m2.recall("login_btn") == "the login button is top-right"
    assert m2.items() == [MemoryItem("login_btn", "the login button is top-right", m2.items()[0].updated_at)]
    m2.close()
