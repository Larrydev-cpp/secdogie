from __future__ import annotations

from secdogie_citadel.goals import GoalTree, build_goal_tree


def _ev(body):
    return {"kind": "goal", "body": body}


def test_add_update_complete_remove():
    tree = build_goal_tree([
        _ev({"op": "add", "id": "g1", "title": "root"}),
        _ev({"op": "add", "id": "g2", "title": "child", "deps": ["g1"]}),
        _ev({"op": "update", "id": "g2", "title": "renamed"}),
        _ev({"op": "complete", "id": "g1"}),
    ])
    assert tree.nodes["g1"].status == "done"
    assert tree.nodes["g2"].title == "renamed"
    assert tree.nodes["g2"].deps == ("g1",)

    removed = build_goal_tree([_ev({"op": "add", "id": "g1"}), _ev({"op": "remove", "id": "g1"})])
    assert "g1" not in removed.nodes


def test_ready_respects_dependencies():
    tree = build_goal_tree([
        _ev({"op": "add", "id": "g1"}),
        _ev({"op": "add", "id": "g2", "deps": ["g1"]}),
    ])
    assert tree.ready() == ["g1"]  # g2 blocked on g1
    tree.apply({"op": "complete", "id": "g1"})
    assert set(tree.ready()) == {"g2"}


def test_topo_order():
    tree = build_goal_tree([
        _ev({"op": "add", "id": "a"}),
        _ev({"op": "add", "id": "b", "deps": ["a"]}),
        _ev({"op": "add", "id": "c", "deps": ["b"]}),
    ])
    assert tree.topo_order() == ["a", "b", "c"]
    assert not tree.has_cycle()


def test_cycle_detected():
    tree = GoalTree()
    tree.apply({"op": "add", "id": "a", "deps": ["b"]})
    tree.apply({"op": "add", "id": "b", "deps": ["a"]})
    assert tree.has_cycle()


def test_malformed_goal_events_are_skipped():
    tree = build_goal_tree([
        _ev({"op": "add"}),                    # no id
        _ev({"op": "add", "id": "g1"}),
        _ev("not a dict"),                     # bad body
        _ev({"op": "update", "id": "ghost"}),  # unknown id
        {"kind": "status", "body": {"op": "add", "id": "x"}},  # not a goal event
    ])
    assert set(tree.nodes) == {"g1"}


def test_missing_dep_blocks_ready():
    tree = build_goal_tree([_ev({"op": "add", "id": "g2", "deps": ["gone"]})])
    assert tree.ready() == []  # dep does not exist -> not ready
    assert tree.topo_order() == ["g2"]  # missing-dep edge ignored for ordering
