# Secdogie collaboration application: system architecture and data communication protocol specification v1.0

Chinese: [DESIGN.zh.md](DESIGN.zh.md)

> Status: v1.0 confirmed (2026-09-27), implemented slice by slice per section 4. Revisions
> made during implementation are listed in the appendix "v1.0 implementation revisions".
> No screen capture, OCR or central server is introduced.

## 0. Positioning and invariants

The **Secdogie Dialogue & Alignment App (the App)** is the single collaboration hub between
the operator and the agent for **intent alignment, state synchronization, logical
questioning and cryptographic authorization**. It is not a one-way remote control.

Four invariants (applying throughout the design):
1. **Consume, sign and converse only.** The App renders the `Observation` the agent pushes;
   it does not capture the screen, read process memory or perform any perception. How
   perception is produced (the owner's `agent/perception/`, #51) is outside this design.
2. **No central server.** Sessions run only over secdogie's existing lower layers: the
   libsodium encrypted tunnel, relays, and WebRTC P2P (signaling through Cloudflare Workers,
   handshake only, never handling plaintext).
3. **Keys never leave the device.** The operator's Ed25519 private key exists only locally
   in the App and is never held by an agent node; the App cannot self-sign on the agent's
   behalf, and the agent cannot sign on the operator's behalf.
4. **Gates decide and never execute; human confirmation has no switch.** The App is the end
   that turns Gate 2 human confirmation into **cryptography**; there is no path that releases
   a destructive action without a valid signature. Revocation (R1.2) applies to operators
   too: a revoked operator's signature no longer verifies.

Existing foundations reused (nothing re-invented):
- `secdogie_identity`: Ed25519 `did:key`, `sign_payload` / `verify_payload` / `canonical`,
  `TrustPolicy` (allowlist minus revocations).
- `secdogie_citadel.authz`: `action_hash` / `create_authorization` / `verify_authorization`
  (Gate 2, merged to main in PR #53).
- `secdogie_citadel.action_gate` / `socratic`: the dual Socratic gates (Gate 1 plan gate +
  instruction gate).
- `secdogie_agent.perception.observation`: `Observation` / `SemanticNode` / `Geometry` (the
  structured view, consumed read-only).
- `secdogie_transport`: `DirectUDPTransport` / `RelayClient` / rendezvous / WebRTC signaling.

---

## 1. Core subsystems and interaction model

The App multiplexes four logical channels over one **DID-authenticated, long-lived
session**. The P2P session layer maintains the session itself; each of the other three
subsystems owns only its payload semantics.

```
┌─────────────────────────────────────────────────────────────┐
│                    Secdogie Dialogue App                     │
│                                                               │
│  ┌───────────────┐  ┌────────────────┐  ┌─────────────────┐  │
│  │ ① Socratic     │  │ ② Zero-Screen  │  │ ③ Cryptographic │  │
│  │   Dialogue     │  │   Inspector    │  │   Guard (Gate2) │  │
│  │ (intent        │  │ (structured    │  │ (private key /  │  │
│  │  alignment)    │  │  view)         │  │  signatures)    │  │
│  └───────┬────────┘  └───────┬────────┘  └────────┬────────┘  │
│          └───────────────────┼────────────────────┘           │
│                    ┌─────────┴──────────┐                     │
│                    │ ④ P2P Session Layer │                     │
│                    │ (libsodium tunnel /  │                    │
│                    │  relay / WebRTC      │                    │
│                    │  signaling)          │                    │
│                    └─────────┬──────────┘                     │
└──────────────────────────────┼──────────────────────────────┘
                               │  one authenticated session, signed frames both ways
                    ┌──────────┴───────────┐
                    │  Agent node (secdogie) │
                    │  loop + action_gate +  │
                    │  socratic + authz      │
                    └───────────────────────┘
```

### ① Intent alignment and Socratic dialogue subsystem (Socratic Dialogue Channel)

- **Gate 1 questioning.** When the agent's `action_gate.gate()` or `socratic.review()`
  finds an ambiguous target, incomplete state (`REQUEST_REOBSERVE`) or a latent risk (a
  missing intent contract → `REWRITE`), the agent does not continue silently. It sends a
  `DialoguePacket(dialogue_type=SocraticQuestion)` on this channel, carrying a `probe_id`,
  the question text and optional choices (`suggested_options`). The agent blocks on that
  `probe_id`; a timeout is handled fail-closed (suspended, not released).
- **Intent repair and alignment.** The user replies with
  `DialoguePacket(dialogue_type=UserClarification, in_reply_to=probe_id)`, whose `content`
  supplies context or corrects the action graph. The agent treats it as **incremental
  input** to the current run (corresponding to `recovery.py`'s "re-observe before retrying"
  and appended task context) and continues reasoning without reinitializing the task. After
  alignment, the agent replies with `SystemStatus` to acknowledge adoption.
- Key point: this is a question–clarification loop, not chat. Every `probe_id` is either
  clarified or suspended on timeout; none is lost.

### ② Structured view inspection subsystem (Zero-Screenshot Inspector)

- **AX tree + DIB delta stream.** At every step (or on a state change) the agent pushes a
  `StateSnapshotPacket` containing the window's `semantic_nodes` **deltas** (added / changed
  / removed, located by a stable `automation_id` or `path_index`) and `dib_references`
  metadata (shape, hash and other **references** for DIB-only nodes, never pixels). The App
  rebuilds the tree from `SemanticNode.parent_index` / `path_index` and renders it as a TUI
  text node view.
- **Focus tracking.** `focused_node_index` points at the node the agent is currently
  hit-testing or about to act on; the App highlights it.
- The App only renders the structure it receives and **initiates no perception**. Rendering
  is a pure function, `(previous snapshot, delta) -> new tree view`, unit-testable headless.

### ③ Gate 2 cryptographic interception subsystem (Cryptographic Guard)

- **Local private-key isolation.** The operator's Ed25519 private key is stored in an
  encrypted keyfile local to the App (encrypted with a passphrase-derived key, or in the
  system keychain). It is decrypted into memory only at the moment of signing and zeroed
  afterwards. The private key never goes on the session and never reaches an agent node.
- **Challenge–response workflow.** When the agent's `action_gate` produces
  `UNAUTHORIZED_ACTION` for a `destructive` action, it sends a `Gate2ChallengePacket`
  (`action_hash`, risk assertion, `expires_at`) on this channel. The App parses it and shows
  the risk and the `action_hash`. On Approve, the App calls `authz.create_authorization` with
  the operator private key, wraps the token in a `Gate2ResponsePacket` and returns it; the
  agent executes only after `verify_authorization` passes (type / hash / subject / not
  expired / operator not revoked / valid signature). Deny or timeout means refusal.
- The `action_hash` the App displays must be **recomputed locally by the App from the
  received `target_action`** and match the challenge's `action_hash` before signing is
  allowed, which prevents the agent from misrepresenting an action to obtain a signature.

### ④ Decentralized networking and session management subsystem (P2P Session Layer)

- WebRTC P2P signaling handshake through Cloudflare Workers (only offer / answer /
  candidate exchanged, never plaintext) establishes a bidirectional encrypted tunnel based on
  C / libsodium; alternatively, native clients use `DirectUDPTransport` + `RelayClient`
  directly (falling back to a relay when hole punching fails).
- The App joins the mesh as an allowlisted operator DID. Long-lived connection, heartbeats
  and reconnection; while disconnected, the agent treats every unanswered `probe_id` /
  `challenge_id` as fail-closed.

---

## 2. Message flow and protocol data schemas (pure Python type definitions)

**Envelope and signature.** Every outbound packet = `Header` + one typed payload, signed as a
whole with `sign_payload` over `canonical(header + payload)`. The receiver runs
`verify_payload(obj, trust_policy)` and deserializes the payload only when the signature is
valid, the sender DID is allowlisted and not revoked, and the timestamp is fresh. Anything
that fails is silently dropped.

```python
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum

PROTOCOL_VERSION = "secdogie/dialogue/v1"

# ---- Envelope ---------------------------------------------------------------

@dataclass(frozen=True)
class Header:
    version: str                 # PROTOCOL_VERSION
    sender_did: str              # did:key (canonical form of sender_pubkey)
    recipient_did: str           # receiver DID: the packet is valid for this one node only and cannot be redirected (revision 1)
    session_id: str              # random id of one session
    seq: int                     # monotonically increasing within the session, paired with a replay window
    timestamp_ns: int            # sender clock; the receiver checks freshness within ±skew
    # The signature is not in the Header: the whole packet goes through sign_payload,
    # producing an outer signer/sig over canonical({header, kind, payload}).

class PacketKind(str, Enum):
    DIALOGUE = "dialogue"
    STATE_SNAPSHOT = "state_snapshot"
    GATE2_CHALLENGE = "gate2_challenge"
    GATE2_RESPONSE = "gate2_response"
    SESSION = "session"          # hello / heartbeat / bye

@dataclass(frozen=True)
class Envelope:                  # result of a successful open_envelope (authentication + parsing)
    header: Header
    kind: PacketKind
    packet: object               # one of the typed packets below (on the wire in to_wire form, signed with sign_payload)
    signer: str

# ---- ① Dialogue -------------------------------------------------------------

class DialogueType(str, Enum):
    SOCRATIC_QUESTION = "SocraticQuestion"     # agent -> user: clarifying question
    USER_CLARIFICATION = "UserClarification"   # user -> agent: clarification / repair
    SYSTEM_STATUS = "SystemStatus"             # agent -> user: status notice

@dataclass(frozen=True)
class DialoguePacket:
    probe_id: str                        # stable id of each question; a clarification points back via in_reply_to
    dialogue_type: DialogueType
    content: str
    in_reply_to: str = ""                # the probe_id a UserClarification answers
    suggested_options: tuple[str, ...] = ()   # candidates for quick selection by the user
    gate_finding: str = ""               # the action_gate finding kind that triggered the question (may be empty)

# ---- ② Structured view (deltas) ---------------------------------------------

class NodeOp(str, Enum):
    ADD = "add"
    UPDATE = "update"
    REMOVE = "remove"

@dataclass(frozen=True)
class NodeDelta:
    op: NodeOp
    index: int                   # stable handle of the node within this window's stream (assigned by the agent, unchanged across snapshots) (revision 2)
    # parent_index / focused_node_index / DibRef.node_index all reference handles;
    # automation_id / path_index only describe the node's origin and are not keys
    automation_id: str = ""
    path_index: tuple[int, ...] = ()
    # ADD / UPDATE carry the node projection (reusing perception's SemanticNode semantic fields)
    role: str = ""
    name: str = ""
    bounds: tuple[int, int, int, int] = (0, 0, 0, 0)   # x,y,w,h
    enabled: bool = True
    is_interactive: bool = False
    parent_index: int = -1

@dataclass(frozen=True)
class DibRef:
    # Reference metadata for a DIB-only node; never contains pixels. Fields come from perception's VisualReference.
    node_index: int
    width: int
    height: int
    pixel_format: str
    content_hash: str            # a change means the node's content changed; the App shows only hash and size

@dataclass(frozen=True)
class StateSnapshotPacket:
    window_id: int
    app_pid: int
    generation: int              # observation generation, for TOCTOU / staleness detection
    nodes: tuple[NodeDelta, ...] # deltas relative to the previous snapshot
    dib_references: tuple[DibRef, ...] = ()
    focused_node_index: int = -1 # node currently hit-tested / about to be acted on; highlighted by the App
    full: bool = False           # True = full resynchronization (first frame after reconnecting)

# ---- ③ Gate 2 challenge / response ------------------------------------------

class RiskLevel(str, Enum):
    LOW = "low"
    HIGH = "high"
    IRREVERSIBLE = "irreversible"   # deleting core files / funds / privilege escalation

@dataclass(frozen=True)
class TargetAction:
    # Corresponds one-to-one with authz.action_hash's _AUTHORIZED_FIELDS; the App recomputes the hash locally from it
    kind: str
    target_id: str = ""
    target_role: str = ""
    target_name: str = ""
    text: str = ""
    high_risk: bool = False      # same default as PlannedAction (revision 3)

@dataclass(frozen=True)
class Gate2ChallengePacket:
    challenge_id: str
    target_action: TargetAction
    risk_level: RiskLevel
    risk_explanation: str            # human-readable risk assertion (why irreversible / blast radius)
    action_hash: str                 # the hash the agent claims; the App must recompute and compare locally
    subject_did: str                 # target node (the token's subject)
    expires_at: float                # challenge validity; void once expired

class Verdict(str, Enum):
    APPROVE = "Approve"
    DENY = "Deny"

@dataclass(frozen=True)
class Gate2ResponsePacket:
    challenge_id: str
    action_hash: str                 # the hash the App recomputed locally (returned for reconciliation)
    user_verdict: Verdict
    authorization: dict = field(default_factory=dict)  # on Approve = the authz token
    # On Deny, authorization is empty; a failed verify_authorization on the agent side means refusal.

# ---- ④ Session control (revision 4) ------------------------------------------

class SessionEvent(str, Enum):
    HELLO = "hello"
    HEARTBEAT = "heartbeat"
    BYE = "bye"
    RESYNC = "resync"            # App -> agent: send a full snapshot

@dataclass(frozen=True)
class SessionPacket:
    event: SessionEvent
    note: str = ""
```

### End-to-end data flow

```
User assigns a task (DialoguePacket/UserClarification or a standalone submit)
      │
      ▼
Agent plans one action ──► action_gate.gate()
      │                     │
      │        ┌────────────┼─────────────┬───────────────┐
      │        ▼            ▼             ▼               ▼
      │   ALLOW (non-     REWRITE/      REJECT (no       (read)
      │   destructive)    REOBSERVE     authorization)
      │        │            │             │
      │        │            ▼             ▼
      │        │   ① SocraticQuestion  ③ Gate2Challenge
      │        │        (probe_id)        (challenge_id, action_hash)
      │        │            │             │
      │        │            ▼             ▼
      │        │   User Clarification   App recomputes and compares the hash locally
      │        │            │             │  match & Approve
      │        │            ▼             ▼
      │        │   Agent adopts it,     create_authorization → Gate2Response (token)
      │        │   continues              │
      │        ▼                          ▼
      │   Execute the action ◄──────  verify_authorization passes
      │        │
      ▼        ▼
② StateSnapshot delta stream (pushed continuously, focus highlighted)
```

A timeout or disconnect at any suspension point (probe / challenge) → fail-closed (not
released, not executed). Once a revocation arrives, a revoked operator's Gate2Response no
longer verifies.

---

## 3. Interactive application and TUI layout (Textual / Rich)

A lightweight interactive TUI with a split screen:

```
┌───────────────────────────┬───────────────────────────────────┐
│                           │  ② Structured view Inspector        │
│  ① Socratic dialogue /    │  window#42  gen 7  focus=dib:3      │
│    intent alignment       │  ▸ AXWindow "Drawing"               │
│                           │    ▸ AXGroup "Toolbar"              │
│  Agent: The target is     │    ▾ canvas (DIB-only)              │
│    ambiguous. Which "Save"│      • polyline "Outer wall" ★(focus)│
│    do you mean?           │      • layer "Structure"            │
│    [1] Top toolbar        │                                     │
│    [2] Dialog button      ├───────────────────────────────────┤
│  You: 1                   │  ③ Gate 2 cryptographic console     │
│  Agent: Adopted,          │  ⚠ IRREVERSIBLE: delete file-42     │
│    continuing.            │  action_hash: 9f3a… (recomputed ✓)  │
│                           │  Risk: unrecoverable, no rollback   │
│                           │  Expires: 04:59  [A]pprove  [D]eny  │
└───────────────────────────┴───────────────────────────────────┘
Status bar: session authenticated did:key:z6Mk…  · tunnel direct  · heartbeat 1.2s
```

- Left: the dialogue stream; questions carry choices, and selecting one replies with
  `UserClarification`.
- Top right: the structured tree, with `focused_node_index` highlighted (★); DIB-only nodes
  are marked but no pixels are shown.
- Bottom right: the Gate 2 prompt. It first recomputes `action_hash` locally and shows
  whether it matches the challenge; `[A]pprove` is allowed only on a match. Approve signs
  with the local private key (possibly after one more passphrase unlock).

The view layer is decoupled from the protocol layer: parsing every packet, recomputing the
hash and generating the token are pure functions, unit-testable headless with no TUI and no
network; Textual only renders and handles keys.

---

## 4. Module decomposition and implementation slices (implemented after design confirmation)

New package `dialogue/` (`secdogie-dialogue`, depending on identity + citadel), in slices:
1. **Protocol layer `protocol.py`:** the dataclasses above + `sign_envelope` /
   `verify_envelope` (reusing identity). Pure functions, unit-tested headless (signatures,
   freshness, replay, non-revocation).
2. **Gate 2 client `guard.py`:** local private-key management + receive `Gate2Challenge` →
   recompute and compare the hash locally → `create_authorization` → `Gate2Response`. Tests:
   refuse to sign on hash mismatch, refuse when expired, Deny yields no token, the agent
   refuses after revocation.
3. **Inspector `inspector.py`:** pure `(snapshot, delta) -> tree` merge + render model.
   Tests: add / update / remove deltas, focus highlight, DIB shows metadata only.
4. **Dialogue state machine `dialogue.py`:** the probe / clarify loop + suspension on
   timeout. Tests: question–clarification–adoption, fail-closed on timeout.
5. **Session layer `session.py`:** binds transport / WebRTC signaling, heartbeats,
   reconnection. Multi-process loopback as the exit criterion.
6. **TUI `app.py`:** Textual split screen, connected only to the pure models above.
7. **Agent-side integration:** `action_gate` findings → send probe / challenge;
   `verify_authorization` receives `Gate2Response`. Connected to T8 (the signed approval
   path).

Each slice is independently testable headless; after this design is confirmed, tests and
implementation are written slice by slice.

---

## Appendix: v1.0 implementation revisions

Implementing `protocol.py` completed section 2 in four places, each a tightening or the
filling of a dangling reference, without changing the design intent:

1. **`Header.recipient_did`.** The original design had no recipient binding: a packet sent to
   node A could be redirected to node B within the skew window (B's replay table does not
   have the session). The receiver now requires `recipient_did == own node DID`.
2. **`NodeDelta.index`.** The original `parent_index` / `focused_node_index` /
   `DibRef.node_index` referenced a "node index", but `NodeDelta` had no index field. It is
   now defined as a handle assigned by the agent and stable within one window stream; every
   cross-reference points to it.
3. **`TargetAction.high_risk` defaults to `False`,** consistent with `PlannedAction`;
   otherwise the same explicit arguments would produce different `action_hash` values. On
   the wire every field must be given explicitly; defaults are never filled in implicitly.
4. **`SessionPacket` / `SessionEvent`:** add the payload for `PacketKind.SESSION` (hello /
   heartbeat / bye / resync).
5. **Session key separated from the operator key** (`guard.py` / `keystore.py`). Section ③
   requires the operator private key to be "decrypted only at the moment of signing", and
   section ④ requires the App to join the mesh as an allowlisted DID; one key cannot satisfy
   both. There are now two:
   - **Session (device) key:** an ordinary did:key that signs every envelope and stays
     resident for the session; it goes on the node's *session allowlist*.
   - **Operator key:** encrypted at rest with Argon2id + SecretBox (mode 0600, never
     overwritten, KDF cost bounded above and below against runaway settings), unlocked with
     the passphrase only on Approve, used to sign one Gate 2 token and then discarded; it
     goes on the node's *operators allowlist*.
   A stolen device key can only converse; it cannot sign any destructive authorization
   (tested). CPython cannot guarantee memory zeroing, so the code makes no such claim; it
   only reduces the residency window to a single signature.

Gate 2 client rules (`guard.respond`): before signing, the App re-verifies on its own: the
locally recomputed `action_hash` must equal the node's claimed value, `subject_did` must be
the authenticated peer of this session, and the challenge must not have expired; otherwise
it refuses to sign (`GuardRefusal`). The token signs **the locally displayed action**, the
subject is the session peer's DID, `expires_at` does not exceed the challenge's validity, and
`valid_from` is set back 30 s to tolerate clock skew. Deny never carries a token.

Inspector merge rules (`inspector.apply`): only deltas that apply cleanly are adopted;
**nothing is guessed**. Updating or removing an unknown node, a duplicate add, a missing
parent, a cycle, a dangling focus or DIB reference, or a delta for a window without a
baseline all keep the last consistent tree and set `needs_resync`; from then on no delta is
applied until a full snapshot arrives (the App sends `SessionEvent.RESYNC`). A non-increasing
`generation` for the same window is stale and ignored. Removal cascades over the whole
subtree and its DIB metadata. The upward traversal of tree validation is structurally bounded
by the node count, so malicious input cannot make it loop forever. Node names come from
arbitrary applications' UI text: control characters are replaced and names truncated to 120
characters before rendering (the TUI layer must also render them as plain text, never
markup).

Dialogue state machine rules (`dialogue.py`): the agent-side `ProbeLedger` accepts only a
**non-empty** `UserClarification` naming a question that is **still open and not expired**;
on expiry the question becomes `EXPIRED` (= no, the step stays suspended), a late answer
never revives an expired question, and a question can be answered only once; a `wait()`
timeout likewise marks it expired. The App-side `Conversation` accepts only questions and
status messages, never a "clarification" sent by the agent (the agent cannot answer for the
operator); a duplicate `probe_id` is not a new question; an answer must be exactly one of
text or a choice number, and empty answers are not sent. A `SystemStatus` from the agent
whose `in_reply_to` names a question closes it in the App (adopted / expired), reusing the
existing field with no protocol change. Dialogue text comes from the agent (which may
paraphrase other applications' UI text), so control characters are always sanitized before
rendering.

Purity test (`tests/test_purity.py`): an AST check over the whole package forbids importing
screen-capture, OCR or image libraries, input injection, `ctypes` / `cffi` / `mmap`, and the
agent's perception layer `secdogie_agent`. Invariant 1 is enforced by CI.

Concrete protocol-layer rules (`open_envelope`):
- **Authenticate before parsing:** the header and payload are parsed only after the signature
  is valid and the signer is on the trust policy (`TrustPolicy`, revocation means refusal);
  an unauthenticated peer never reaches the parser.
- **Strict schema:** every field is required, unknown fields are rejected (no pixel field can
  be smuggled into the structured view), and types are exact (a bool is not an int, a
  non-finite float is not a time).
- **Replay protection:** `timestamp_ns` must be within ±30 s of the local clock; `seq` for the
  same (sender, session) strictly increases. Rejected packets do not consume a seq. The
  session table is bounded; when full, it forgets only sessions idle for more than twice the
  skew window (by then no packet it ever accepted is still fresh, so forgetting cannot reopen
  replay); if every session is active, new sessions are refused (fail-closed), and active
  sessions are never evicted.

### Stage 2 revisions (Wave C0)

6. **Replay protection becomes a sliding window.** `ReplayGuard` keeps, per (sender,
   session), the highest seq and a 256-entry bitmap below it, as the transport's frame
   counter does. Out-of-order packets within the window are accepted, exactly once; packets
   older than the window are rejected. The earlier "strictly increasing" rule wrongly dropped
   packets reordered on UDP and retransmissions that arrived after newer packets.
7. **`StateSnapshotPacket.base_generation`.** A delta must declare the generation it is based
   on (`0 ≤ base < generation`); a full snapshot uses -1. The Inspector applies a delta only
   when its base equals the generation it holds; otherwise it records a gap and requests
   resynchronization. A lost intermediate delta is therefore detected instead of leaving the
   view silently stale.
8. **`ControlPacket` (`PacketKind.CONTROL`).** Operator requests from App → node: `add_goal` /
   `stop` / `pause` / `resume` / `confirm_memory` (carrying a memory confirmation signed with
   the session key) / `retract_memory`. The node replies with a `SystemStatus` whose
   `in_reply_to = request_id`. Invariants: goal operations must carry a `goal_id`, `add_goal`
   must carry a task title, memory operations must carry a `memory_id`, and only
   `confirm_memory` carries a confirmation.
9. **Session layer `session.py` (C2),** running over the transport's `ChannelMux` (C1) on
   channel `dialogue/v1`:
   - Fragments ≤ 16 KiB; reassembly has three limits (4 MiB per message, 32 concurrent
     messages, 10 s timeout); out-of-range indices, oversized totals, oversized fragments,
     inconsistent totals and duplicate fragments are dropped and never stored.
   - Reliable channels: dialogue / gate2 / control / session (except heartbeats) are
     acknowledged per message with exponential-backoff retransmission; after 6 unacknowledged
     attempts `on_undeliverable` is raised, and the upper layer handles it fail-closed, never
     assuming delivery. Duplicate arrivals are acknowledged but not delivered again (the
     envelope layer's sliding window rejects them).
   - Snapshots use the unreliable channel and rely on `base_generation` to detect gaps and
     request resynchronization.
   - Heartbeats every 2 s; three consecutive intervals without hearing the peer raise
     `on_peer_down`, and hearing it again raises `on_peer_up`.
   - Only packets signed by this session's peer DID are delivered: other keys on the trust
     policy do not count as this peer.
   - All timing goes through `tick(now)`; tests drive it with a fake clock and a seeded
     in-memory link with loss, duplication and reordering, so results are deterministic;
     there is also one real UDP loopback case. Mutation testing: 20/20 killed.
10. **Node-side bridge `agent_bridge.py` (C3, slice 7 of the original design).**
    `OperatorBridge` plugs into `Supervisor` as citadel's `OperatorHooks`, so citadel takes
    no dependency on dialogue.
    - **Gate 2:** the gate (`loop_gate.make_plan_gate(authorize=…)`) calls
      `authorize(planned)` for every destructive action; the bridge sends a
      `Gate2ChallengePacket` (action, risk, node-computed hash, node DID, validity) and
      blocks. The token returned with Approve is **verified by the gate**
      (`_check_authorization`); the bridge does not judge tokens itself. Deny, timeout, peer
      loss or an irrelevant answer all return None → the gate refuses; `unauthorized-action`
      blocks in the loop.
    - **The signature is the confirmation (owner decision):** after the gate releases a
      destructive action the operator has just signed, the bridge records a one-time marker;
      the loop's subsequent high-risk confirmation consumes the marker and passes without
      asking again. The marker holds only for "same action + released by the gate + just
      signed by the operator", is cleared once used, and is also cleared when the peer goes
      down. Other confirmations (plan approval, unsigned steps) go through an Approve / Deny
      question, and only a literal `Approve` counts.
    - **ask_user → clarifying question:** `ask(question)` sends a question through
      `ProbeLedger`, waits for the answer and returns the operator's text; the loop writes it
      into the model's history and continues reasoning (the old `bool`-returning wiring still
      works). The Supervisor records question and answer as `ask_request` / `ask_result`
      journal events.
    - **Control and session:** a `ControlPacket` goes to the node's `on_control`, and the
      result is returned as a `SystemStatus` with `in_reply_to = request_id` (no handler, or a
      handler that raises, yields `refused: …`); `RESYNC` goes to the snapshot publisher;
      `BYE` means the peer is down.
    - Mutation testing: 17 dialogue, 15 citadel and 4 agent mutants, all killed.
11. **The App itself (C4).** `app.py`'s `AppController` is the App without a screen; the
    Textual UI (`tui.py`, optional dependency `[tui]`) and the headless script only call its
    methods and read its views.
    - **The two keys are used separately:** the session key signs envelopes and memory
      confirmations; the operator key is never resident. `approve` takes an `unlock` callback,
      called once for this one signature, and the key is discarded on return.
    - **Two re-checks before signing:** once when the challenge arrives, and again when
      Approve is pressed, **before** the operator key is unlocked (a challenge that cannot be
      signed never prompts for the passphrase); after unlocking, `guard.respond` checks a
      third time as of the signing moment (an expiry while the passphrase is typed means no
      signature). Once signed, it is settled; the same `challenge_id` is never asked again.
    - **Memory confirmations are also recomputed locally.** A new `MemoryCandidatePacket`
      (`PacketKind.MEMORY_CANDIDATE`, node → App) carries the full content. The App
      recomputes the id from the displayed content with `lessons.candidate_id` and runs the
      same `validate` (including secret detection); a mismatch or invalid candidate cannot be
      confirmed. A node cannot display one note and obtain confirmation of another.
    - **Disconnect clears state:** when the peer goes down or says bye, pending challenges and
      questions are cleared on the App side (the node has already treated them as failed); on
      reconnection the App requests a full view automatically. When the view has a gap, it
      requests resynchronization at most once every 2 s.
    - **UI:** all text from the node is rendered as `rich.text.Text`, never parsed as markup;
      `/approve` applies only to the one challenge shown on screen when the command was
      entered, and no key approves directly.
    - **Headless script** (`--headless`, for end-to-end tests): strict schema; `approve` /
      `deny` must state the action's kind and its object (target id / name, or the key text
      for key actions), matching and signing exactly one challenge; there is no "approve all".
    - **CLI:** `secdogie-dialogue connect` trusts only the single DID named by `--node` (the
      transport and session layers share that one allowlist); `--transport-key` and
      `--node-binding` must be given together; `new-operator-key` generates an encrypted
      operator key.
    - Tests: 44 controller cases, 9 Textual pilot cases, 8 CLI cases (including a real UDP
      loopback: the App script approves and the node's `verify_authorization` passes);
      mutation testing 31/31 killed.
12. **Structured-view publisher (C5).** `publisher.py`'s `SnapshotPublisher` runs on the node
    and shapes the accessibility-element targets the loop hands to the model at each step
    (the `AgentConfig.on_targets` hook, passed through by the Supervisor via
    `OperatorHooks.on_targets`) into `StateSnapshotPacket`s.
    - **Shapes only, never perceives:** no screen capture, no process access, no import of
      the agent (enforced by the purity test). Each element reads only the structural fields
      listed in `FIELDS` (role, name, automation id, bounds, enabled, is_interactive,
      visual_reference), and DIBs only `DIB_FIELDS` (width, height, pixel format, bit depth,
      content hash). Tests use "tripwire" objects to prove it touches no other attribute and
      therefore cannot carry out any pixel buffer. The perception layer was not changed.
    - **Stable handles:** the same identity (role, name, automation id, rank among identical
      elements) keeps the same handle across steps, so the view changes by delta rather than
      flickering wholesale; the loop provides a flat target list, all attached under one
      synthetic window node.
    - **Deltas and resynchronization:** the first frame is full, then only ADD / UPDATE /
      REMOVE, each delta naming the generation it is based on; a window change starts a new
      stream; a full snapshot is sent every 50 frames as a backstop; after a send failure the
      next frame is full. The App's RESYNC is answered immediately with the current full
      tree, even while the loop waits for the operator.
    - **Limits:** at most 2,000 nodes per frame, text truncated to 200 characters, anomalous
      coordinates treated as 0 without raising. A hook failure only logs a warning and never
      affects the step.
    - Tests: 16 publisher cases (including a property test folding 30×25-step random
      sequences through the real Inspector with no gaps), 2 loop-hook cases, 2 Supervisor /
      agent_run_task pass-through cases; mutation testing 20/20 killed.
