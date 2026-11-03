# Socratic Gate 1 intent contract and staged memory: design v1

Chinese: [MEMORY.zh.md](MEMORY.zh.md)

> Status: v1 confirmed (2026-09-27). M1–M5 and M7 are implemented (`action_gate.py` /
> `episodes.py` / `lessons.py` / `consolidate.py`; live-loop integration in `loop_gate.py` /
> `loop_memory.py` / `supervisor.py`). The clarifying-question part of M6 is delivered by
> `dialogue/agent_bridge.py` (Wave C3): `ask_user` becomes a Socratic clarifying question
> and a destructive action becomes a Gate 2 challenge. Memory confirmation arrives through
> `ControlPacket.confirm_memory`; node-side handling was connected in Wave D. Revisions made
> during implementation are listed in the appendix "Implementation revisions".

## 0. Positioning and invariants

Memory lets the agent learn from the past: which step keeps failing, what the operator has
clarified, where a given control is. The dual Socratic gates question every step before it
runs. Combined, the gates **question with memory**.

Four invariants:
1. **Memory can only make the gates more cautious, never more permissive.** Relative to a
   no-memory baseline, a gate verdict with memory is the same or stricter (allow → rewrite /
   reject / reobserve), never the reverse. A property test enforces this.
2. **Gate 2 never reads memory.** "The operator approved deleting this file last time" is
   not authorization this time. Every destructive action needs a fresh operator signature
   bound to that action's hash. Memory never turns HITL into auto-approval.
3. **Memory stores neither raw observations nor secrets.** No screenshots, no full AX trees,
   no DIB contents; only distilled lessons and references to runs and steps. Every tier's
   entry point reuses the `looks_like_secret` filter and rejects any match.
4. **Provenance is traceable, retractable, and invalidated by revocation.** Every long-term
   memory records its author DID and its basis (evidence runs or operator confirmation). It
   is a signed journal event, so memory written by a revoked author is rejected
   automatically by `TrustPolicy`; the operator can also retract a memory explicitly.

---

## 1. Tier model (staged storage)

```
 S0 working ──► S1 episodic ──► S2 candidate ──► S3 consolidated
 in-process,     journal          local            signed journal
 volatile        projection       quarantine       events
 (this run)      (signed,         (not replicated, (replicated mesh-wide,
                 replicated)      untrusted)       retractable)
                                  ▲ model remember        │
                                  │ distilled from        ▼
                                  │ episodes
                                  └──── memory Socratic gate (promotion review)
```

| Tier | Content | Storage | Lifetime | Trust | Readers |
| --- | --- | --- | --- | --- | --- |
| S0 working | This run's recent actions, observation generations, pending questions | In-process | Discarded at run end | Valid within this run | Gates (`recent_actions`, etc.) |
| S1 episodic | Every step of every run: action key, gate verdict, outcome | **A projection of the Citadel journal** (a view; no data copied) | Same as the journal | Signed, tamper-evident | The distiller |
| S2 candidate | Lessons distilled from episodes; notes written by the model's `remember` | Local SQLite on this node, **not replicated** | TTL (expires after 30 days without new evidence, by default) | **Untrusted, quarantined** | The promotion gate, the Dialogue App (pending-confirmation list) |
| S3 consolidated | Memories that passed promotion review | Citadel journal `memory` events (signed, replicated mesh-wide) | Until retracted | Trusted (grounded, traceable) | Gates (can only add caution), the planning prompt |

Every memory carries a `scope`: `global` / `app:<bundle>` / `goal:<id>`. Memory can
therefore also be partitioned by task stage (goal, sub-goal) and does not leak into
unrelated tasks.

---

## 2. The memory Socratic gate (S2 → S3 promotion review)

The promotion threshold is proportional to what the memory can do:

| Memory class | Example | Effect | Promotion condition |
| --- | --- | --- | --- |
| **CAUTION** | "Clicking this button did nothing in 3 runs" | Can only make gates stricter | **Automatic promotion on evidence:** failures or rejections in ≥ `min_runs` distinct runs, and no success after the last failure |
| **FACT** | "'Save' in this application means the toolbar one" | Affects planning | **Operator confirmation required** (Dialogue App question: "Remember this?") |
| **PREFERENCE** | "Export to PDF by default" | Affects planning | **Operator confirmation required** |

- A memory of any class that matches the secret filter is discarded and never promoted.
- CAUTION retraction is also evidence-based: once the same action key succeeds in ≥
  `min_runs` runs, a `retract` is written. Retraction only returns to the baseline and never
  goes below it, consistent with invariant 1.
