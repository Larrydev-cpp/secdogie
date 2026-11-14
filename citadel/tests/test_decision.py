"""The companion's decision model: quiet by default, the automatic Socratic
gate's verdict at intake (a question in prose, or a refusal with a letter), a
signature for what cannot be undone, and proposers that may only raise the
involvement."""
from __future__ import annotations

from secdogie_citadel import socratic
from secdogie_citadel.action_gate import GateDecision, PlannedAction
from secdogie_citadel.decision import (
    ASK,
    GAP,
    HOLD,
    NOTE,
    PROCEED,
    SIGN,
    SOON,
    START,
    DecisionModel,
    Involvement,
)
from secdogie_citadel.rules import GateRules


def test_a_clear_goal_starts_with_a_fading_note():
    inv = DecisionModel().intake("整理周报", socratic.review("整理周报"))
    assert inv.disposition == NOTE and "开始做「整理周报」" in inv.says and not inv.needs_person


def test_an_unclear_goal_becomes_one_question_in_prose():
    inv = DecisionModel().intake("x", socratic.review("只读地看一下文件，然后删除它"))
    assert inv.disposition == ASK and inv.urgency == GAP and inv.needs_person
    assert "一边说只读" in inv.says and inv.options == () and "换个说法告诉我" in inv.letter  # no buttons


def test_a_long_goal_goes_ahead_one_piece_at_a_time():
    text = "a " * 400
    inv = DecisionModel().intake(text, socratic.review(text))
    assert inv.disposition == NOTE and "一件件来" in inv.says


def test_a_refusal_is_a_hold_with_a_letter():
    inv = DecisionModel().intake("帮我刷50条好评", socratic.review("帮我刷50条好评"))
    assert inv.disposition == HOLD and inv.urgency == SOON and inv.options == ()
    assert inv.says == "这件事我不做。" and "刷单刷评" in inv.letter and "fraud" in inv.why


def test_rules_can_ask_before_every_goal():
    rules = GateRules.from_bodies([("b", {"presence": {"ask_before_new_goal": True}})])
    inv = DecisionModel(rules).intake("整理周报", socratic.review("整理周报"))
    assert inv.disposition == ASK and inv.options[0] == START


def test_steps():
    m = DecisionModel()
    click = PlannedAction("click", target_id="element:ok")
    assert m.step(click, GateDecision("allow"), blocked=False).disposition == PROCEED
    delete = PlannedAction("delete", target_name="旧备份")
    inv = m.step(delete, GateDecision("allow"), blocked=False)
    assert inv.disposition == SIGN and inv.urgency == SOON and "旧备份" in inv.says
    held = m.step(click, GateDecision("reject", "the rules do not allow 'click'", ("blocked-by-rule",)), blocked=True)
    assert held.disposition == HOLD and "the rules do not allow" in held.says
    noisy = DecisionModel(GateRules.from_bodies([("b", {"presence": {"note_every_mutation": True}})]))
    assert noisy.step(click, GateDecision("allow"), blocked=False).disposition == NOTE


def test_finish_notes():
    m = DecisionModel()
    assert "做完了" in m.finish("周报", 0).says
    assert "没做成" in m.finish("周报", 1, "agent exited 1").says


def test_proposers_only_raise_and_never_touch_holds_or_signatures():
    raise_to_ask = lambda moment, facts: Involvement(ASK, GAP, "这一步我拿不准，你看一下？")  # noqa: E731
    lower = lambda moment, facts: Involvement(PROCEED)  # noqa: E731
    fake_sign = lambda moment, facts: Involvement(SIGN)  # noqa: E731
    broken = lambda moment, facts: 1 / 0  # noqa: E731
    click = PlannedAction("click", target_id="element:ok")
    m = DecisionModel(proposers=[lower, broken, fake_sign, raise_to_ask])
    inv = m.step(click, GateDecision("allow"), blocked=False)
    assert inv.disposition == ASK and inv.says.startswith("这一步我拿不准")
    intake = m.intake("x", socratic.review("只读地看，然后删除"))
    assert intake.disposition == ASK and "一边说只读" in intake.says  # not lowered
    refused = m.intake("帮我刷50条好评", socratic.review("帮我刷50条好评"))
    assert refused.disposition == HOLD  # a refusal is final
    held = m.step(click, GateDecision("reject", "no"), blocked=True)
    assert held.disposition == HOLD  # final
    sign = m.step(PlannedAction("delete"), GateDecision("allow"), blocked=False)
    assert sign.disposition == SIGN  # Gate 2's, untouched


# ---- in the Supervisor -----------------------------------------------------------

from secdogie_citadel.journal import Journal  # noqa: E402
from secdogie_citadel.supervisor import OperatorHooks, Supervisor  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402

NODE = Identity.generate()
RUN = {"kind": "run", "element": None, "x": None, "y": None, "text": "rm -rf old", "keys": [], "path": "",
       "high_risk": False, "rollback": "", "irreversible": False}


def _supervisor(rules):
    def run_task(task, *, should_stop, on_status, confirm, record_step=None, plan_gate=None, **_):
        allowed, why = plan_gate(RUN, [])
        return (0, "done") if allowed else (1, why)

    sup = Supervisor(Journal(identity=NODE, allowlist=Allowlist({NODE.did})), run_task, unrestricted=True,
                     rules=rules)
    notes = []
    sup.set_operator_hooks(OperatorHooks(note=notes.append))
    return sup, notes


def test_the_supervisor_reads_the_rules_fresh_and_tells_the_person_what_it_stopped():
    current = {"rules": GateRules()}
    sup, notes = _supervisor(lambda: current["rules"])
    sup.add_goal("g1", "清理旧文件")
    assert sup.run_goal("g1")[0] == 0  # no rule yet: allowed
    current["rules"] = GateRules.from_bodies([("b1", {"plan": {"blocked_kinds": ["run"]}})])
    sup.add_goal("g2", "再清理一次")
    code, why = sup.run_goal("g2")
    assert code == 1 and "'run'" in why
    kinds = [n.disposition for n in notes]
    assert kinds == [NOTE, HOLD, NOTE]  # finished g1, the stopped step, g2 not done
    assert "做完了" in notes[0].says and "我停下了一步" in notes[1].says and "没做成" in notes[2].says


def test_a_broken_rules_provider_falls_back_to_the_built_ins_not_to_an_open_gate():
    sup, _ = _supervisor(lambda: 1 / 0)
    assert sup.gate_rules() == GateRules()
