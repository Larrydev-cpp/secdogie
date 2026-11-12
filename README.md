# secdogie · a cloud lifeform

**English** · [中文](README.zh-CN.md)

A **decentralized, DID-authenticated, supervised** autonomous agent (a "cloud
lifeform") that runs **only on nodes you own or have authorized**. There is no
central server: it lives on by peer-to-peer. It learns from **public or
authorized** sources, examines every step of intent with **two layers of
Socratic gates**, and takes real-world action **only on an authenticated local
device, under a capability grant and with a human in the loop (HITL)**.

**Perception is structural first.** The goal is to "see" through the
accessibility tree (AX / UIA), read-only structured DIB references, and the
AX / text of authorized web sessions, never through screen pixels.

**Where that stands today:**
- On macOS the live loop renders the AX tree as a structure graph for the model.
- On Windows and Linux the live loop **still takes screenshots**.
- Moving those to structure-first, with screenshots only when the owner
  explicitly turns them on, is Track D in [`ROADMAP.md`](ROADMAP.md).

> Full architecture: [`ARCHITECTURE.zh.md`](ARCHITECTURE.zh.md). Roadmap: [`ROADMAP.md`](ROADMAP.md).
> Source audit and spec alignment: [`docs/AUDIT-P2P-ALIGNMENT.zh.md`](docs/AUDIT-P2P-ALIGNMENT.zh.md).

---

## Download

Every release on the [Releases page](https://github.com/Larrydev-cpp/secdogie/releases) carries ready-to-run builds:

**Studio** is the desktop app: a window of cards in rice paper by day and ink
by night. It shows your companion, what waits on you, the 时辰 of your day and
your own devices, and you pair a new device from inside it.

| Package | What it is |
| --- | --- |
| `secdogie-studio-windows-x86_64-<version>-setup.exe` | Studio installer for Windows. It installs for you only (no administrator), adds a Start menu entry, and starts nothing at login. |
| `secdogie-studio-macos-<arch>-<version>.dmg` | Studio for macOS (`arm64` or `x86_64`): drag **secdogie Studio** to Applications. |
| `secdogie-studio-linux-x86_64-<version>.AppImage` | Studio for Linux: `chmod +x` it and run it. |
| `secdogie-studio-<platform>-<version>-portable.zip` | The same app unpacked, with nothing installed. |
| `secdogie-agent-<platform>-<version>` | The agent as a single executable. |
| `secdogie-atlas-<platform>-<version>.zip` | Native read-only structure tools (`atlas_inspect`, `atlas_mct`, `atlas_target`). |
| `secdogie-tunnel-linux-x86_64-<version>` | The encrypted C tunnel. |

Every Studio build checks itself: run it with `--self-test` and it walks through
a throwaway profile, a node, a link and a WebRTC loopback, then prints the result.

Studio builds are not code-signed yet:
- on macOS, right-click the app and choose **Open** the first time;
- on Windows, choose **More info → Run anyway** in SmartScreen.

---

## Four pillars

1. **A decentralized network.**
   - Identity is a DID (Ed25519 `did:key`); the session and endpoint layers are abstractions over real P2P UDP.
   - Peers find each other through authenticated rendezvous, upgrade to a direct path or fall back to a relay, and converge membership by gossip anti-entropy.
   - A hub only bootstraps and relays: if it goes down, the mesh lives on.
2. **P2P, distributed learning.**
   - A signed, hash-chained, append-only event journal with a `StateStore` on top.
   - Journal and state converge across the mesh by anti-entropy with **two layers of authentication**, so signed "learned evidence" written on one node spreads to the other authorized nodes.
3. **A Socratic philosophy system.** Two layers of gates:
   - **instruction level** (examining intent);
   - **action-plan level** (`GateDecision`: allow / reject / rewrite / request_reobserve).

   The gates **only judge; they never execute**.
4. **Action on authenticated local devices.**
   - Structured observation fusion (AX + DIB by reference).
   - Opaque targets with generations to close TOCTOU gaps.
   - Every action still passes the safety boundary, a capability grant and a human in the loop.

---

## Your own devices, in Studio and in the browser

The mesh runs **only on devices you own**. There are no public nodes and no
seed nodes. Adding a device happens in Studio's **Devices** page or in the
browser; the two speak the same protocol, so a phone's browser and the Studio
on your desk pair with each other directly.

1. **Pair.** Open [`webrtc/pair.html`](webrtc/pair.html) on a device already in your mesh and write an invitation. Open the link on the new device and click **Agree**.
   - The two devices connect directly over a WebRTC data channel.
   - Each signs the DTLS certificate fingerprints it sees, so anyone in the middle of the signaling is caught.
   - Both screens show the same six digits. You confirm on the inviting device, which signs an admission for the new one.
2. **Go online.** Open [`webrtc/mesh.html`](webrtc/mesh.html) on each device and click **Go online**.
   - Every device links directly to every other online device.
   - Both ends of each link sign a `link-hello` before anything else is read.
   - A newly paired phone learns the rest of the mesh from the owner's roster.
   - Close the page and that device goes offline: nothing runs in the background.

The signaling server only helps two devices find each other; it never sees what
they say. Design and next steps: [`docs/PLAN-OWN-DEVICES.zh.md`](docs/PLAN-OWN-DEVICES.zh.md)
and [`webrtc/README.md`](webrtc/README.md).

---

## Perception is structural (design and status)

The target design: screenshots and screen-pixel capture are not how this project
perceives. `agent/observation.py` fuses exactly two **structured** senses.
It **never silently lets one override the other**. A disagreement:
- is recorded as an `ObservationConflict`;
- lowers the fused confidence;
- is handed to the action gate, which asks for a re-observation.

| Sense | What it is | Role |
| --- | --- | --- |
| `ax` | The accessibility tree (macOS AX / Windows UIA): role / name / automation_id | **Primary**: the semantic identity that targets and gate reasoning rely on |
| `dib` | A bitmap that native `atlas` **rebuilds read-only** from process memory, carried **by reference** (identity and hash, never pixels) | **Verification**: proves that what AX says is at a position really drew the right shape. This is a structured memory read, **not** a screenshot |

There is no `pixel` or screen-capture path in the fusion layer
(`observation.py`). Large visual data is always **referenced by content hash**;
it never enters the Python heap or the event journal.

**Status:**
- Fusion in `observation.py` and the generation (TOCTOU) checks in `target.py` are so far used only in tests; they are not yet wired into the live loop (Track D1).
- The live loop uses the AX structure graph on macOS and still takes screenshots on Windows and Linux (Track D2 moves them to structure-first, with screenshots only when explicitly enabled).

---

## One authenticated chain, end to end

```
DID identity  →  authenticated session  →  signed state  →  Socratic gates  →  (capability + HITL) real action
identity/        transport/                citadel/          citadel/            agent/ + safety boundary
```

**No authenticated identity, no session. No session, no replication. No plan
through the gates, no execution. No capability grant and human in the loop, no
real-world action.** Every step is verifiable, and all of it is testable
headless over loopback. The full sequence diagram is in
[`ARCHITECTURE.zh.md`](ARCHITECTURE.zh.md).

---

## Status (an honest inventory)

What is built and pushed, judged by the real code:

| Layer | Package / module | Status |
| --- | --- | --- |
| Identity | `identity/` (DID, canonical signing, Allowlist) + `binding.py` (DID ↔ transport key) | ✅ |
| Network | `transport/`: peer/session/endpoint, real P2P over `udp.py`, `rendezvous.py`, `upgrade.py`, `membership.py`, and `relay.py` (any allowlisted node can relay) | ✅ |
| State | `citadel/`: `journal.py` signed journal, `state.py` StateStore, `sync.py` anti-entropy, `replication.py` convergence over transport | ✅ |
| Mind | `citadel/socratic.py` (instruction gate) + `action_gate.py` (plan gate: intent contract, known failures, capabilities) + `authz.py` (Gate 2 operator signature) + `supervisor.py` (supervised node); both gates are in the live agent loop. An automatic Socratic gate (`deliberate.py`) weighs every new goal against what it knows of you | ✅ |
| Memory | `citadel/episodes.py` / `lessons.py` / `consolidate.py`: staged memory, from S1 episodes to S2 quarantine to S3 consolidation; facts need an operator-signed confirmation and can only make the gates stricter | ✅ |
| Dialogue | `dialogue/` (`secdogie-dialogue`): the operator's Dialogue App. It covers follow-up questions and answers, a structured view (no pixels), the Gate 2 signing desk and memory confirmation, over a session layer that survives loss and reordering | ✅ |
| Node | `node/` (`secdogie-node`): the resident node, assembling transport, dialogue, the signed journal and the supervised loop; real end-to-end UDP tests | ✅ |
| Policy | `policy/` (`secdogie-policy`): signed, declarative bundles for gate rules, decision models, ingestion limits and allowlists, which can only tighten. A TS mirror is locked to it by golden vectors. Bundles are injected locally into a node and take effect at once (`node/policy_host.py`); spreading them across the mesh is still to do | 🔨 |
| Companion | `studio/` (`secdogie-studio`): the desktop app. A floating companion stays faded until it needs you, then shows a single card (question / signature / memory). A home window of cards in rice paper / ink holds the conversation, your devices (pairing included), the rules and an introduction. Installers for Windows, macOS and Linux; `citadel/decision.py` decides when to reach you, and attention reads only the system idle time | 🔨 |
| Inference mesh | `inference/`, which routes in three modes:<br>· **normal:** over the mesh to leased model nodes;<br>· **a node fails:** detour, quarantine and a central fallback;<br>· **a node turns against you:** lockdown, with self-proving evidence, key rotation, cross-checks by two models, and release only by an operator signature | ✅ |
| Perception / action | `agent/observation.py` (AX + DIB fused by reference) + `target.py` (TOCTOU) + AX/safety; `native/atlas` (read-only DIB reconstruction) | 🔨 building blocks done; observation/target not yet in the live loop |
| Devices / sessions | `desktop/` (a chat-style native window, plus `websession.py`, which reuses **authorized** browser sessions for read-only navigation and reading structure) | ✅ |
| Carrier / ops | `tunnel/` (encrypted C tunnel, confidentiality), `fleet/`, `console/` | ✅ |
| Browser P2P | `webrtc/` (WebRTC data-channel client + Cloudflare Worker signaling gateway; starts only after a click) | ✅ |
| Your own devices | The mesh runs only on your own devices:<br>· `webrtc/pair.html` pairs a device in the browser (invite → agree → direct WebRTC → six digits on both screens → admission);<br>· on `webrtc/mesh.html`, devices that go online form a full WebRTC mesh: both ends of each link sign a `link-hello`, and rosters spread by themselves;<br>· reference implementations are `transport/pairing.py` and `meshlink.py`;<br>· the address book keeps only your own devices by default (the public tier is shelved) | 🔨 revocation and Python nodes on WebRTC still to do |
| Browser runtime | `symbiont/` (TS: two Socratic gates, inline signing prompts, attention scheduling, sandboxed fetch) + `graph/` (Rust-WASM: append-only state graph, have/want, lexical routing adapter); byte-compatible with Python, see `fixtures/vectors/` | 🔨 transport bridge to Python nodes still to do |

**Phase two (a runnable closed loop) is done**, including:
- k-of-n revocation;
- zero-trust defaults (see [`docs/ZERO-TRUST-MIGRATION.md`](docs/ZERO-TRUST-MIGRATION.md));
- both gates and staged memory in the live loop;
- the Dialogue App;
- `secdogie-node` with end-to-end tests.

**Phase three, still to do** (per [`ROADMAP.md`](ROADMAP.md)):
- mesh runtime: UDP over rendezvous (T3), live gossip (T4), durable revocation spread through the journal (T6);
- device classes and headless isolation (T7);
- approval paths for fleet / console / desktop;
- C + libsodium tunnel hardening (T9): v2 handshake (Noise IK), rekeying, end-to-end relaying, a local control socket;
- a multi-node vertical demo, a security re-review, verification on real machines;
- perception in the loop, structure first (driven by the owner).

---

## Safety boundary (forbidden list)

> The authoritative wording is the Chinese list in [`README.zh-CN.md`](README.zh-CN.md), kept verbatim. This is a faithful English rendering.

**Never** introduce:
- process memory writes or remote thread injection;
- kernel HID;
- EDR or anti-detection;
- covert persistence or privilege escalation;
- bypassing user authorization or the macOS Accessibility / Screen Recording permissions;
- turning HITL into default auto-approval;
- covertly embedding third-party services;
- traffic obfuscation, or hole-punching used as anti-detection.

**Keep:**
- memory = read-only;
- execution = supervised;
- high-risk = fail-closed, and **always** confirmed by a human, in every mode and at every entry point (there is no switch to turn it off);
- physical action = an explicit capability.

The capability model **never** contains `process.memory.write`, kernel HID,
anti-detection or privilege escalation.

`websession.py` only **reuses authorized sessions that you saved after logging
in properly elsewhere**, for read-only navigation and reading page structure.
- It **never** types credentials, creates or steals sessions, or bypasses authentication, CAPTCHAs or anti-bot measures.
- If a site refuses automation, that is respected.

Before every slice is committed, a grep regression confirms none of these
primitives appear. The full trust model is in [`SECURITY.md`](SECURITY.md).

---

## Quick start (headless, testable)

Every package is pure logic or loopback-testable; no desktop or screen needed:

```sh
pip install ruff
ruff check .                                   # root lint (ruff.toml)

python -m pytest identity/tests  -q            # DID, signing, binding
python -m pytest transport/tests -q            # sessions, UDP, rendezvous, upgrade, membership, pairing, mesh links
python -m pytest citadel/tests   -q            # journal, state, anti-entropy, replication, gates, supervision
cd agent && python -m pytest tests/test_observation.py tests/test_target.py -q   # structured perception, targets
cd webrtc && npm test && npm run e2e           # browser pairing and mesh (e2e needs Playwright + Chromium)
```

CI ([`.github/workflows/test.yml`](.github/workflows/test.yml)) runs:
- headless `pytest` for every package;
- `ctest` for the C tunnel;
- `ruff` over all Python;
- the browser end-to-end tests over real WebRTC.

---

## About the old screenshot tools (removed)

The earlier standalone tools in this repository, built on vision LLMs and
screenshots, **have been removed from the main line**. That covers `android/`,
`ios/`, `scene3d/`, the multi-window `open/`, and the game stack
`aim/` / `commander/` / `handoff/` / `gta/`. The project perceives structurally
and does not need screenshots. They remain in git history for reference.
`agent/` stays as the shared engine; the cloud lifeform's structured perception
stack lives there.

---

## Further reading

- [`ARCHITECTURE.zh.md`](ARCHITECTURE.zh.md): the four pillars, the authenticated chain, the component table, the safety boundary, the roadmap.
- [`ROADMAP.md`](ROADMAP.md): implementation goals and milestones.
- [`docs/PLAN-OWN-DEVICES.zh.md`](docs/PLAN-OWN-DEVICES.zh.md): the mesh of your own devices over WebRTC.
- [`docs/AUDIT-P2P-ALIGNMENT.zh.md`](docs/AUDIT-P2P-ALIGNMENT.zh.md): source audit and conflict log.
- [`SECURITY.md`](SECURITY.md): trust model and vulnerability reporting.
