# secdogie roadmap

Chinese: [ROADMAP.zh.md](ROADMAP.zh.md)

> The overall implementation goal and the milestones that lead to it. The full
> architecture, component map and end-to-end authentication chain are in
> [`ARCHITECTURE.md`](ARCHITECTURE.md).

## North Star

A **decentralized, DID-authenticated, supervised** autonomous agent running on nodes
**you own or are authorized to use**. It has no central server and persists through the
P2P mesh. It learns from **public or authorized** sources (**structured perception, no
screen capture**). A **two-tier Socratic gate** reviews the intent of every step. It takes
physical actions **only on an authenticated local device, under a capability grant and
human-in-the-loop (HITL) confirmation**.

**Non-negotiable constraints (prohibited):** process-memory writes, remote thread
injection, kernel-level HID, EDR evasion or anti-detection, covert persistence, privilege
escalation, bypassing user authorization or macOS permissions, changing HITL to
auto-approve by default, traffic obfuscation, NAT-traversal techniques used for detection
evasion. **Always keep:** memory = read-only; execution = supervised; high-risk =
fail-closed and requiring human confirmation at every entry point (no switch disables
this); physical action = explicit capability.

## Five parallel tracks

- **A Decentralized networking.** DID identity → sessions and endpoints → direct P2P over
  UDP → rendezvous → direct-path upgrade with relay fallback → membership gossip →
  Kademlia routing → (later) rendezvous over UDP, on-the-wire gossip, C + libsodium tunnel
  hardening (T9).
- **B Distributed state and learning.** Signed journal and `StateStore` → anti-entropy
  replication converging across the mesh → content-addressed observation and knowledge
  storage (large payloads referenced by `content_hash`) → (later) evaluate a true CRDT.
- **C Reasoning (Socratic gates and supervision).** Instruction gate and plan gate →
  **2.7 Agent↔Citadel run loop** → 2.8 crash recovery (a crash while executing
  re-observes before retrying) → Stage 2: intent contract and staged memory (S1–S3) in the
  live loop, Gate 2 signed through the Dialogue App.
- **D Authenticated device execution.** Structured observation fusion (AX + DIB by
  reference) and target TOCTOU protection → 2.9 signed capability-grant model → OS-side
  wiring of native AX identity and generations (validated on macOS and Windows) → DIB
  fully integrated into the runtime.
- **E Control and operations.** Native desktop GUI, console, fleet coordination, CI
  matrix, releases, documentation; maintained throughout.

## Milestones (each with exit criteria)

- **M1 Foundational authentication chain: ✅ achieved.** 2.1→2.6 + P2P.1. Exit criteria:
  identity → session → state → gate → (supervised) action is verifiable at every step,
  with the whole suite green headless.
- **M2 P2P mesh and state convergence: ✅ achieved.** P2P.2 direct-path upgrade, P2P.3
  membership gossip, Replication.1. Exit criteria: any two authorized nodes communicate
  directly or through a relay, and signed state written on one node converges to the
  others (multi-node loopback tests).
- **M3 Closed run loop and capability governance: ✅ achieved (Stage 2, #56 / #57 / #60 /
  #61 / #62).** C's 2.7 and 2.8, D's 2.9. Exit criteria: given a goal, a node plans →
  observes → passes the gates → executes (capability + HITL) → verifies → writes back; after
  a crash it re-observes before recovering safely; every action is checked against a
  signed capability. Verified by `secdogie-node`'s end-to-end tests over real UDP (Gate 1,
  Gate 2 signatures, clarifying questions, staged memory, known-failure rejection).
- **M4 End-to-end vertical slice: 🔨 partial.** The full single-node "learn + act" chain
  runs headless (`node/tests/test_e2e.py`). The demonstration of results converging across
  multiple nodes through replication is deferred to Stage 3.
- **M5 Hardening and deployment: 🔜.** Native OS wiring validated on real hardware, tunnel
  confidentiality on the data plane, packaging, releases, documentation, security review.

## Slice plan (2026-09)

One PR per item: audit → implement → test → run the full suite → lint → commit. ✅ done,
🔨 in progress, 🔜 not started.

**Decisions taken:**
- The encrypted tunnel uses only this repository's C + libsodium implementation
  (`tunnel/`); no second VPN back end. Hardening (v2 handshake, rekeying, end-to-end
  relaying, control socket) is Stage 3 item T9.
- Relays forward ciphertext only (end-to-end). NAT traversal is coordinated through the
  mesh and only between allowlisted nodes.
