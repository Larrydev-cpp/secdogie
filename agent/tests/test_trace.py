"""Execution-trace tests: the hash chain must detect any edit, reorder, drop,
or hash mismatch. All pure -- no loop, no model."""
import json

from secdogie_agent import trace


def _counter_clock():
    t = {"n": 0}

    def clock():
        t["n"] += 1
        return float(t["n"])

    return clock


def _sample(path=None):
    tr = trace.ExecutionTrace(path, clock=_counter_clock())
    tr.record(b"frameA", {"kind": "left_click", "x": 1, "y": 2}, "click the button", "clicked")
    tr.record(b"frameB", {"kind": "type", "text": "hi"}, "type the name", "typed 2 chars")
    tr.record(b"frameC", {"kind": "done"}, "", "done")
    return tr


def test_chain_links_each_entry_to_the_previous():
    tr = _sample()
    assert tr.entries[0].prev_hash == trace.GENESIS
    assert tr.entries[1].prev_hash == tr.entries[0].entry_hash
    assert tr.entries[2].prev_hash == tr.entries[1].entry_hash
    assert tr.head == tr.entries[-1].entry_hash  # head commits to the whole history


def test_frame_hash_is_sha256_of_the_screenshot():
    import hashlib

    tr = _sample()
    assert tr.entries[0].frame_sha256 == hashlib.sha256(b"frameA").hexdigest()


def test_a_clean_trace_verifies():
    ok, reason = trace.verify_entries([e.to_dict() for e in _sample().entries])
    assert ok and reason is None


def test_editing_a_field_breaks_verification():
    entries = [e.to_dict() for e in _sample().entries]
    entries[1]["result"] = "typed 99 chars"  # tamper with what happened
    ok, reason = trace.verify_entries(entries)
    assert not ok and "entry 1" in reason and "hash" in reason


def test_editing_the_screenshot_hash_breaks_verification():
    entries = [e.to_dict() for e in _sample().entries]
    entries[0]["frame_sha256"] = "0" * 64  # pretend a different screen was seen
    ok, reason = trace.verify_entries(entries)
    assert not ok and "entry 0" in reason


def test_reordering_entries_breaks_the_chain():
    entries = [e.to_dict() for e in _sample().entries]
    entries[0], entries[1] = entries[1], entries[0]
    ok, reason = trace.verify_entries(entries)
    assert not ok  # seq + prev_hash both no longer line up


def test_dropping_the_first_entry_breaks_the_chain():
    entries = [e.to_dict() for e in _sample().entries][1:]
    ok, reason = trace.verify_entries(entries)
    assert not ok  # the new first entry's prev_hash isn't GENESIS


def test_save_and_reload_roundtrips_and_verifies(tmp_path):
    path = tmp_path / "trace.jsonl"
    _sample(str(path))
    reloaded = trace.load(str(path))
    assert len(reloaded) == 3
    ok, _ = trace.verify_entries(reloaded)
    assert ok


def test_recording_truncates_a_prior_file(tmp_path):
    path = tmp_path / "trace.jsonl"
    path.write_text("stale line\n")
    _sample(str(path))  # a fresh run must not append to an old trace
    lines = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    assert len(lines) == 3 and lines[0]["seq"] == 1


def test_cli_main_reports_ok_and_tampered(tmp_path, capsys):
    path = tmp_path / "trace.jsonl"
    _sample(str(path))
    assert trace.main([str(path)]) == 0
    assert "chain intact" in capsys.readouterr().out

    entries = trace.load(str(path))
    entries[2]["reasoning"] = "sneakily changed"
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    assert trace.main([str(path)]) == 1
    assert "TAMPERED" in capsys.readouterr().err