- Everything the model writes through `remember` goes to S2 with `source="model"`. It may
  paraphrase text from other applications on screen, which makes it an entry point for
  persistent prompt injection, so it enters S3 only after operator confirmation.

---

## 3. Gate 1 intent contract (extending `action_gate.py`)

Before an action executes, the gate must be able to answer four questions: why do it, what
should be observed, how to back out on failure, and whether the preconditions still hold.
"What should be observed" is already covered by `expected_observation`; this adds the other
three and connects memory.

```python
@dataclass(frozen=True)
class IntentContract:
    purpose: str = ""                       # which goal (goal id) it serves / why this step
    rollback: str = ""                      # how to back out on failure; empty = undeclared
    irreversible: bool = False              # explicitly acknowledged irreversible (still must pass Gate 2)
    requires_present: tuple[str, ...] = ()  # target ids that must still be in the current observation (gate-verifiable)

# Added to PlannedAction (not part of action_hash: it describes the rationale, not the effect)
    intent: IntentContract = IntentContract()

# Added to GateContext (all off / empty by default; existing callers unchanged)
    require_intent: bool = False
    active_goal_ids: frozenset[str] = frozenset()   # goals still active in the goal tree
    known_failures: frozenset[str] = frozenset()    # from S3 CAUTION: action_keys of repeatedly failing actions
```

New findings (all pure-function checks, registered in `_CHECKS`):

| Finding | Verdict | Trigger |
| --- | --- | --- |
| `intent-unproven` | REJECT → clarifying question | With `require_intent` on: a mutating action has no `purpose`, or a destructive action has neither `rollback` nor `irreversible` |
| `intent-contradiction` | REJECT | Declares `irreversible` yet gives a `rollback`; or `purpose` names a goal not in `active_goal_ids` (working for a completed or deleted goal) |
| `precondition-failed` | REQUEST_REOBSERVE | A target in `requires_present` is not in `target_present_ids` (judged only when the current observation is known) |
| `known-failure` | REJECT | `authz.action_hash(action)` ∈ `known_failures`, with the hint "fix the cause, do not retry" |

- `action_key` reuses `authz.action_hash` directly: it covers only effect-relevant fields,
  is stable, and is the same key Gate 2 uses.
- When the contract is missing, the gate does not fill in a rollback (a gate must not invent
  a recovery path for the agent), so the verdict is REJECT. The agent then raises
  `SocraticQuestion(gate_finding="intent-unproven")` over the Dialogue channel; after the
  operator's clarification completes the contract, the agent resubmits the action and the
  gate judges it again. **A clarification never changes a verdict to allow directly.**

---

## 4. Data structures (S1 / S2 / S3)

```python
# ---- S1 episodic: journal projection (episodes.py, a pure fold like goals.py) ----
# The step event payload gains three fields (append-only; the inputs of the state_hash chain are unchanged):
#   action_key: str   = authz.action_hash(planned)
#   outcome: str      = "ok" | "failed" | "no_change" | "rejected" | "unknown"
#   findings: list[str]  finding kinds reported by the gate

@dataclass(frozen=True)
class StepRecord:
    run_id: str
    seq: int
    action_key: str
    verdict: str
    findings: tuple[str, ...]
    outcome: str

@dataclass(frozen=True)
class Episode:
    run_id: str
    goal_id: str
    author: str                    # DID of the node that recorded this run
    state: str                     # run lifecycle state
    code: int | None
    steps: tuple[StepRecord, ...]

def build_episodes(events) -> dict[str, Episode]: ...

# ---- S2 candidate: local quarantine (lessons.py, SQLite, not replicated) ----
class MemoryClass(str, Enum):
    CAUTION = "caution"
    FACT = "fact"
    PREFERENCE = "preference"

@dataclass(frozen=True)
class Candidate:
    candidate_id: str              # sha256(canonical({mclass, scope, key, value}))
    mclass: MemoryClass
    scope: str
    key: str                       # CAUTION: action_key; FACT/PREF: stable name
    value: str                     # human-readable description (secrets removed, truncated)
    source: str                    # "consolidation" | "model" | "operator"
    evidence: tuple[str, ...]      # supporting run_ids (deduplicated)
    contradictions: tuple[str, ...]  # contradicting run_ids
    first_seen: float
    last_seen: float

def extract_cautions(episodes, *, scope="global") -> list[Candidate]: ...  # aggregates failures/rejections by action_key

# ---- S3 consolidated: journal memory events + projection (consolidate.py) ----
@dataclass(frozen=True)
class MemoryRecord:                # journal event body, kind="memory"
    op: str                        # "assert" | "retract"
    memory_id: str                 # = candidate_id
    mclass: MemoryClass
    scope: str
    key: str
    value: str
    basis: str                     # "evidence" | "operator"
    evidence: tuple[str, ...]      # run_ids
    confirmed_by: str = ""         # on operator confirmation: DID of the clarification packet's signer
    confirmation_ref: str = ""     # the corresponding probe_id

@dataclass(frozen=True)
class PromotionDecision:
    promote: bool
    basis: str                     # "evidence" | "operator" | ""
    needs_operator: bool
    reason: str

def review_candidate(c: Candidate, *, min_runs: int = 3) -> PromotionDecision: ...

@dataclass(frozen=True)
class MemoryView:
    known_failures: frozenset[str]                     # → GateContext.known_failures
    facts: dict[tuple[str, str], MemoryRecord]         # (scope, key) → confirmed facts / preferences

def build_memory(events, *, scope: str | None = None) -> MemoryView: ...
```

