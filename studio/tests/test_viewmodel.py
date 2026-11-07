from secdogie_studio import viewmodel as vm
from secdogie_studio.controller import EXITED, RUNNING, STARTING, STOPPED, Snapshot


def test_every_planned_page_names_its_slice():
    assert vm.NAV[0].key == "node" and not vm.NAV[0].planned
    for item in vm.NAV[1:]:
        assert item.planned and item.planned in vm.planned_text(item)


def test_first_run_problems():
    assert vm.first_run_problems("long enough", "long enough", ("forward",)) == []
    assert len(vm.first_run_problems("short", "other", ())) == 3


def test_buttons_follow_the_node_state():
    base = dict(initialized=True, node_did="n", app_did="a", operator_did="o", roles=("forward",))
    stopped = Snapshot(**base, node_state=STOPPED)
    assert vm.can_start(stopped) and not vm.can_stop(stopped) and not vm.can_check_link(stopped)
    starting = Snapshot(**base, node_state=STARTING, pid=1)
    assert not vm.can_start(starting) and vm.can_stop(starting) and not vm.can_check_link(starting)
    running = Snapshot(**base, node_state=RUNNING, pid=1, listen="127.0.0.1:5")
    assert vm.can_check_link(running) and not vm.can_check_link(Snapshot(**base, node_state=RUNNING, busy="x"))
    exited = Snapshot(**base, node_state=EXITED, returncode=0)
    assert vm.can_start(exited) and vm.node_state_text(exited) == "已退出（代码 0）"
    rows = dict(vm.node_rows(running))
    assert rows["状态"] == "运行中" and rows["进程"] == "1" and rows["角色"] == "forward"


def test_the_link_row_explains_the_expected_refusal():
    assert vm.link_text("refused: no active memory studio-link-").startswith("已连通")
    assert vm.link_text("something else") == "节点回复：something else"
