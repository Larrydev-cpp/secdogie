"""One headless run that chains all four pillars with real components.

This is the vertical slice: it proves the pieces fit into a single flow, not that
each half passes its own tests. Everything is real -- DID identity, signed
capability grants, the encrypted node mesh, journal replication, content-addressed
evidence, the Socratic step, the structured (screenshot-free) agent loop, and
signed run records -- except the two things a headless box genuinely lacks, which
are faked exactly as fleet's end-to-end test fakes them: the vision model (a
scripted provider) and the browser (a canned page).

The flow, all on 127.0.0.1:

  1. an operator and three nodes (A, B, C, encrypted) come up on the allowlist;
  2. the operator signs a minimal capability grant to node A;
  3. node A learns a page -> content-addressed evidence + a signed knowledge entry;
  4. the mesh converges: the grant, the knowledge and the evidence bytes reach C,
     byte-for-byte;
  5. a goal on A runs through the Supervisor -- Socratic review/revise, capability
     enforcement (granted click runs, ungranted typing is refused), the real
     structural loop (no screenshot), each step a signed run record;
  6. the run record converges to C, where its hash chain verifies;
  7. every stage is asserted, headless, with no credentials and no third party.

`run_slice()` returns a `SliceResult` for tests; `narrate=True` prints the story.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from secdogie_citadel.evidence import knowledge_entries, record_knowledge
from secdogie_citadel.run import verify_run
from secdogie_citadel.state import StateStore
from secdogie_citadel.supervisor import Supervisor
from secdogie_identity import Allowlist, Identity
from secdogie_identity.capability import create_capability
from secdogie_node import Node, NodeConfig

try:  # the real browser reader (optional; a canned page is used without it)
    from secdogie_desktop.websession import PageObservation, WebAxNode
    _HAVE_WEBSESSION = True
except ImportError:
    _HAVE_WEBSESSION = False


@dataclass
class SliceResult:
    steps: list[tuple[str, bool, str]] = field(default_factory=list)
    used_real_loop: bool = False

    def record(self, name: str, ok: bool, detail: str = "") -> None:
        self.steps.append((name, bool(ok), detail))

    @property
    def ok(self) -> bool:
        return all(ok for _n, ok, _d in self.steps)


# --- the two headless fakes: a canned page, and a scripted vision model -------

_PAGE = {
    "url": "https://example.com/guide",
    "title": "Save Guide",
    "text": "To save, press the Save button in the toolbar.",
    "ax": [{"role": "document", "name": "Save Guide", "value": "",
            "children": [{"role": "button", "name": "Save", "value": "", "children": []}]}],
}


def _canned_page() -> dict:
    """The page the operator's authorized session would have returned. Built
    through the real websession types when they're importable, so the shape is the
    genuine one, not a demo-only invention."""
    if not _HAVE_WEBSESSION:
        return dict(_PAGE)

    def to_node(n) -> WebAxNode:
        return WebAxNode(role=n["role"], name=n["name"], value=n.get("value", ""),
                         children=tuple(to_node(c) for c in n.get("children", [])))

    obs = PageObservation(url=_PAGE["url"], title=_PAGE["title"], text=_PAGE["text"],
                          ax_nodes=tuple(to_node(n) for n in _PAGE["ax"]))

    def to_dict(n) -> dict:
        return {"role": n.role, "name": n.name, "value": n.value,
                "children": [to_dict(c) for c in n.children]}
    return {"url": obs.url, "title": obs.title, "text": obs.text,
            "ax": [to_dict(n) for n in obs.ax_nodes]}


# --- node mesh helpers -------------------------------------------------------

def _drive(nodes, predicate, *, rounds=200) -> bool:
    for _ in range(rounds):
        for n in nodes:
            n.tick()
        time.sleep(0.02)
        try:
            if predicate():
                return True
        except Exception:  # noqa: BLE001 -- a read racing the recv thread; retry
            pass
    return False


def run_slice(narrate: bool = False) -> SliceResult:  # noqa: C901 -- a linear script, read top to bottom
    import base64
    import json
    import tempfile

    from nacl.public import PrivateKey
    from secdogie_transport.sealed import public_key_b64

    result = SliceResult()
    tmp = tempfile.mkdtemp(prefix="secdogie-slice-")

    def say(msg: str) -> None:
        if narrate:
            print(msg)

    # 1) operator + three encrypted nodes on the allowlist ---------------------
    operator = Identity.generate()
    names = ["a", "b", "c"]
    ids = {n: Identity.generate() for n in names}
    tkeys = {n: PrivateKey.generate() for n in names}
    allow = Allowlist({operator.did, *(ids[n].did for n in names)})
    allow_path = f"{tmp}/authorized.conf"
    with open(allow_path, "w", encoding="utf-8") as f:
        f.write("".join(f"authorized_did = {d}\n" for d in sorted(allow.dids())))

    nodes: dict[str, Node] = {}
    for n in names:
        ids[n].save(f"{tmp}/{n}.key")
        with open(f"{tmp}/{n}.tkey", "w", encoding="utf-8") as f:
            f.write(f"private_key = {base64.b64encode(bytes(tkeys[n])).decode()}\n")
        # every node is handed the others' bindings so encryption is ready
        for m in names:
            from secdogie_identity.binding import create_binding
            b = create_binding(ids[m], public_key_b64(tkeys[m]), key_version=1)
            with open(f"{tmp}/{m}.binding.json", "w", encoding="utf-8") as bf:
                json.dump(b, bf)
        cfg = NodeConfig(
            identity=f"{tmp}/{n}.key", journal=f"{tmp}/{n}.db",
            listen_host="127.0.0.1", listen_port=0, authorized=allow_path,
            transport_key=f"{tmp}/{n}.tkey", evidence=f"{tmp}/{n}-evidence",
            bindings=[f"{tmp}/{m}.binding.json" for m in names if m != n],
            sync_interval=0.02,
        )
        nodes[n] = Node(cfg)
    a, b, c = nodes["a"], nodes["b"], nodes["c"]
    a.add_peer(b.did, *b.address)
    b.add_peer(c.did, *c.address)  # a - b - c
    c.add_peer(b.did, *b.address)
    result.record("1. operator + 3 encrypted nodes up", a.encrypted and b.encrypted and c.encrypted,
                  f"A={a.did[:16]} B={b.did[:16]} C={c.did[:16]}")
    say(f"1. 三个加密节点起来了 (A/B/C),操作员 {operator.did[:16]}")

    # supervisor over node A's journal, enforcing the operator's grants
    sup = Supervisor(a.journal, run_task=_make_run_task(result), issuers=Allowlist({operator.did}))

    # 2) operator signs a minimal grant -> A's journal ------------------------
    grant = create_capability(operator, a.did, ["physical.click", "observe.read"], ttl=3600)
    sup.add_grant(grant)
    scopes = sorted(sup.node_scopes())
    result.record("2. operator grant reaches A", scopes == ["observe.read", "physical.click"],
                  f"scopes={scopes}")
    say(f"2. 操作员签发能力授权 → A 的日志;A 现在持有 scope: {scopes}")

    # 3) A learns a page -> content-addressed evidence + knowledge -------------
    page = _canned_page()
    root = a.evidence.put_json(page)
    size = len(json.dumps(page, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    record_knowledge(a.journal, root=root, url=page["url"], title=page["title"],
                     size=size, preview=page["text"])
    result.record("3. A learns a page (evidence + knowledge)", a.evidence.has_all(root),
                  f"root={root[:16]} size={size}")
    say(f"3. A 学习了《{page['title']}》→ 证据根 {root[:16]} ({size}B) + knowledge 入日志")

    # 4) the mesh converges: grant + knowledge + evidence reach C -------------
    def converged_to_c() -> bool:
        ks = {k.root for k in knowledge_entries(c.journal)}
        grants = [e for e in c.journal.events() if e.get("kind") == "capability_grant"]
        return root in ks and bool(grants) and c.evidence.has_all(root)

    conv = _drive([a, b, c], converged_to_c)
    same_bytes = conv and c.evidence.read_bytes(root) == a.evidence.read_bytes(root)
    result.record("4. grant + knowledge + evidence converge to C (byte-identical)", same_bytes,
                  "C has the same evidence bytes as A")
    say("4. 授权、knowledge、证据本体都收敛到 C,且证据逐字节一致")

    # 5) a goal runs on A: Socratic -> capability -> structural loop -> record --
    a.journal.append("goal", {"op": "add", "id": "g1",
                              "title": "submit the report automatically without asking", "deps": []})
    code, summary = sup.run_goal("g1")
    blocked = result_meta.get("blocked", [])
    executed = result_meta.get("executed", [])
    # Socratic must have rewritten the unattended instruction; the loop must have
    # run the granted click and refused the ungranted type.
    reviews = [e["body"] for e in a.journal.events() if e.get("kind") == "socratic"]
    revised = any(r["verdict"] == "revise" for r in reviews)
    step5_ok = (code == 0 and revised and "left_click" in executed and "type" not in executed
                and any("physical.type" in x for x in blocked))
    result.record("5. goal runs: Socratic revise + capability gate + structural loop", step5_ok,
                  f"code={code} executed={executed} blocked={blocked} revised={revised} "
                  f"real_loop={result.used_real_loop}")
    say(f"5. 目标执行: 苏格拉底改写={revised}, 执行={executed}, 被能力闸拦={blocked}, "
        f"真实回路={result.used_real_loop}")

    # 6) the run record converges to C and its hash chain verifies ------------
    def run_on_c():
        store = StateStore()
        store.merge_events(c.journal.events())
        runs = store.entities("run")
        return store, runs

    def run_reached_c() -> bool:
        _store, runs = run_on_c()
        return any(r.get("goal_id") == "g1" and r.get("steps", 0) >= 1 for r in runs.values())

    reached = _drive([a, b, c], run_reached_c)
    verified = False
    if reached:
        store, runs = run_on_c()
        run_id = next(rid for rid, r in runs.items() if r.get("goal_id") == "g1")
        verified, reason = verify_run(run_id, store)
        result.record("6. run record converges to C and verifies", verified,
                      f"run={run_id[:16]} verify={verified} {reason or ''}")
        say(f"6. run 记录收敛到 C,链校验 {'通过' if verified else '失败: '+str(reason)}")
    else:
        result.record("6. run record converges to C and verifies", False, "run did not reach C")

    # 7) everything above held ------------------------------------------------
    result.record("7. whole chain headless, no credentials, no third party", result.ok,
                  "each stage asserted above")
    say(f"7. 整条链 {'全部通过' if result.ok else '有失败'} —— 全 headless、无凭据、无第三方")

    for n in nodes.values():
        n.channel.close()          # stop inbound first, across the whole mesh...
    time.sleep(0.1)                # ...let any in-flight recv callback finish...
    for n in nodes.values():
        n.close()                  # ...then close journals (idempotent channel close)
    return result


# The structured task runner: the real agent loop when the agent is installed,
# else a faithful stand-in that still exercises the capability gate + run record.
result_meta: dict = {}


def _make_run_task(result: SliceResult):
    def run_task(task, *, should_stop, on_status, confirm, record_step=None, plan_gate=None, **_kw):
        result_meta.clear()
        result_meta["executed"] = []
        result_meta["blocked"] = []
        try:
            return _real_loop(task, record_step, plan_gate, result)
        except ImportError:
            return _fake_loop(task, record_step, plan_gate, result)
    return run_task


def _script():
    # the model's plan: click the granted target, then try ungranted typing
    return [
        {"action": "click_element", "element": "e1"},
        {"action": "type", "text": "secret"},
        {"action": "done", "text": "saved"},
    ]


def _real_loop(task, record_step, plan_gate, result: SliceResult):
    from secdogie_agent import actions, elements, screen  # noqa: F401
    from secdogie_agent.axtree import AxElement
    from secdogie_agent.loop import AgentConfig, run
    from secdogie_agent.providers.base import Action, VisionProvider

    result.used_real_loop = True
    tree = [AxElement(role="Button", name="Save", automation_id="save", bounds=(10, 10, 90, 40))]

    class FakeAx:
        def snapshot(self):
            return list(tree)

    class AxBackend:
        ax_provider = FakeAx()

        def setup(self, logger):
            pass

        def capture(self, region=None):
            raise AssertionError("structural mode must not capture the screen")

        def execute(self, action):
            result_meta["executed"].append(action.kind)
            return "ok"

        def element_targets(self):
            return elements.interactable_targets(tree)

        def invoke_element(self, el):
            result_meta["executed"].append("left_click")  # click_element invokes as a click
            return f"invoked {el.name}"

    class Scripted(VisionProvider):
        def __init__(self):
            self.script = _script()

        def next_action(self, task, screenshot_png, screen_size, history):
            return Action.from_dict(self.script.pop(0))

    def gate(view, recent):
        allowed, note = plan_gate(view, recent) if plan_gate else (True, "")
        if not allowed:
            result_meta["blocked"].append(f"{view.get('kind')}: {note}")
        return allowed, note

    cfg = AgentConfig(task=task, auto=True, max_steps=8, backend=AxBackend(), structural=True,
                      action_pause=0, plan_gate=gate,
                      trace_on_entry=(lambda e: record_step(observation=e.frame_sha256,
                                                            action=e.action, result=e.result))
                      if record_step else None)
    code = run(Scripted(), cfg)
    return code, "done" if code == 0 else f"exit {code}"


def _fake_loop(task, record_step, plan_gate, result: SliceResult):
    """Agent package absent: still drive the capability gate + run record so the
    slice runs, and say so honestly (used_real_loop stays False)."""
    from secdogie_citadel.loop_gate import make_plan_gate  # noqa: F401
    seq = 1
    for view in ({"kind": "left_click", "element": "e1", "x": 50, "y": 25, "text": "", "keys": [],
                  "path": "", "high_risk": False},
                 {"kind": "type", "text": "secret", "element": None, "x": None, "y": None,
                  "keys": [], "path": "", "high_risk": False}):
        allowed, note = plan_gate(view, []) if plan_gate else (True, "")
        if not allowed:
            result_meta["blocked"].append(f"{view['kind']}: {note}")
            if record_step:
                record_step(observation={"seq": seq}, action=view,
                            result=f"refused by plan gate: {note}", state="executing")
        else:
            result_meta["executed"].append(view["kind"])
            if record_step:
                record_step(observation={"seq": seq}, action=view, result="ok", state="executing")
        seq += 1
    return 0, "done"


__all__ = ["SliceResult", "run_slice"]