---

## 5. End-to-end data flow

```
 every step of a run ──► RunRecorder.record_step (appends action_key / outcome / findings)
                         │ signed journal
                         ▼
                 S1 build_episodes (projection)
                         │
                         ▼
                 S2 extract_cautions / model remember ──► local quarantine
                         │
                         ▼
                 memory Socratic gate review_candidate
                  ├─ CAUTION with enough evidence ──────► journal "memory" assert (node-signed)
                  └─ FACT / PREF ──► Dialogue question ──► operator clarification ──► assert (confirmed_by)
                                                                       │ replicated mesh-wide
                                                                       ▼
                 S3 build_memory ──► MemoryView
                  ├─ known_failures ──► GateContext ──► Gate 1 only adds caution
                  └─ facts ──────────► planning prompt (confirmed only)
                                        Gate 2: never reads memory
```

---

## 6. Invariants and tests

- **Monotonic caution (property test):** for a large set of generated (action, ctx, memory)
  combinations, `precedence(gate(a, ctx+memory)) ≥ precedence(gate(a, ctx))`.
- **Gate 2 isolation:** whatever values the memory fields take, the result of
  `_check_authorization` is unchanged.
- **No leakage before confirmation:** nothing in S2 appears in `MemoryView` or in the
  rendered prompt; FACT/PREF cannot be asserted without `confirmed_by`.
- **Revocation takes effect:** `memory` events signed by a revoked author do not enter the
  projection (journal-level `TrustPolicy`); after a `retract`, the gate returns to baseline.
- **Secret removal:** every entry point (`remember`, distillation, assert) rejects secrets.
- Every slice is mutation-tested; each mutant must fail fast, never by timeout.

---

## 7. Implementation slices

| Slice | Content | Depends on |
| --- | --- | --- |
| **M1** | Gate 1 intent contract: `IntentContract` + 4 new checks (off by default) | — |
| **M2** | S1: step payload gains `action_key` / `outcome` / `findings`; `episodes.py` fold | — |
| **M3** | S2: `lessons.py` local quarantine + `extract_cautions` + secret removal + TTL | M2 |
| **M4** | S3: `memory` journal events + `review_candidate` promotion gate + `build_memory` projection + retraction | M3 |
| **M5** | Wiring: `MemoryView.known_failures` → the GateContext of Supervisor / loop_gate; monotonic-caution property test | M1, M4 |
| **M6** | Dialogue integration (merged with D7): `intent-unproven` / `precondition-failed` → clarifying question; FACT candidate → confirmation question → assert | M4, D7 |
| **M7** | The agent's `remember` writes to S2 (behavior change, see decision 3) | M3 |

M1–M4 are pure logic and fully testable headless. M5 is where the live loop is connected.

---

## 8. Decisions requiring confirmation

1. **Tiering:** S0 working → S1 episodic → S2 candidate → S3 consolidated (each with a scope,
   partitionable by task stage). Is this the right reading of "staged storage"?
2. **Confirmation level for FACT / PREF promotion:** is a clarification issued with the
   session key enough (recommended: these memories affect only planning; the gates and
   Gate 2 are unchanged), or is an operator-key signature required?
3. **The existing `remember`:** switch it to write S2 candidates and stop injecting into the
   prompt directly (recommended: closes persistent prompt injection), or keep it as is for
   now?