- Whole-machine routing = mesh subnet + exit node.
- Revocation is a Master **k-of-n threshold signature** and is **permanent**.
- Perception: **screen capture, OCR and vision parsing are removed entirely** (owner
  decision, 2026-10, superseding the earlier "screen capture only when explicitly
  enabled"). Only the AX/UIA accessibility tree and Atlas's read-only process-memory
  structure view are used; an application with neither fails closed ("no structured
  view"). Reading a target process remains a read-only copy (token wall and denylist
  unchanged); "zero-copy" refers only to the Atlas → agent shared-memory hand-off (D5/D6,
  not started).
- The inference mesh's WebRTC transport uses aiortc as an optional dependency; the DID is
  bound to the DTLS fingerprint inside the DataChannel, and the signaling Worker is
  unchanged (W5, built). Browser tabs join only as admitted, forward-only nodes, after a
  click, with a key that lives in the tab and nothing stored (W6, built); how the mesh
  obtains nodes, and what is excluded, is in [`docs/NODES.md`](docs/NODES.md).
- HITL confirms only high-risk actions by default, but high-risk actions require
  confirmation at every entry point.

| Track | Slices | Status |
| --- | --- | --- |
| S Security fixes and corrections | S1 SDTP switches only after confirmation · S2 fleet secure mode all-or-nothing · S3 documentation aligned with code | ✅ ✅ ✅ |
| A Mesh runtime | 2C any allowlisted node can act as a relay · A0 rendezvous over UDP (T3, #64; inference nodes bootstrap and discover through it) · A1 wire gossip (T4) · A2 `secdogie-node` runtime | ✅ ✅ 🔜 ✅ |
| R 3.0 Revocation | R1 MasterSet / threshold revocation statements / TrustPolicy · R2 enforcement everywhere plus cache invalidation · R3 zero-trust closed by default · R4 propagation (fast frames built; durable propagation through the journal = T6) · R5 self-halt on revocation | ✅ ✅ ✅ 🔨 ✅ |
| T9 Tunnel hardening | C + libsodium tunnel: hygiene, fuzzing and netns smoke tests → Noise IK v2 handshake → timers, rekeying, DoS rate limiting → end-to-end relaying → local control socket | 🔜 (Stage 3) |
| C M3 live loop | C1 CI runs real loop tests · C2 HITL corrections (high-risk list, confirmation at every entry point, signed approval = Gate 2 through the Dialogue App) · C3 fleet/console/desktop approval paths · C4 stronger gating (intent contract + known-failure memory) · C5 run records earlier (per-step `action_key` and outcome) · C6 single entry point (`secdogie-node`) · C7 device classes and headless isolation (T7) | ✅ ✅ 🔜 ✅ ✅ ✅ 🔜 |
| D Perception | D1 observation/target in the loop · D2 structure-first (Windows UIA structure image) · D3 macOS AX hit-testing · D4 Atlas read-only bridge (first a loopback `/inspect` client) · D5 remove the pixel and vision paths (`screen`, `mac_capture`, `ax_image`, `reflex`, `look`, pixel-diff verification, vision prompts; the frame hash becomes an AX-tree plus Atlas-snapshot hash; a purity test forbids screen capture) · D6 Atlas → agent shared-memory seqlock hand-off (zero-copy; the reader is `perception/dib.py`) | 🔜 |
| W Browser | W1 browser DID + DID authentication (a non-extractable, tab-scoped key; the DID is authenticated inside the DataChannel by `rtc-hello`, the signaling Worker stays unauthenticated; operator `admission/v1`, forward-only, ≤ 30 days, flooded) · W2 cross-language signature vectors (now also `fixtures/vectors/mesh.json`) · W3 aiortc bridge proof of concept · W4 browser as observer and approver (`symbiont/` + `graph/`: dual Socratic gates, inline signing, attention scheduling, WASM state graph) · W5 inference-mesh `RtcWire` (`[webrtc]` optional dependency, a signed `rtc-hello` binding DID ↔ DTLS fingerprint, `FailoverWire` falling back to UDP / relay) · W6 browser forward-only mesh node (`symbiont/mesh.html`: heartbeats, role-checked flooding, carrying with path checks, Lockdown refusal; Playwright end-to-end test) | ✅ ✅ 🔜 🔨 ✅ ✅ |
| I Inference mesh (three-state model) | I0 the three states (#75) · I1 reference topology (four serving nodes, the coordinator attaches to the neighbours of a failed node, standby forwarders anchored to the key broker, flooded fault reports) · I2 mode state machine (only an operator reset leaves Lockdown) · I3 session lifecycle and memory bounds · I4 Merkle-DAG heartbeats (fork isolation without Lockdown, witnessed links, ledger-root anti-entropy) · I5 history-aware decision gate (hard vetoes, escalation to cross-checking) · I6 node runtime and discovery (`secdogie-inference run`, rendezvous bootstrap, 3–6 witnessed transit links) | ✅ ✅ ✅ ✅ ✅ ✅ ✅ |
| M Completion | M4 vertical-slice demonstration · M5 security review, validation on real hardware, release | 🔜 |

**Stage 2 (runnable closed loop) is complete:** P0 and Waves A / B / C / D / E (#56, #57,
#60, #61, #62 and the documentation PR). The seven exit criteria are in section 5 of
[`ARCHITECTURE.md`](ARCHITECTURE.md).

**Stage 3 order:**
1. T4 on-the-wire gossip, T6 durable revocation propagation (T3 is done).
2. T7 device classes, C3 fleet/console/desktop approval paths.
3. T9 tunnel hardening.
4. W and M (multi-node vertical-slice demonstration, security review, validation on real
   hardware, release).

The perception track (D) is driven by the owner.

The networking track and the agent track change different packages and can proceed in
parallel.

## Standing discipline

Every slice is `inspect → implement → test → run the full suite → lint → commit`; no
big-bang rewrites. Everything is testable headless over loopback and gated by DID
signatures and allowlists. Before each commit, a grep-based regression check confirms that
no prohibited primitive is present. Conflicts and the actual state are recorded in the
documentation as they are; no architecture diagram is allowed to claim completion that
does not exist.
