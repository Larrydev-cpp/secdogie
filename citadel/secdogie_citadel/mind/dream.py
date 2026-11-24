"""Tri-Network Creative Cognition Engine (``mind/dream.py``): Hippocampus + DMN + SN + ECN.

Implements human-inspired constructive episodic creativity without any LLM calls, pretrained
weights, or regular expressions (Beaty et al., *Network Neuroscience of Creative Cognition*,
PMC6428436; Constructive Episodic Simulation):

1. **Hippocampal Deconstruction (``deconstruct_anchor`` / ``deconstruct_hippocampus``)**:
   Decomposes concrete past experiences (``ConcreteAnchor`` + 128-byte self-decoding Episode-VSA
   coordinates) into interchangeable symbolic causal bricks ``(S0, A, O, ΔS+, ΔS-)``.

2. **Default Mode Network — Divergent Recombination (DMN)**:
   - *VSA Analogical Transfer (``analogical_transfer``)*: unbinds the target object role from a
     128-byte episode coordinate via bipolar XNOR + ``CleanupCodebook``, substitutes a new domain
     target, and re-binds the structural action topology onto the novel object.
   - *Compositional Replay (``compositional_replay``)*: chains bricks across distinct past episodes
     whenever ``ΔS+`` of step ``k`` satisfies preconditions ``S0`` of step ``k+1``, synthesizing
     multi-step action chains the companion has never executed before.

3. **Salience Network — Spark Selection (SN, ``score_salience``)**:
   Evaluates every candidate spark using ``Familiarity`` (Sememe-VSA) for genuine novelty
   (filtering out stale routines) multiplied by terminal-state goal alignment (``novelty × goal_fit``),
   keeping only the Top-K sparks.

4. **Executive Control Network — Reality & Safety Convergence (ECN, ``verify_spark``)**:
   Runs every Top-K spark through:
   - ``WorldTrajectory`` multi-step causal closure (kills compositional / semantic-chaining attacks),
   - ``action_gate.gate`` 14-check enforcement (kills destructive chains, out-of-capability actions,
     unattended external posting, excessive cost, or missing intent/rollback contracts),
   - ``Cortex`` + ``mind.decide`` 14-question Socratic safety check (when mounted).
   Only sparks surviving all reality and safety checks become ``MacroSkill`` proposals, which
   require explicit sandbox/human confirmation before ``promote_macro_skill`` writes them back
   into episodic memory.
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..cortex.curriculum import Lesson
from ..cortex.episode_vsa import CleanupCodebook, bind_episode, decode_episode
from ..cortex.familiar import Familiarity
from ..cortex.hippocampus import ConcreteAnchor, Hippocampus
from ..cortex.sememe_vsa import encode_packed, hamming_similarity_bytes
from .decide import decide
from .plan import DISSUADE, PROCEED, REFUSE
from .trajectory import WorldTrajectory, infer_effects

if TYPE_CHECKING:
    from ..action_gate import GateContext, PlannedAction

DEFAULT_MIN_NOVELTY = 0.08
DEFAULT_TOP_K = 5
DEFAULT_MAX_CHAIN = 3

_FAILED_OUTCOMES = frozenset({"failed", "rejected", "refused", "aborted", "error", "blocked"})
_READ_ACTIONS = frozenset({"read", "observe", "scan", "inspect", "list", "search", "index", "check"})


def _norm_atom(atom: str) -> str:
    return atom.strip().lower().replace(" ", "_")


def _norm_atoms(atoms: Iterable[str]) -> frozenset[str]:
    return frozenset(_norm_atom(a) for a in atoms if a and a.strip())


def _brick_hash(*parts: str) -> str:
    h = hashlib.blake2b("|".join(parts).encode("utf-8"), digest_size=8, person=b"sd-drm-1").hexdigest()
    return h


def _rewrite_atoms(atoms: frozenset[str], old_tag: str, new_tag: str) -> frozenset[str]:
    """Transfer object-scoped symbolic state atoms cleanly from source domain tag to target domain tag."""
    if not old_tag or not new_tag or old_tag == new_tag:
        return atoms
    old_n = _norm_atom(old_tag)
    new_n = _norm_atom(new_tag)
    out: set[str] = set()
    for a in atoms:
        if old_n and old_n in a:
            out.add(a.replace(old_n, new_n))
        else:
            out.add(a)
    return frozenset(out)


@dataclass(frozen=True)
class TargetDomain:
    """A novel domain object to which past action topologies can be transferred via VSA analogy."""

    entity: str
    object_class: str
    label: str = ""
    goal_atoms: frozenset[str] = frozenset()

    @property
    def display(self) -> str:
        return self.label.strip() or self.entity.strip() or self.object_class.strip()


@dataclass(frozen=True)
class CausalBrick:
    """One deconstructed causal building block ``(S0, A, O, ΔS+, ΔS-)`` extracted from episodic memory."""

    brick_id: str
    action: str
    object_class: str
    entity: str
    tool: str = ""
    param: str = ""
    preconditions: frozenset[str] = frozenset()
    delta_add: frozenset[str] = frozenset()
    delta_remove: frozenset[str] = frozenset()
    description: str = ""
    vsa_coord: bytes = b""
    source_lesson_idx: int = -1
    high_risk: bool = False
    irreversible: bool = False
    rollback: str = ""
    estimated_cost: float = 1.0

    def apply(self, state: frozenset[str]) -> frozenset[str]:
        """Forward-simulate one causal transition: ``(S - ΔS-) ∪ ΔS+``."""
        return (state - self.delta_remove) | self.delta_add

    def to_planned_action(self, *, purpose: str = "", step_idx: int = 0) -> PlannedAction:
        """Convert this brick into a concrete ``PlannedAction`` for ECN ``action_gate`` verification."""
        from ..action_gate import IntentContract, PlannedAction

        kind = _action_kind_for_gate(self.action, self.tool)
        is_read = kind in _READ_ACTIONS
        exp_obs = "" if is_read else f"state reflects {', '.join(sorted(self.delta_add)) or self.action}"
        rb = self.rollback or ("" if self.irreversible else f"restore {self.entity or self.object_class} from snapshot")
        return PlannedAction(
            kind=kind,
            target_id=f"dream:{self.object_class or 'obj'}:{self.entity or step_idx}",
            target_role=self.object_class or "resource",
            target_name=self.entity or self.object_class,
            text=self.description or f"{self.action} {self.entity}".strip(),
            high_risk=self.high_risk,
            expected_observation=exp_obs,
            estimated_cost=self.estimated_cost,
            intent=IntentContract(
                purpose=purpose or self.description or self.action,
                rollback=rb,
                irreversible=self.irreversible,
            ),
        )


def _action_kind_for_gate(action: str, tool: str) -> str:
    """Map brick action/tool primitives onto ``action_gate`` kinds without regex."""
    a = action.strip().lower()
    t = tool.strip().lower()
    if a in ("delete", "erase", "wipe", "format", "uninstall", "drop", "overwrite", "run_elevated"):
        return a
    if a in ("post", "publish", "submit", "send", "reply", "comment", "tweet", "dm", "upload", "exfiltrate"):
        return "send" if a in ("upload", "exfiltrate") else a
    if a in _READ_ACTIONS:
        return "read"
    if "elevated" in t or "sudo" in t:
        return "run_elevated"
    if "shell" in t or "exec" in t or a in ("run", "execute"):
        return "run"
    if a in ("open", "launch"):
        return "open"
    return "click"


@dataclass(frozen=True)
class DreamSpark:
    """A novel candidate idea synthesized by DMN recombination (analogical transfer or compositional replay)."""

    spark_id: str
    mode: str  # "analogical_transfer" | "compositional_replay"
    steps: tuple[CausalBrick, ...]
    initial_state: frozenset[str]
    terminal_state: frozenset[str]
    description: str
    vsa_coord: bytes
    source_bricks: tuple[str, ...] = ()
    transfer_from: str = ""
    transfer_to: str = ""
    novelty: float = 0.0
    goal_fit: float = 0.0
    salience: float = 0.0


@dataclass(frozen=True)
class ECNVerdict:
    """Executive Control Network reality + safety verification audit for one spark."""

    accepted: bool
    trajectory_verdict: str  # proceed | dissuade | refuse
    trajectory_chains: tuple[str, ...] = ()
    gate_verdicts: tuple[str, ...] = ()
    gate_findings: tuple[str, ...] = ()
    cortex_verdict: str = ""
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class MacroSkill:
    """A reality-verified creative workflow ready for sandbox/human confirmation and memory writeback."""

    skill_id: str
    name: str
    mode: str
    steps: tuple[CausalBrick, ...]
    preconditions: frozenset[str]
    postconditions: frozenset[str]
    description: str
    vsa_coord: bytes
    novelty: float
    goal_fit: float
    salience: float
    ecn: ECNVerdict
    requires_confirmation: bool = True

    def to_concrete_anchor(self, *, timestamp_ms: int = 0, journal_ref: str = "") -> ConcreteAnchor:
        """Package this verified macro-skill into a lossless ``ConcreteAnchor`` for episodic writeback."""
        paths = tuple(dict.fromkeys(s.entity for s in self.steps if s.entity))
        tools = ",".join(dict.fromkeys(s.tool for s in self.steps if s.tool)) or "macro_skill"
        facts = [
            ("action", "->".join(s.action for s in self.steps)),
            ("object_class", self.steps[-1].object_class if self.steps else "macro"),
            ("outcome", "verified_macro_skill"),
            ("dream_mode", self.mode),
        ]
        for a in sorted(self.preconditions):
            facts.append(("pre", a))
        for a in sorted(self.postconditions):
            facts.append(("add", a))
        return ConcreteAnchor(
            verbatim=self.description,
            tool=tools,
            entities=tuple(dict.fromkeys(s.object_class for s in self.steps if s.object_class)),
            paths=paths,
            facts=tuple(facts),
            timestamp_ms=timestamp_ms,
            journal_ref=journal_ref,
            vsa_coord=self.vsa_coord,
        )


@dataclass(frozen=True)
class DreamReport:
    """Full audit trace of one tri-network creativity cycle (Hippocampus -> DMN -> SN -> ECN)."""

    bricks: tuple[CausalBrick, ...]
    generated_count: int
    salient_sparks: tuple[DreamSpark, ...]
    verified_skills: tuple[MacroSkill, ...]
    rejected_sparks: tuple[tuple[DreamSpark, ECNVerdict], ...]

    def view(self) -> dict:
        return {
            "bricks": len(self.bricks),
            "generated_sparks": self.generated_count,
            "salient_sparks": len(self.salient_sparks),
            "verified_skills": [
                {
                    "skill_id": s.skill_id,
                    "name": s.name,
                    "mode": s.mode,
                    "steps": [
                        {
                            "action": st.action,
                            "object_class": st.object_class,
                            "entity": st.entity,
                            "tool": st.tool,
                            "pre": sorted(st.preconditions),
                            "add": sorted(st.delta_add),
                        }
                        for st in s.steps
                    ],
                    "description": s.description,
                    "novelty": round(s.novelty, 3),
                    "goal_fit": round(s.goal_fit, 3),
                    "salience": round(s.salience, 3),
                    "requires_confirmation": s.requires_confirmation,
                    "ecn": {
                        "accepted": s.ecn.accepted,
                        "trajectory_verdict": s.ecn.trajectory_verdict,
                        "cortex_verdict": s.ecn.cortex_verdict,
                        "gate_verdicts": list(s.ecn.gate_verdicts),
                    },
                }
                for s in self.verified_skills
            ],
            "rejected_sparks": [
                {
                    "spark_id": sp.spark_id,
                    "mode": sp.mode,
                    "description": sp.description,
                    "novelty": round(sp.novelty, 3),
                    "goal_fit": round(sp.goal_fit, 3),
                    "salience": round(sp.salience, 3),
                    "reasons": list(ev.reasons),
                    "trajectory_chains": list(ev.trajectory_chains),
                    "gate_findings": list(ev.gate_findings),
                    "cortex_verdict": ev.cortex_verdict,
                }
                for sp, ev in self.rejected_sparks
            ],
        }


# ---------------------------------------------------------------------------
# Stage 1: Hippocampal Deconstruction (ConcreteAnchor -> CausalBrick)
# ---------------------------------------------------------------------------


def deconstruct_anchor(
    anchor: ConcreteAnchor,
    *,
    codebook: CleanupCodebook | None = None,
    lesson_idx: int = -1,
) -> CausalBrick | None:
    """Deconstruct one ``ConcreteAnchor`` (+ 128-byte VSA coordinate) into a reusable ``CausalBrick``.

    Skips anchors whose outcome explicitly failed or was rejected so creativity only recombines
    viable causal primitives.
    """
    decoded: dict[str, str] = {}
    if anchor.vsa_coord and codebook is not None:
        for role, match in decode_episode(anchor.vsa_coord, codebook).items():
            if match.matched and match.value:
                decoded[role] = match.value

    fact_map: dict[str, str] = {}
    pre: set[str] = set()
    add: set[str] = set()
    rem: set[str] = set()
    high_risk = False
    irreversible = False
    rollback = ""
    est_cost = 1.0

    for raw_k, raw_v in anchor.facts:
        k = raw_k.strip().lower()
        v = raw_v.strip()
        if not k or not v:
            continue
        if k in ("pre", "precondition", "s0"):
            for item in v.split(","):
                if item.strip():
                    pre.add(_norm_atom(item))
        elif k in ("add", "effect", "delta_add", "ds", "post"):
            for item in v.split(","):
                if item.strip():
                    add.add(_norm_atom(item))
        elif k in ("del", "remove", "delta_remove"):
            for item in v.split(","):
                if item.strip():
                    rem.add(_norm_atom(item))
        elif k == "high_risk":
            high_risk = v.lower() in ("1", "true", "yes")
        elif k == "irreversible":
            irreversible = v.lower() in ("1", "true", "yes")
        elif k == "rollback":
            rollback = v
        elif k == "cost":
            try:
                est_cost = max(0.1, float(v))
            except ValueError:
                est_cost = 1.0
        else:
            fact_map[k] = v

    outcome = (fact_map.get("outcome") or decoded.get("outcome") or "").strip().lower()
    if outcome in _FAILED_OUTCOMES:
        return None

    action = (fact_map.get("action") or decoded.get("action") or anchor.tool or "").strip()
    if not action:
        return None

    object_class = (
        fact_map.get("object_class")
        or decoded.get("object_class")
        or (anchor.entities[0] if anchor.entities else "resource")
    ).strip()
    entity = (
        (anchor.paths[0] if anchor.paths else "")
        or (anchor.entities[0] if anchor.entities else "")
        or decoded.get("entity", "")
        or object_class
    ).strip()
    tool = (anchor.tool or decoded.get("tool") or "").strip()
    param = decoded.get("param", "")
    if not param and anchor.quantities:
        ql, qv, qu = anchor.quantities[0]
        param = f"{ql.strip()}:{qv:g}{qu.strip()}"

    if not add:
        add.add(_norm_atom(f"{action}_{object_class}_done"))

    desc = anchor.verbatim.strip() or f"{action} {entity}".strip()
    coord = anchor.vsa_coord
    if not coord:
        slots = {
            "action": action,
            "object_class": object_class,
            "entity": entity,
        }
        if tool:
            slots["tool"] = tool
        if param:
            slots["param"] = param
        if outcome:
            slots["outcome"] = outcome
        coord = bind_episode(slots)

    bid = f"brk-{_brick_hash(action, object_class, entity, ','.join(sorted(pre)), ','.join(sorted(add)))}"
    return CausalBrick(
        brick_id=bid,
        action=action,
        object_class=object_class,
        entity=entity,
        tool=tool,
        param=param,
        preconditions=frozenset(pre),
        delta_add=frozenset(add),
        delta_remove=frozenset(rem),
        description=desc,
        vsa_coord=coord,
        source_lesson_idx=lesson_idx,
        high_risk=high_risk,
        irreversible=irreversible,
        rollback=rollback,
        estimated_cost=est_cost,
    )


def deconstruct_hippocampus(memory: Hippocampus) -> list[CausalBrick]:
    """Deconstruct all non-disputed episodic anchors in ``Hippocampus`` into causal bricks."""
    bricks: list[CausalBrick] = []
    seen_ids: set[str] = set()
    codebook = memory.codebook
    for idx, lesson in enumerate(memory.lessons):
        if lesson.disputed:
            continue
        anchor = memory.anchors[idx] if idx < len(memory.anchors) else None
        if anchor is None or not anchor.has_structured_slots():
            continue
        brick = deconstruct_anchor(anchor, codebook=codebook, lesson_idx=idx)
        if brick is not None and brick.brick_id not in seen_ids:
            seen_ids.add(brick.brick_id)
            bricks.append(brick)
    return bricks


# ---------------------------------------------------------------------------
# Stage 2: Default Mode Network (DMN) — Analogical Transfer & Compositional Replay
# ---------------------------------------------------------------------------


def analogical_transfer(
    brick: CausalBrick,
    target: TargetDomain,
    *,
    codebook: CleanupCodebook | None = None,
    initial_state: frozenset[str] = frozenset(),
) -> DreamSpark | None:
    """VSA Analogical Transfer: unbind old object slot, rebind action topology onto ``target``."""
    old_ent = brick.entity.strip()
    old_cls = brick.object_class.strip()
    new_ent = target.entity.strip()
    new_cls = target.object_class.strip()
    if not new_ent and not new_cls:
        return None
    if new_ent.lower() == old_ent.lower() and new_cls.lower() == old_cls.lower():
        return None

    # Decode structural slots directly from the 128-byte VSA coordinate when a codebook is available
    preserved_action = brick.action
    preserved_tool = brick.tool
    preserved_param = brick.param
    if brick.vsa_coord and codebook is not None:
        dec = decode_episode(brick.vsa_coord, codebook)
        if "action" in dec and dec["action"].matched:
            preserved_action = dec["action"].value
        if "tool" in dec and dec["tool"].matched:
            preserved_tool = dec["tool"].value
        if "param" in dec and dec["param"].matched:
            preserved_param = dec["param"].value

    # Rebind action topology onto the novel domain object in 128-byte Episode-VSA space
    new_slots = {
        "action": preserved_action,
        "object_class": new_cls or old_cls,
        "entity": new_ent or new_cls,
    }
    if preserved_tool:
        new_slots["tool"] = preserved_tool
    if preserved_param:
        new_slots["param"] = preserved_param
    new_slots["outcome"] = "transferred_candidate"
    new_coord = bind_episode(new_slots)

    # Transfer symbolic preconditions & effects cleanly onto target domain
    new_pre = _rewrite_atoms(brick.preconditions, old_cls, new_cls)
    new_add = _rewrite_atoms(brick.delta_add, old_cls, new_cls) | _norm_atoms(target.goal_atoms)
    new_rem = _rewrite_atoms(brick.delta_remove, old_cls, new_cls)

    desc = f"{preserved_action} {target.display}".strip()
    if brick.description and old_ent and old_ent in brick.description:
        desc = brick.description.replace(old_ent, target.display)
    elif brick.description and old_cls and old_cls in brick.description:
        desc = brick.description.replace(old_cls, target.display)

    transferred_brick = CausalBrick(
        brick_id=f"trf-{_brick_hash(brick.brick_id, new_cls, new_ent)}",
        action=preserved_action,
        object_class=new_cls or old_cls,
        entity=new_ent or new_cls,
        tool=preserved_tool,
        param=preserved_param,
        preconditions=new_pre,
        delta_add=new_add,
        delta_remove=new_rem,
        description=desc,
        vsa_coord=new_coord,
        source_lesson_idx=brick.source_lesson_idx,
        high_risk=brick.high_risk,
        irreversible=brick.irreversible,
        rollback=brick.rollback,
        estimated_cost=brick.estimated_cost,
    )

    s0 = _norm_atoms(initial_state) | new_pre
    s_end = transferred_brick.apply(s0)
    sid = f"spk-trf-{_brick_hash(brick.brick_id, new_cls, new_ent)}"
    return DreamSpark(
        spark_id=sid,
        mode="analogical_transfer",
        steps=(transferred_brick,),
        initial_state=s0,
        terminal_state=s_end,
        description=desc,
        vsa_coord=new_coord,
        source_bricks=(brick.brick_id,),
        transfer_from=old_ent or old_cls,
        transfer_to=target.display,
    )


def compositional_replay(
    bricks: Sequence[CausalBrick],
    *,
    initial_state: Iterable[str] = (),
    max_depth: int = DEFAULT_MAX_CHAIN,
    max_sparks: int = 32,
) -> list[DreamSpark]:
    """Compositional Replay: chain causal bricks where previous ``ΔS+`` bridges next ``S0``."""
    pool = list(bricks)
    if len(pool) < 2 or max_depth < 2:
        return []

    s0 = _norm_atoms(initial_state)
    sparks: list[DreamSpark] = []
    seen_chains: set[tuple[str, ...]] = set()

    def _dfs(
        chain: list[CausalBrick],
        cur_state: frozenset[str],
        last_added: frozenset[str],
    ) -> None:
        if len(sparks) >= max_sparks:
            return
        if len(chain) >= 2:
            sig = tuple(b.brick_id for b in chain)
            if sig not in seen_chains:
                seen_chains.add(sig)
                desc = " -> ".join(b.description or f"{b.action} {b.entity}" for b in chain)
                coord = bind_episode(
                    {
                        "action": "->".join(b.action for b in chain),
                        "object_class": chain[-1].object_class,
                        "entity": chain[-1].entity,
                        "tool": chain[-1].tool or chain[0].tool or "chain",
                        "outcome": "compositional_candidate",
                    }
                )
                sparks.append(
                    DreamSpark(
                        spark_id=f"spk-cmp-{_brick_hash(*sig)}",
                        mode="compositional_replay",
                        steps=tuple(chain),
                        initial_state=s0,
                        terminal_state=cur_state,
                        description=desc,
                        vsa_coord=coord,
                        source_bricks=sig,
                    )
                )
        if len(chain) >= max_depth:
            return

        used_ids = {b.brick_id for b in chain}
        used_ops = {(b.action.lower(), b.entity.lower()) for b in chain}
        for nxt in pool:
            if len(sparks) >= max_sparks:
                return
            if nxt.brick_id in used_ids or (nxt.action.lower(), nxt.entity.lower()) in used_ops:
                continue
            # Preconditions must hold in current simulated world state
            if not (nxt.preconditions <= cur_state):
                continue
            # For step >= 2, require genuine causal chaining: previous ΔS+ feeds next S0
            if chain and nxt.preconditions:
                if not (nxt.preconditions & last_added):
                    continue
            elif chain and not nxt.preconditions:
                # Allow bridging only if it adds strictly new state atoms not already present
                if nxt.delta_add <= cur_state:
                    continue
            nxt_state = nxt.apply(cur_state)
            if nxt_state == cur_state:
                continue  # skip causal no-op
            _dfs(chain + [nxt], nxt_state, nxt.delta_add)

    # Seed DFS with bricks whose preconditions already hold in initial_state
    for first in pool:
        if len(sparks) >= max_sparks:
            break
        if first.preconditions <= s0:
            s1 = first.apply(s0)
            if s1 != s0:
                _dfs([first], s1, first.delta_add)

    return sparks


# ---------------------------------------------------------------------------
# Stage 3: Salience Network (SN) — Novelty (Sememe-VSA) × Goal-Fit
# ---------------------------------------------------------------------------


def score_salience(
    sparks: Sequence[DreamSpark],
    familiar: Familiarity,
    *,
    goal_atoms: Iterable[str] = (),
    goal_text: str = "",
    min_novelty: float = DEFAULT_MIN_NOVELTY,
    top_k: int = DEFAULT_TOP_K,
) -> list[DreamSpark]:
    """Score sparks by ``novelty × goal_fit`` using ``Familiarity`` (Sememe-VSA) and return Top-K."""
    g_atoms = _norm_atoms(goal_atoms)
    g_vec = encode_packed(goal_text) if goal_text.strip() else b""
    scored: list[DreamSpark] = []

    for sp in sparks:
        nov, _ = familiar.novelty(sp.description)
        if nov < min_novelty:
            continue  # too familiar -> already an old routine, not a creative spark

        # Goal fit combines symbolic terminal-state coverage + optional Sememe-VSA goal alignment
        sym_fit = 0.0
        if g_atoms:
            sym_fit = len(sp.terminal_state & g_atoms) / float(len(g_atoms))
        sem_fit = 0.0
        if g_vec:
            sp_vec = encode_packed(sp.description)
            sem_fit = max(0.0, float(hamming_similarity_bytes(sp_vec, g_vec)))

        if g_atoms and g_vec:
            goal_fit = 0.72 * sym_fit + 0.28 * sem_fit
        elif g_atoms:
            goal_fit = sym_fit
        elif g_vec:
            goal_fit = sem_fit
        else:
            # Open-ended REM dreaming without a fixed goal: reward non-trivial state progress
            gained = len(sp.terminal_state - sp.initial_state)
            goal_fit = min(1.0, 0.35 + 0.20 * gained)

        if goal_fit <= 0.0:
            continue

        sal = float(nov) * float(goal_fit)
        scored.append(
            DreamSpark(
                spark_id=sp.spark_id,
                mode=sp.mode,
                steps=sp.steps,
                initial_state=sp.initial_state,
                terminal_state=sp.terminal_state,
                description=sp.description,
                vsa_coord=sp.vsa_coord,
                source_bricks=sp.source_bricks,
                transfer_from=sp.transfer_from,
                transfer_to=sp.transfer_to,
                novelty=float(nov),
                goal_fit=float(goal_fit),
                salience=sal,
            )
        )

    scored.sort(key=lambda s: (-s.salience, -s.goal_fit, -s.novelty, s.spark_id))
    return scored[: max(0, int(top_k))]


# ---------------------------------------------------------------------------
# Stage 4: Executive Control Network (ECN) — WorldTrajectory + ActionGate + Cortex
# ---------------------------------------------------------------------------


def verify_spark(
    spark: DreamSpark,
    *,
    cortex=None,
    gate_context: GateContext | None = None,
    goal_atoms: Iterable[str] = (),
    require_goal_reached: bool = False,
) -> ECNVerdict:
    """Reality-test a dream spark through WorldTrajectory, ActionGate 14 checks, and Cortex."""
    reasons: list[str] = []

    # 1. Symbolic causal integrity check (no broken preconditions mid-chain)
    sim_state = set(spark.initial_state)
    for idx, step in enumerate(spark.steps):
        if not (step.preconditions <= sim_state):
            missing = sorted(step.preconditions - sim_state)
            reasons.append(f"causal-break@step{idx}: missing {', '.join(missing)}")
            break
        sim_state = (sim_state - set(step.delta_remove)) | set(step.delta_add)

    g_atoms = _norm_atoms(goal_atoms)
    if require_goal_reached and g_atoms and not (g_atoms <= sim_state):
        missing_g = sorted(g_atoms - sim_state)
        reasons.append(f"goal-unreached: missing {', '.join(missing_g)}")

    # 2. Multi-step WorldTrajectory compositional closure (defeats semantic chaining in dreams)
    import dataclasses
    from ..action_gate import REJECT, GateContext, gate

    traj = WorldTrajectory()
    traj_verdict = PROCEED
    traj_chains: list[str] = []
    gate_verdicts: list[str] = []
    gate_findings: list[str] = []
    recent_actions: list = []

    base_ctx = gate_context or GateContext(requires_verification=True, require_intent=True)
    active_goals = base_ctx.active_goal_ids or frozenset({spark.description})

    for idx, step in enumerate(spark.steps):
        planned = step.to_planned_action(purpose=spark.description, step_idx=idx)
        from .trajectory import EFFECT_ATOMS

        explicit_effects = [a for a in step.delta_add if a in EFFECT_ATOMS]
        obj_l = step.object_class.strip().lower()
        if obj_l in ("credential", "secret", "key", "password") and planned.kind == "read":
            explicit_effects.append("secret_in_context")
        if obj_l in ("network", "external", "remote") and planned.kind in ("send", "post", "publish"):
            explicit_effects.append("network_egress")
        if obj_l in ("antivirus", "firewall", "defense", "security") and step.action.strip().lower() in (
            "disable", "stop", "kill", "uninstall", "delete",
        ):
            explicit_effects.append("defense_weakened")

        inferred = infer_effects(
            effects=explicit_effects,
            tool_name=step.tool or planned.kind,
            action_kind=planned.kind,
            irreversible=step.irreversible,
        )
        assess = traj.simulate(
            inferred,
            tool=step.tool or planned.kind,
            label=step.description,
        )
        if assess.verdict == REFUSE:
            traj_verdict = REFUSE
        elif assess.verdict == DISSUADE and traj_verdict != REFUSE:
            traj_verdict = DISSUADE
        for ch in assess.triggered:
            if ch.rule.id not in traj_chains:
                traj_chains.append(ch.rule.id)

        # 3. ActionGate 14-check enforcement on every step of the synthesized plan
        step_ctx = dataclasses.replace(
            base_ctx,
            recent_actions=tuple(recent_actions),
            instruction=step.description,
            active_goal_ids=active_goals,
            trajectory=traj,
        )
        gd = gate(planned, step_ctx)
        gate_verdicts.append(gd.verdict)
        for fk in gd.findings:
            if fk not in gate_findings:
                gate_findings.append(fk)
        if gd.verdict == REJECT:
            reasons.append(f"action-gate@step{idx}: {gd.reason}")

        # Advance simulated trajectory & recent action log for downstream chain checks
        traj.commit(inferred, tool=step.tool or planned.kind, label=step.description)
        recent_actions.append(planned)

    if traj_verdict in (REFUSE, DISSUADE):
        reasons.append(f"world-trajectory:{traj_verdict}({','.join(traj_chains)})")

    # 4. Cortex 14-question Socratic safety check (when cortex is provided)
    cortex_verdict = ""
    if cortex is not None:
        u = cortex.understand([spark.description])[0]
        g = cortex.genome
        dec = decide(
            u.answers,
            sure_at=g["sure_at"],
            novelty=0.0,  # creativity is intentionally novel; evaluate safety on cortex answers directly
            novel_weight=0.0,
        )
        cortex_verdict = dec.verdict
        if dec.verdict in ("refuse", "support", "dissuade"):
            reasons.append(f"cortex:{dec.verdict}({dec.because})")

    accepted = (
        not reasons
        and traj_verdict == PROCEED
        and all(v != REJECT for v in gate_verdicts)
        and cortex_verdict not in ("refuse", "support", "dissuade")
    )
    return ECNVerdict(
        accepted=accepted,
        trajectory_verdict=traj_verdict,
        trajectory_chains=tuple(traj_chains),
        gate_verdicts=tuple(gate_verdicts),
        gate_findings=tuple(gate_findings),
        cortex_verdict=cortex_verdict,
        reasons=tuple(reasons),
    )


# ---------------------------------------------------------------------------
# Full Tri-Network Cycle & Confirmed Macro-Skill Promotion
# ---------------------------------------------------------------------------


def dream_cycle(
    *,
    bricks: Sequence[CausalBrick] = (),
    memory: Hippocampus | None = None,
    familiar: Familiarity | None = None,
    initial_state: Iterable[str] = (),
    goal_atoms: Iterable[str] = (),
    goal_text: str = "",
    target_domains: Sequence[TargetDomain] = (),
    cortex=None,
    gate_context: GateContext | None = None,
    min_novelty: float = DEFAULT_MIN_NOVELTY,
    top_k: int = DEFAULT_TOP_K,
    max_chain_depth: int = DEFAULT_MAX_CHAIN,
    require_goal_reached: bool = False,
) -> DreamReport:
    """Execute one complete Hippocampus -> DMN -> SN -> ECN creative synthesis cycle."""
    pool: list[CausalBrick] = list(bricks)
    codebook: CleanupCodebook | None = None
    if memory is not None:
        codebook = memory.codebook
        seen = {b.brick_id for b in pool}
        for b in deconstruct_hippocampus(memory):
            if b.brick_id not in seen:
                seen.add(b.brick_id)
                pool.append(b)

    fam = familiar
    if fam is None:
        fam = Familiarity.with_sememe(b.description for b in pool if b.description)

    s0 = _norm_atoms(initial_state)

    # DMN Stage: Divergent recombination (Analogical Transfer + Compositional Replay)
    candidates: list[DreamSpark] = []
    seen_sparks: set[str] = set()

    for b in pool:
        for td in target_domains:
            sp = analogical_transfer(b, td, codebook=codebook, initial_state=s0)
            if sp is not None and sp.spark_id not in seen_sparks:
                seen_sparks.add(sp.spark_id)
                candidates.append(sp)

    for sp in compositional_replay(pool, initial_state=s0, max_depth=max_chain_depth):
        if sp.spark_id not in seen_sparks:
            seen_sparks.add(sp.spark_id)
            candidates.append(sp)

    # SN Stage: Novelty × Goal-Fit filtering -> Top-K sparks
    salient = score_salience(
        candidates,
        fam,
        goal_atoms=goal_atoms,
        goal_text=goal_text,
        min_novelty=min_novelty,
        top_k=top_k,
    )

    # ECN Stage: WorldTrajectory + ActionGate + Cortex reality & safety convergence
    verified: list[MacroSkill] = []
    rejected: list[tuple[DreamSpark, ECNVerdict]] = []

    for sp in salient:
        ecn = verify_spark(
            sp,
            cortex=cortex,
            gate_context=gate_context,
            goal_atoms=goal_atoms,
            require_goal_reached=require_goal_reached,
        )
        if ecn.accepted:
            skill_name = f"{sp.mode}: {sp.description}"
            skill = MacroSkill(
                skill_id=f"msk-{_brick_hash(sp.spark_id, sp.description)}",
                name=skill_name,
                mode=sp.mode,
                steps=sp.steps,
                preconditions=sp.initial_state,
                postconditions=sp.terminal_state,
                description=sp.description,
                vsa_coord=sp.vsa_coord,
                novelty=sp.novelty,
                goal_fit=sp.goal_fit,
                salience=sp.salience,
                ecn=ecn,
                requires_confirmation=True,
            )
            verified.append(skill)
        else:
            rejected.append((sp, ecn))

    return DreamReport(
        bricks=tuple(pool),
        generated_count=len(candidates),
        salient_sparks=tuple(salient),
        verified_skills=tuple(verified),
        rejected_sparks=tuple(rejected),
    )


def promote_macro_skill(
    skill: MacroSkill,
    *,
    memory: Hippocampus,
    familiar: Familiarity | None = None,
    cortex=None,
    confirmed: bool = False,
    timestamp_ms: int = 0,
    journal_ref: str = "",
) -> ConcreteAnchor:
    """Commit a sandbox/human-confirmed ``MacroSkill`` back into episodic memory as a concrete skill.

    Enforces human/sandbox confirmation: refuses unconfirmed or ECN-rejected skills.
    """
    if not skill.ecn.accepted:
        raise ValueError("cannot promote a macro-skill that failed ECN reality/safety verification")
    if not confirmed:
        raise PermissionError("macro-skill promotion requires explicit sandbox or human confirmation (confirmed=True)")

    anchor = skill.to_concrete_anchor(timestamp_ms=timestamp_ms, journal_ref=journal_ref)
    lesson = Lesson(
        text=skill.description,
        answers={"kind": "task", "self": "no", "others": "self", "seat": "nobody", "clear": "yes", "effect": "makes"},
        source="dream",
        weight=1.0,
    )
    if cortex is not None:
        pat = cortex.patterns([lesson.text], [lesson.context])[0]
    elif memory.keys is not None and memory.keys.ndim == 2 and memory.keys.shape[1] > 0:
        # Deterministic neutral pattern placeholder when promoting to standalone Hippocampus
        import numpy as np

        pat = np.zeros(memory.keys.shape[1], dtype=np.float32)
    else:
        import numpy as np

        pat = np.zeros(16, dtype=np.float32)

    memory.keep(lesson, pat, anchor=anchor)
    if familiar is not None:
        familiar.learn([skill.description])
    return anchor


__all__ = [
    "CausalBrick",
    "DEFAULT_MAX_CHAIN",
    "DEFAULT_MIN_NOVELTY",
    "DEFAULT_TOP_K",
    "DreamReport",
    "DreamSpark",
    "ECNVerdict",
    "MacroSkill",
    "TargetDomain",
    "analogical_transfer",
    "compositional_replay",
    "deconstruct_anchor",
    "deconstruct_hippocampus",
    "dream_cycle",
    "promote_macro_skill",
    "score_salience",
    "verify_spark",
]