**Decided (2026-09-27):** 1 use the four tiers; 2 a session-key clarification suffices;
3 `remember` writes S2 candidates (M7).

---

## Appendix: implementation revisions

Implementing M1–M4 tightened and completed the design above without changing its intent:

1. **CAUTION promotion and retraction as counts.** A caution is promoted after failures or
   no-effect outcomes in ≥ `min_runs` (default 3) usable runs **with no success ever**; any
   success marks the action "unstable", not a known failure. After promotion, it is retracted
   only once successful runs total ≥ `min_runs`; a single success is not enough. **Cautions
   given by the operator are never retracted automatically by evidence**, only by an explicit
   `retract`.
2. **Operator confirmation = a verifiable signed statement.** The App's session key signs
   `secdogie/memory-confirmation/v1` with content `{memory_id, subject=node DID,
   confirmed_at}`. Every node re-verifies it at projection time: valid signature, signer in
   `confirmers`, matching `memory_id`, and `subject` equal to the event's author. Otherwise
   `confirmed_by` would be only the node's own claim, while S3 replicates mesh-wide. With no
   `confirmers` configured, no operator-based memory counts (fail-closed).
3. **`memory_id` must equal the content hash** (`sha256(canonical({mclass, scope, key,
   value}))`), recomputed at projection time. A confirmation of content A cannot be moved
   onto content B (tested).
4. **S2 is a work queue, not evidence.** When a caution is promoted, its evidence is always
   recomputed with `lessons.tally` from S1's verified episodes; the candidate's stored
   `evidence` field is never read. Planting a candidate with 50 fake run_ids in the local
   file achieves nothing (tested).
5. **A gate rejection is not failure evidence.** `outcome="rejected"` (the gate refused; the
   action never ran) counts as neither failure nor success. Otherwise a known-failure caution
   would keep rejecting an action and feed itself on those rejections.
6. **`Episode` has no `author`** (the StateStore does not keep the author when
   materializing); it carries `verified` / `usable` (finished and chain-complete) instead.
   Only usable episodes take part in distillation.
7. **Retroactive revocation.** `build_memory(trust=TrustPolicy)` removes from the view the
   `memory` events of authors revoked later, even if those events were merged into the
   journal before the revocation.
8. **`run.check_chain`.** The chain check of `verify_run` is extracted into a pure function
   over materialized entities, so folding many runs materializes once; a malformed step
   returns failure instead of raising.

M1's four checks and every S1 / S2 / S3 verification were mutation-tested (M1 16, M2 13,
M3 18, M4 27: all 74 mutants killed, each failing in under one second).

### Live-loop integration (Stage 2 Wave A, M5 + M7)

- **Per-step correlation.** At gate time, `loop_gate.make_plan_gate(observer=…)` hands the
  action's `action_hash` and findings to `loop_memory.StepCorrelator`; the trace the loop
  writes immediately afterwards takes them only if the action kind matches, and only once.
  Steps that do not pass through the gate (done / look / ask_user …) carry no key, and their
  outcomes do not count toward memory.
- **Outcome.** `secdogie_agent.loop.classify_result` lives next to the result strings it
  parses: gate rejection / operator rejection / `refused:` → rejected; `error:` → failed; no
  visible change → no_change; elevated process not started → failed; anything else executed
  → ok.
- **Memory into the gate.** `Supervisor(memory=MemoryConfig(…))`. At the start of each run
  it reads S3 once → `known_failures`; `purpose` = the current goal_id; `active_goal_ids`
  comes from the goal tree; `require_intent` is on by default. In the loop,
  `known-failure` / `intent-unproven` / `intent-contradiction` block the step (the rejection
  reason enters the model's history); the other heuristics remain advisory.
- **Intent fields.** A model action may carry `rollback` / `irreversible` (only a literal
  `true` counts), as the system prompt explains; a high-risk step with neither is rejected
  and the model resubmits accordingly.
- **Automatic consolidation.** `consolidate` runs once after each goal ends; a failure is
  only logged and does not affect the goal's outcome.
- **M7.** `remember` goes into the S2 quarantine through `remember_hook`; memory in the prompt
  comes from `memory_block` (confirmed S3 only). The standalone agent CLI's `--memory` also
  changed to "quarantine first, inject only after confirmation", managed with
  `secdogie-agent memory list|confirm|forget`; per the owner's decision, entries that existed
  before the migration are treated as confirmed.

Mutation testing: all 18 citadel and 15 agent mutants killed.
