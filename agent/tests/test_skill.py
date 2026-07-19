"""Skill interpreter tests -- pure control flow, with fake execute/check."""
import pytest
from secdogie_agent.skill import SkillError, run_skill


def _runner():
    """A fake execute() that records the action dicts it's handed."""
    done = []
    return done, (lambda action: done.append(action))


def _lib(**skills):
    return {"skills": skills}


# -- parameters + substitution ------------------------------------------------

def test_params_substitute_into_actions():
    done, execute = _runner()
    lib = _lib(greet={"params": ["name"], "body": [
        {"op": "action", "action": "type", "text": "hello {name}"},
    ]})
    r = run_skill(lib, "greet", {"name": "Ada"}, execute, lambda d: False)
    assert r.outcome == "completed"
    assert done == [{"action": "type", "text": "hello Ada"}]


def test_unbound_variable_is_an_error():
    _, execute = _runner()
    lib = _lib(x={"body": [{"op": "action", "action": "type", "text": "{missing}"}]})
    with pytest.raises(SkillError, match="unbound variable"):
        run_skill(lib, "x", {}, execute, lambda d: False)


# -- sub-flow calls (composition) ---------------------------------------------

def test_call_runs_another_skill_with_args():
    done, execute = _runner()
    lib = _lib(
        main={"body": [
            {"op": "call", "skill": "click_at", "args": {"x": 10, "y": 20}},
            {"op": "call", "skill": "click_at", "args": {"x": 30, "y": 40}},
        ]},
        click_at={"params": ["x", "y"], "body": [
            {"op": "action", "action": "left_click", "x": "{x}", "y": "{y}"},
        ]},
    )
    run_skill(lib, "main", {}, execute, lambda d: False)
    assert done == [
        {"action": "left_click", "x": "10", "y": "20"},
        {"action": "left_click", "x": "30", "y": "40"},
    ]


def test_call_to_unknown_skill_errors():
    _, execute = _runner()
    lib = _lib(main={"body": [{"op": "call", "skill": "nope"}]})
    with pytest.raises(SkillError, match="unknown skill"):
        run_skill(lib, "main", {}, execute, lambda d: False)


def test_runaway_recursion_is_bounded():
    _, execute = _runner()
    lib = _lib(loop={"body": [{"op": "call", "skill": "loop"}]})
    with pytest.raises(SkillError, match="depth exceeded"):
        run_skill(lib, "loop", {}, execute, lambda d: False)


# -- conditionals -------------------------------------------------------------

def test_if_takes_then_branch_when_screen_condition_true():
    done, execute = _runner()
    lib = _lib(x={"body": [
        {"op": "if", "cond": {"kind": "screen", "description": "popup"},
         "then": [{"op": "action", "action": "key", "keys": ["esc"]}],
         "else": [{"op": "action", "action": "type", "text": "no popup"}]},
    ]})
    run_skill(lib, "x", {}, execute, lambda desc: desc == "popup")
    assert done == [{"action": "key", "keys": ["esc"]}]


def test_if_takes_else_branch_when_false():
    done, execute = _runner()
    lib = _lib(x={"body": [
        {"op": "if", "cond": {"kind": "screen", "description": "popup"},
         "then": [{"op": "action", "action": "key", "keys": ["esc"]}],
         "else": [{"op": "action", "action": "type", "text": "no popup"}]},
    ]})
    run_skill(lib, "x", {}, execute, lambda desc: False)
    assert done == [{"action": "type", "text": "no popup"}]


def test_expr_condition_compares_numerically():
    done, execute = _runner()
    lib = _lib(x={"params": ["n"], "body": [
        {"op": "if", "cond": {"kind": "expr", "left": "{n}", "op": ">=", "right": "3"},
         "then": [{"op": "action", "action": "type", "text": "big"}]},
    ]})
    run_skill(lib, "x", {"n": 5}, execute, lambda d: False)
    assert done == [{"action": "type", "text": "big"}]


def test_not_condition_inverts():
    done, execute = _runner()
    lib = _lib(x={"body": [
        {"op": "if", "cond": {"kind": "not", "cond": {"kind": "screen", "description": "ready"}},
         "then": [{"op": "action", "action": "wait", "seconds": 1}]},
    ]})
    run_skill(lib, "x", {}, execute, lambda d: False)  # "ready" is false -> not -> true
    assert done == [{"action": "wait", "seconds": 1}]


