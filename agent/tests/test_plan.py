"""Plan progression tests -- pure state, no model or loop involved."""
from secdogie_agent.plan import Plan


def test_current_and_advance_through_completion():
    p = Plan(subtasks=["a", "b", "c"])
    assert p.current == "a" and not p.is_done
    p.complete_current()
    assert p.current == "b" and p.completed == ["a"]
    p.complete_current()
    p.complete_current()
    assert p.is_done and p.current is None
    assert p.completed == ["a", "b", "c"] and p.skipped == []


def test_skip_advances_and_records_separately():
    p = Plan(subtasks=["a", "b"])
    p.skip_current()
    assert p.current == "b" and p.skipped == ["a"] and p.completed == []
    p.complete_current()
    assert p.is_done and p.completed == ["b"] and p.skipped == ["a"]


def test_operations_past_the_end_are_noops():
    p = Plan(subtasks=["only"])
    p.complete_current()
    assert p.is_done
    p.complete_current()  # nothing left -> no crash, no change
    p.skip_current()
    assert p.completed == ["only"] and p.skipped == [] and p.index == 1


def test_progress_note_marks_current_completed_and_skipped():
    p = Plan(subtasks=["open menu", "click save", "type name"])
    p.complete_current()
    p.skip_current()
    note = p.progress_note()
    assert "[x] open menu" in note
    assert "[skipped] click save" in note
    assert "CURRENT sub-task" in note and "type name" in note
    # It must tell the model that `done` means the sub-task, not the whole task.
    assert "done" in note and "sub-task" in note
    assert "(1/3 done)" in note
