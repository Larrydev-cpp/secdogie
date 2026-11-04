# secdogie: a decentralized, supervised autonomous agent

Chinese: [README.zh.md](README.zh.md)

secdogie is a **decentralized, DID-authenticated, supervised** autonomous agent that
runs only on nodes **you own or are authorized to use**. It has no central server and
persists through a P2P mesh. It learns only from **public or authorized** sources. A
**two-tier Socratic gate** reviews the intent of every step. It takes physical
actions **only on an authenticated local device, under a capability grant and
human-in-the-loop (HITL) confirmation**.

**Perception is structured.** The target perception stack is the accessibility tree
(macOS AX / Windows UIA), read-only structured DIB references, and the AX tree or text of
authorized web sessions. It does not use screen pixels. **Current status:** on macOS the
live loop renders the AX tree into a structure image for the model; on Windows and Linux
the live loop **still captures the screen**. The owner has decided to remove the
screen-capture and vision path entirely. Applications without an accessibility tree
will use Atlas's read-only process-memory structure view; when neither is available,
the agent fails closed. This is not implemented yet; see Track D (D5 / D6) in
[`ROADMAP.md`](ROADMAP.md).

> Full architecture: [`ARCHITECTURE.md`](ARCHITECTURE.md). Roadmap: [`ROADMAP.md`](ROADMAP.md).
> Source-level audit and specification alignment: [`docs/AUDIT-P2P-ALIGNMENT.md`](docs/AUDIT-P2P-ALIGNMENT.md).
> Terminology: [`docs/GLOSSARY.md`](docs/GLOSSARY.md).

---

## Four pillars

1. **Decentralized networking.** DID identity (Ed25519 `did:key`), a session and
   endpoint abstraction, and direct P2P transport over UDP. Peers discover each other
   through an authenticated rendezvous service, upgrade to a direct path or fall back
   to a relay, and converge membership by gossip-based anti-entropy. A hub serves only
   as bootstrap and relay; the mesh survives its loss.
2. **Distributed acquisition and learning over P2P.** A signed, hash-chained,
   append-only event journal with a `StateStore` on top. Journal and state converge
   across the mesh by **mutually authenticated** anti-entropy: signed evidence that one
   node learned propagates to every other authorized node.
3. **The Socratic system.** A two-tier gate: an **instruction gate** (intent review) and
   an **action-plan gate** (`GateDecision`: allow / reject / rewrite / request_reobserve).
   The gates **decide; they never execute**.
4. **Authenticated local-device execution.** Structured observation fusion (AX plus DIB
   by reference), opaque targets with generation counters against TOCTOU races. Every
   action still passes the safety boundary, a capability grant and HITL confirmation.

---

## Structured perception: design and status

Target design: screen capture and screen-pixel scraping are not how this project
perceives. `agent/observation.py` fuses exactly two **structured** senses and **never
silently lets one override the other**. A disagreement is recorded as an
`ObservationConflict`, lowers the fusion confidence, and is escalated to the action
gate, which requests re-observation:

| Sense | What it is | Role |
| --- | --- | --- |
| `ax` | Accessibility tree (macOS AX / Windows UIA): role, name, automation_id | **Primary**: the semantic identity that targeting and gate reasoning rely on |
| `dib` | A bitmap that native `atlas` **reconstructs read-only** from process memory, carried **by reference** (identity and hash, never pixels) | **Verification**: proves that the location the AX tree reports actually renders the expected shape. This is a structured memory read, **not** a screen capture |

The fusion layer (`observation.py`) has no `pixel` or screen-capture input. Large visual
payloads are always **referenced by content hash**; they never enter the Python heap or
the event journal.

**Status:** the fusion in `observation.py` and the generation (TOCTOU) checks in
`target.py` are exercised only by tests and are not yet wired into the live loop
(Track D1). The live loop uses the AX structure image on macOS and still captures the
screen on Windows and Linux (Track D2 moves these to structure-first).