# -- loops --------------------------------------------------------------------

def test_repeat_runs_the_body_n_times_with_an_index():
    done, execute = _runner()
    lib = _lib(x={"body": [
        {"op": "repeat", "count": 3, "var": "i", "body": [
            {"op": "action", "action": "type", "text": "row {i}"},
        ]},
    ]})
    run_skill(lib, "x", {}, execute, lambda d: False)
    assert [a["text"] for a in done] == ["row 1", "row 2", "row 3"]


def test_repeat_count_can_be_a_parameter():
    done, execute = _runner()
    lib = _lib(x={"params": ["n"], "body": [
        {"op": "repeat", "count": "{n}", "body": [{"op": "action", "action": "key", "keys": ["down"]}]},
    ]})
    run_skill(lib, "x", {"n": 4}, execute, lambda d: False)
    assert len(done) == 4


def test_while_loops_until_the_condition_flips():
    done, execute = _runner()
    calls = {"n": 0}

    def check(desc):
        calls["n"] += 1
        return calls["n"] <= 3  # true for the first 3 checks, then false

    lib = _lib(x={"body": [
        {"op": "while", "cond": {"kind": "screen", "description": "more rows"}, "body": [
            {"op": "action", "action": "left_click", "x": 1, "y": 1},
        ]},
    ]})
    run_skill(lib, "x", {}, execute, check)
    assert len(done) == 3


def test_while_with_a_counter_via_incr():
    done, execute = _runner()
    lib = _lib(x={"body": [
        {"op": "set", "var": "i", "value": 0},
        {"op": "while", "cond": {"kind": "expr", "left": "{i}", "op": "<", "right": "3"}, "body": [
            {"op": "action", "action": "key", "keys": ["tab"]},
            {"op": "incr", "var": "i"},
        ]},
    ]})
    r = run_skill(lib, "x", {}, execute, lambda d: False)
    assert r.outcome == "completed" and len(done) == 3


# -- guards -------------------------------------------------------------------

def test_infinite_while_is_capped_as_limit():
    done, execute = _runner()
    lib = _lib(x={"body": [
        {"op": "while", "cond": {"kind": "screen", "description": "always"}, "body": [
            {"op": "action", "action": "key", "keys": ["a"]},
        ]},
    ]})
    r = run_skill(lib, "x", {}, execute, lambda d: True, max_loop=50)
    assert r.outcome == "limit" and len(done) == 50


def test_max_statements_stops_a_long_run():
    done, execute = _runner()
    lib = _lib(x={"body": [
        {"op": "repeat", "count": 1000, "body": [{"op": "action", "action": "key", "keys": ["a"]}]},
    ]})
    r = run_skill(lib, "x", {}, execute, lambda d: False, max_statements=10)
    assert r.outcome == "limit"
    assert r.statements <= 11  # stopped near the cap, didn't run all 1000


def test_should_stop_ends_as_stopped():
    done, execute = _runner()
    calls = {"n": 0}

    def should_stop():
        calls["n"] += 1
        return calls["n"] > 2

    lib = _lib(x={"body": [
        {"op": "repeat", "count": 100, "body": [{"op": "action", "action": "key", "keys": ["a"]}]},
    ]})
    r = run_skill(lib, "x", {}, execute, lambda d: False, should_stop=should_stop)
    assert r.outcome == "stopped"


def test_unknown_op_errors():
    _, execute = _runner()
    lib = _lib(x={"body": [{"op": "frobnicate"}]})
    with pytest.raises(SkillError, match="unknown statement op"):
        run_skill(lib, "x", {}, execute, lambda d: False)


def test_result_counts_actions_and_statements():
    done, execute = _runner()
    lib = _lib(x={"body": [
        {"op": "action", "action": "key", "keys": ["a"]},
        {"op": "set", "var": "z", "value": "1"},
        {"op": "action", "action": "key", "keys": ["b"]},
    ]})
    r = run_skill(lib, "x", {}, execute, lambda d: False)
    assert r.actions == 2 and r.statements == 3 and r.outcome == "completed"