---

## The end-to-end authentication chain

```
DID identity  →  authenticated session  →  signed state  →  Socratic gates  →  physical action (capability + HITL)
identity/        transport/                citadel/          citadel/            agent/ + safety boundary
```

**No authenticated identity, no session. No session, no replication. No plan that
passed the gates, no execution. No capability grant and HITL confirmation, no physical
action.** Every step is verifiable, and all of it is testable headless over loopback.
The full sequence diagram is in [`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## Status inventory

Based on the code as built and pushed:

| Layer | Package / module | Status |
| --- | --- | --- |
| Identity | `identity/` (DIDs, canonical signing, `Allowlist`) and `binding.py` (DID-to-transport-key binding) | ✅ |
| Networking | `transport/` (peer, session, endpoint; direct P2P in `udp.py`; `rendezvous.py`, `upgrade.py`, `membership.py`; `relay.py`, where any allowlisted node can act as a relay) | ✅ |
| State | `citadel/` (`journal.py` signed journal, `state.py` `StateStore`, `sync.py` anti-entropy, `replication.py` convergence over the transport) | ✅ |
| Reasoning | `citadel/socratic.py` (instruction gate), `action_gate.py` (plan gate: intent contract, known-failure memory, capabilities), `authz.py` (Gate 2 operator signature), `supervisor.py` (supervised node). Both gates are wired into the live agent loop | ✅ |
| Memory | `citadel/episodes.py` / `lessons.py` / `consolidate.py`: staged memory, S1 episodic → S2 quarantine → S3 consolidated. Facts require operator-signed confirmation, and memory can only make the gates stricter | ✅ |
| Operator dialogue | `dialogue/` (`secdogie-dialogue`): the operator's Dialogue App. Clarifying questions and answers, a structured (pixel-free) view, the Gate 2 signing console, memory confirmation, and a session layer that tolerates loss and reordering | ✅ |
| Node | `node/` (`secdogie-node`): the resident node, which assembles transport, dialogue, the signed journal and the supervised loop; end-to-end tests over real UDP | ✅ |
| Inference mesh | `inference/`: three-state routing. State 1 (Nominal) routes over four serving nodes to leased model provider nodes. State 2 (Degraded) isolates faulty nodes, routes around them and falls back to the coordinator, and recruits standby forwarders anchored to the key broker. State 3 (Lockdown) acts on self-verifying evidence, rotates keys, cross-checks two independent models, and is released only by an operator-signed reset. Protocol v2 adds an explicit mode state machine, bounded sessions, Merkle-DAG heartbeats (fork isolation, witnessed links, ledger anti-entropy) and a history-aware decision gate | ✅ |
| Perception and actuation | `agent/observation.py` (AX + DIB fusion by reference), `target.py` (TOCTOU), AX and safety layers; `native/atlas` (read-only, DIB reconstruction) | 🔨 Components built; observation and target not yet in the live loop |
| Device and web sessions | `desktop/` (native chat-style window) and `websession.py` (reuses an **already authorized** browser session for read-only navigation and structure reads) | ✅ |
| Transport and operations | `tunnel/` (C encrypted tunnel for confidentiality), `fleet/`, `console/` | ✅ |
| Browser P2P | `webrtc/` (WebRTC DataChannel client and Cloudflare Worker signaling gateway; starts only after a user gesture) | ✅ |
| Browser runtime | `symbiont/` (TypeScript: dual Socratic gates, inline signing prompts, attention scheduling, sandboxed fetch) and `graph/` (Rust-WASM: append-only state graph, have/want sync, lexical route adapter); byte-for-byte compatible with Python, see `fixtures/vectors/` | 🔨 Transport bridge to the Python nodes pending |

**Stage 2 (runnable closed loop) is complete:** k-of-n revocation, zero-trust by default
(see [`docs/ZERO-TRUST-MIGRATION.md`](docs/ZERO-TRUST-MIGRATION.md)), both gates and staged
memory in the live loop, the Dialogue App, `secdogie-node`, and end-to-end tests.

**Stage 3 backlog** (per [`ROADMAP.md`](ROADMAP.md)):
- Mesh runtime: rendezvous over UDP (T3), on-the-wire gossip (T4), durable revocation
  propagation through the journal (T6).
- Device classes and headless isolation (T7); approval paths for fleet, console and desktop.
- C + libsodium tunnel hardening (T9): v2 handshake (Noise IK), rekeying, end-to-end
  relaying, a local control socket.
- Multi-node vertical-slice demonstration, security review, validation on real hardware.
- Perception in the live loop, structure-first (owner-driven).

---

## Safety boundary (prohibited list)

This is a translation of the verbatim list in [`README.zh.md`](README.zh.md), which is binding.

**Never introduce:** process-memory writes, remote thread injection, kernel-level HID,
EDR evasion or anti-detection, covert persistence, privilege escalation, bypassing user
authorization or macOS Accessibility / Screen Recording permissions, changing HITL to
auto-approve by default, covert embedding of third-party services, traffic obfuscation,
or NAT-traversal techniques used for detection evasion.

**Always keep:** memory = read-only; execution = supervised; high-risk = fail-closed and
requiring human confirmation in every mode and at every entry point (no switch disables
this); physical action = explicit capability. The capability model **never** includes
`process.memory.write`, kernel HID, anti-detection or privilege escalation.

`websession.py` only **reuses an authorized session that you saved after logging in
normally elsewhere**, for read-only navigation and reading page structure. It **does not
enter credentials, create or steal sessions, or bypass authentication, CAPTCHAs or
anti-bot controls**; if a site refuses automation, that is respected. Before each slice is
committed, a grep-based regression check confirms none of these primitives is present.
The full trust model is in [`SECURITY.md`](SECURITY.md).

---

## Quick start (headless)

Every package is testable as pure logic or over loopback, with no desktop and no display:

```sh
pip install ruff
ruff check .                                   # repository lint (ruff.toml)

python -m pytest identity/tests  -q            # DIDs, signing, bindings
python -m pytest transport/tests -q            # sessions, UDP, rendezvous, upgrade, membership
python -m pytest citadel/tests   -q            # journal, state, anti-entropy, replication, gates, supervision
cd agent && python -m pytest tests/test_observation.py tests/test_target.py -q   # structured perception, targets
```

CI ([`.github/workflows/test.yml`](.github/workflows/test.yml)) runs headless `pytest` for
every package, `ctest` for the C tunnel, and `ruff` over all Python.

---

## The former screen-capture tools (removed)

The repository's earlier standalone tools built on a vision LLM and screen capture
(`android/`, `ios/`, `scene3d/`, the multi-window `open/`, and the game-automation stack
`aim/`, `commander/`, `handoff/`, `gta/`) **have been removed from the main line**: the
project's perception is structured and does not need screen capture. They remain
available in git history. `agent/` stays as the shared engine; the agent's structured
perception stack lives there.

---

## Further reading

- [`ARCHITECTURE.md`](ARCHITECTURE.md): the four pillars, the end-to-end authentication chain, the component map, the safety boundary, the roadmap.
- [`ROADMAP.md`](ROADMAP.md): overall implementation goal and milestones.
- [`docs/AUDIT-P2P-ALIGNMENT.md`](docs/AUDIT-P2P-ALIGNMENT.md): source-level audit and conflict record.
- [`docs/GLOSSARY.md`](docs/GLOSSARY.md): canonical terminology, English and Chinese.
- [`docs/NODES.md`](docs/NODES.md): how the inference mesh obtains nodes (operator machines,
  automatic discovery, admitted browser forwarders) and what is excluded.
- [`SECURITY.md`](SECURITY.md): trust model and vulnerability reporting.
