# secdogie: source-level audit and P2P / autonomy specification alignment

Chinese: [AUDIT-P2P-ALIGNMENT.zh.md](AUDIT-P2P-ALIGNMENT.zh.md)

> Audit date: 2026-09-19 · Basis: repository source code (not README claims) · Coverage:
> all packages
>
> This is an English translation of a historical record. It describes the repository as it
> stood on the audit date; later changes appear only in the status update inside section 3.
>
> This document treats **the source code as the sole source of truth**. It compares the
> actual implementation, item by item, against a target specification (an autonomous P2P
> "digital organism"), and reports the real defects found and a phased roadmap.

## Context

Audit goal: compare `secdogie`'s actual implementation with a target specification. That
specification envisions an autonomous "digital organism" ("digital soul") with a DID and a
cryptocurrency wallet (@Larryx), a 24/7 state machine based on CRDTs / DAGs running on a
roaming P2P mesh (Citadel + `secdogie-tunnel`), and an attach-on-demand physical "body"
(`secdogie-daemon`) performing process-RAM pointer mapping and kernel-level virtual HID
injection.

**Method.** Every package's source was read directly, supplemented by verification greps and
file reads; no README was treated as authoritative.

**A correction that changes the overall picture.** The specification assumes that
`README.md` is "badly out of date and disconnected from the code". It is not: the top-level
`README.md`, `SECURITY.md` and `ROADMAP.md` **are consistent with the code**. They describe a
supervised, owner-operated computer-control tool whose primary perception is the
accessibility tree, and the code matches them closely. The real disconnect is **between the
target specification** (an autonomous P2P digital life form, wallet-funded metabolism,
kernel HID) **and the code**. The "gaps" reported here are therefore specification vs.
reality, not documentation vs. reality.

**Nature of the deliverable.** An audit, a gap analysis and a roadmap. Part of the
specification is conventional distributed-systems engineering (implementable); several
parts are things the codebase **deliberately refuses** to do (anti-detection HID,
memory-writing cheats, weakening human supervision), and those refusals should be kept. The
split is in Part B of the roadmap.

---

## 1. What secdogie actually is (verified)

A **single-owner, human-supervised, LLM-driven computer-control tool whose primary
perception is the accessibility tree**; pixel screenshots are the **fallback**, not the
default. A single agent process takes one natural-language task, reads the local UI (see
§1.4, layered perception), asks an LLM for the next action, and **executes one action at a
time** (click / type / scroll / …) under a per-step confirmation gate, until the task is done
or the step budget is exhausted. The rest of the repository is either peripherals that reuse
this agent loop (mobile, multi-window, multi-desktop, 3D scene analysis) or channels for
reaching the machine (the tunnel).

It is **not** a daemon, not autonomous, does no peer-to-peer networking, has no identity /
wallet / signatures, has no cost accounting, and has no reasoning architecture beyond a
one-shot linear sub-task list.

### 1.1 Packages and their actual responsibilities

| Package | Language | What it actually is |
|---|---|---|
| `agent/` (~9.5k lines) | Python | The core. A bounded step loop (`loop.py`); **accessibility-tree-first** targeting and execution (UIA / AT-SPI / macOS AX via `--desktop-ax`, through `desktop_ax.py` + `harness.py`): actions go through accessibility APIs (Invoke / AXPress / SetValue), without moving the real mouse, stealing focus or sending screenshots to the model; two LLM providers (Anthropic, OpenAI / OpenRouter); mss pixel screenshots only as a fallback; DPI and focus correction, macro recording / replay, a hash-chained audit trace. The only CLI: `secdogie-agent`. |
| `native/atlas/` (~6k lines of C++) | C++ | **Deep perception:** read-only process-memory inspection that extracts strings / JSON / PE headers and **reconstructs DIB bitmaps from heap memory as RGBA previews** (`memory_inspector.cpp` parses BITMAPINFOHEADER; `inspect_json.cpp` places them base64-encoded in `dibs[]`), i.e. "constructing images by reading the process" with no screen capture. Plus a user-mode execution twin. Windows / macOS / Linux. Delivered as standalone owner-run binaries (`atlas_mct`, `atlas_inspect`); a loopback-only, unauthenticated HTTP / line-protocol service listening on `127.0.0.1:17890`. |
| `tunnel/` (~1.5k lines of C) | C | A UDP VPN implemented from scratch on libsodium (X25519 + BLAKE2b + XChaCha20-Poly1305). Point-to-point by default, plus optional decrypting hub-and-spoke routing. A real TUN device carrying the raw IP data plane. |
| `fleet/` (~1k lines) | Python | A star-topology task dispatcher: one in-memory TCP coordinator, N worker nodes dialing out, a newline-delimited JSON protocol. No authentication, no persistence, no replication. |
| `commander/`, `handoff/` | Python | Single-machine, two-process "baton" coordination of input ownership for a game-automation scenario (Minecraft). `handoff` = an OS file-lock arbiter; `commander` = a fixed-priority tactician. |
| `aim/`, `gta/` | Python | Game automation: a YOLO-driven aiming controller (relative mouse look via SendInput / `/dev/uinput`) and a GTA V driving controller (TCP to an external ScriptHookV plugin **not in this repository**). |
| `scene3d/`, `open/`, `android/`, `ios/` | Python | Peripherals reusing the agent loop: multi-view 3D scene fan-out; a multi-window browser UI; Android via `adb`; iOS via WebDriverAgent. |

### 1.2 Active entry points / "APIs"

- **`secdogie-agent`** (`agent/secdogie_agent/cli.py:main`): the only real CLI; one positional
  task argument plus flags, no subcommands.
- Per-package CLIs: `secdogie-fleet`, `secdogie-commander`, `secdogie-aim`, `secdogie-gta`,
  `secdogie-scene3d` / `-perceive`, `secdogie-open`, `secdogie-android`, `secdogie-ios`.
- **Atlas MCT service:** loopback-only HTTP: `GET /health|/status|/list`,
  `POST /cmd|/inspect`, listening on `127.0.0.1:17890`, unauthenticated (the boundary is
  loopback + Cloudflare Access).
- **Fleet TCP:** 5 message types (`hello/status/result` up, `assign/stop` down), listening on
  `0.0.0.0:47810`, unauthenticated.
- **Tunnel:** `secdogie-tunnel {genkey,server,client,hub}`, UDP.
- Outbound HTTPS to LLM APIs (Anthropic / OpenAI / OpenRouter), with an optional HTTP / SOCKS5
  proxy.

### 1.3 Data flow (the agent core)

`for step in range(1, max_steps+1)` (default **50**): check for stop → optional watch sleep →
present → **read the AX snapshot (primary under `--desktop-ax`)**;
`harness.should_omit_screenshot()` decides whether this step can omit pixels, and if so **no
screenshot is sent to the model** and the AX element list is the current UI
(`loop.py:396`); otherwise fall back to an mss screen capture → assemble the prompt
(history[-10], plan notes, memory) → `provider.next_action()` (with backoff retries) →
coordinate scaling → confirmation gate (y/N; high-risk actions require confirmation even with
`--auto`) → **execute (accessibility APIs first: Invoke / AXPress / SetValue; pixel clicks as
the fallback)** → optional pixel-diff verification + retry → append to the bounded history →
next step. If the model gives pixel coordinates on an AX-only step, the action is refused and
the model is told to use a listed ref or `look` (`loop.py:481-503`). Exits with process codes
0–7. **No `while True`, no restart, no scheduler.** Persistence across runs is entirely
optional: `--memory` (SQLite key-value), `--trace` (hash-chained JSONL, cleared each run),
`--macro` (JSON), a configuration file.

### 1.4 Layered perception (key point)

secdogie's perception is not "screenshot first" but **layered**, with pixels as the last
layer:

1. **Primary: the accessibility tree.** UIA (Windows) / AT-SPI (Linux) / macOS AX, via
   `--desktop-ax` (the GUI launch card `task` defaults to `--gui --desktop-ax`). It lists the
   interactive elements, actions go through accessibility APIs, and **no screenshot is sent to
   the model for that step** (fewer tokens, no mouse movement, no focus stealing).
2. **Deep: read-only process memory.** `native/atlas` opens a read-only handle, walks the
   committed pages, extracts strings / JSON / PE headers, and **reconstructs DIB bitmaps from
   heap memory** (BITMAPINFOHEADER → top-down RGBA, base64 into `dibs[]`), i.e. **constructs
   images by reading the process**, with no screen capture at all. It is still read-only
   perception: no write primitives, no pointer chains, no AOB scanning; PPL / lsass / csrss and
   higher-integrity targets are refused.
3. **Fallback: pixel screenshots + vision.** Only when the AX tree is empty (CAD canvases,
   games, owner-drawn controls) or the model explicitly asks to `look` does the old path of
   mss screenshots + a vision model run. The documentation's own words: "pixels remain the
   fallback, not the default" (`harness.py`).

**Wiring status (stated plainly; also a gap):** in the active Python `agent` loop, layer 1
(AX first) and layer 3 (pixel fallback) are wired; layer 2 (memory reading / DIB image
reconstruction) currently lives in the standalone `native/atlas` binaries (`atlas_mct` /
`atlas_inspect`, invoked manually by the owner per the documentation) and **is not yet
bridged into the Python loop**. `agent/secdogie_agent/atlas.py` is its pure-Python twin but is
dead code at runtime (used only by tests). The capability of "constructing images by reading
the process" is therefore **implemented in the native layer but not fused with the agent's
main loop** (see roadmap Phase 6).

---

## 2. Gap report: specification (a three-part P2P life form) vs. reality

| Specification pillar | Claim | What the code actually has | Conclusion |
|---|---|---|---|
| **Digital soul**: autonomous will, a right to refuse domestication, an internal Socratic dialectic loop | A 24/7 autonomous agent that can refuse instructions and argue with itself | A bounded, one-shot, supervised loop (`loop.py`, at most 50 steps). No introspection / critic / debate / replanning. Per-action `reasoning` is logged and never used. "Refusal" is deterministic code gating (`--read-only`, allowlists, platform walls) plus one prompt line that has the model escalate to `ask_user`. There is no `refuse` / `abort` action; the strongest move the model has is `ask_user` → a human decides. | **Absent.** No autonomy, no will, no dialectic. |
| **Identity and wallet @Larryx**: DID + crypto wallet; signing instructions and nodes; micropayments funding compute / APIs | Signed identity + on-chain metabolism | **None.** No DID, wallet, signature, payment, micropayment, ledger or balance (grep: 0 hits each). No token or cost accounting at all: `model_registry.py` parses OpenRouter pricing, but nothing consumes it. API keys are plaintext env vars / files. The tunnel keys are **X25519 key agreement**, not signatures. `trace.py` is a hash chain and explicitly states it is "not a signature". | **Absent.** |
| **Citadel + roaming P2P mesh**: CRDT event stream + dynamic DAG goal tree; a 24/7 state machine; `secdogie-tunnel` doing P2P hole punching, roaming and state sync | Self-healing mesh, CRDT state, DAG goals | No CRDT / vector clocks / replication (0 hits). `Plan` is a **flat list with a cursor**, built once and never revised; over-budget sub-tasks are skipped rather than replanned. No 24/7 daemon (on-demand CLI only). The tunnel is **hub-and-spoke / client-server** with a static public-key allowlist and **no discovery / gossip / DHT / hole punching / STUN / IPv6**. "Roaming" = adopting a NAT-rebound address by an 8-byte session id (`hub.c:236`). Client↔client traffic is **decrypted and re-encrypted** at the hub (not end-to-end, not a mesh, as `PROTOCOL.md:173` states). The production path is explicitly `cloudflared`, **not** the C tunnel. Fleet is a star dispatcher: in-memory, unauthenticated TCP, a single point of failure. | **Absent / opposite** (a star, not a mesh). |
| **Physical probe secdogie-daemon**: an attach-on-demand body; process-RAM pointer mapping; kernel virtual HID with humanized injection | A kernel-level memory + HID "body" | **Read-only memory perception is implemented:** `ReadProcessMemory` / `process_vm_readv` / `mach_vm_read_overwrite`, extracting strings / JSON / PE and **reconstructing DIB images from heap memory** (see §1.4). "Constructing images by reading the process" exists, but in the standalone `native/atlas` binaries, **not yet bridged into the agent loop**. **The refused / absent half is "pointer mapping + kernel HID":** every write primitive is refused (no `WriteProcessMemory`, `VirtualProtectEx`, `CreateRemoteThread`, `ptrace`); **no pointer chains / base+offset / AOB scanning**; input is **user-mode** `SendInput` (Windows) / `AXPress` (macOS) / none (Linux); **no kernel driver of any kind** (no `.sys` / `.inf`, `DriverEntry` / KMDF / IOKit); no `secdogie-daemon` service (`atlas_mct` is an owner-started foreground loopback process); "humanization" is only timing jitter on mobile, labeled "not a fingerprint bypass". | **Partially implemented** (read-only perception + memory image reconstruction); **pointer writes / kernel HID deliberately refused.** |

**Bottom line:** none of the specification's distributed-systems vocabulary (DID, mesh, CRDT,
DAG, wallet) is backed by an implementation. Perception is the opposite: **accessibility tree
first + read-only process memory + DIB image reconstruction from memory** is real and has
considerable engineering depth (it is secdogie's actual strength, not "a screenshot tool"),
but the deep memory perception is not yet bridged into the agent's main loop. What the
codebase **deliberately seals off** is only the cheating / evasion subset of the "hard"
capabilities: **memory pointer-write mapping** and **kernel-level humanized HID**.

---

## 3. Real defects found by the audit (worth fixing in any direction)

1. **`tunnel/PROTOCOL.md` is out of sync with `tunnel/*.c`** (the rare case where the
   documentation *is* wrong): `mac1` is a keyed BLAKE2b-128 (`crypto.c:29`), not
   `crypto_auth`; roaming and multiple peers are implemented yet still listed as non-goals;
   the static public-key equality check (`handshake.c:56`), the real authentication gate, is
   not listed; it references a nonexistent `wire.c`.
2. **The tunnel hub does not validate the inner source IP** (`hub.c:241-253`): an
   authenticated client can forge any source IP toward another client (the gap
   WireGuard-style cryptokey routing closes). It also `write()`s unparseable plaintext straight
   into the TUN (`hub.c:242-247`) and generally ignores `write()` / `sendto()` return values.
3. **Fleet has no authentication / authorization / encryption on `0.0.0.0:47810`:** any host
   that can reach the port can register as a node or hijack an existing `node_id`
   (`server.py:197-201`). The only real defense is the node-side `ALLOWED_OPTIONS`.
4. **`RunAllowlistedAsSystem` (Windows SYSTEM launch) is compiled but has zero callers**
   (`privilege_manager.cpp`): a real, gated capability sitting idle with no CLI or bridge
   wiring. A decision is needed on whether it should exist.
5. **`agent/secdogie_agent/atlas.py` (563 lines) is dead code at runtime:** the pure-Python
   twin of `native/atlas`, used only by tests. Fine as a CI mirror, but worth labeling so no
   one assumes it is on the active path.

> **Status update (2026-09)**
> 1. ✅ `PROTOCOL.md` aligned with the code.
> 2. ✅ The hub validates the inner source IP and drops non-IPv4; `write()` / `sendto()` return
>    values are still unchecked (Tunnel T2.0b). Newly found: the v1 handshake_init does not
>    prove possession of the initiator's private key and can be forged to replace a session;
>    contained with "switch only after confirmation" (`ffe49c8`, see `tunnel/PROTOCOL.md`), to
>    be fixed at the root by the v2 handshake.
> 3. 🔨 Fleet half-configuration (identity only or allowlist only) now refuses to start, and
>    unauthenticated operation requires an explicit `--insecure-dev` (`6f43f51`); traffic is
>    still unencrypted on the wire and is carried over the tunnel / WireGuard.
> 4. ✅ `RunAllowlistedAsSystem` and its dedicated helpers were deleted (zero callers).
> 5. ✅ `atlas.py` is labeled "test / CI mirror only" in its file header.

---

## 4. Roadmap

The specification mixes legitimate engineering with several items that should not be built.
Below is the split, with an honest version of each item.

### Part A: what can advance toward the specification (phased, supervised)

**Phase 0: documentation corrections and the defects found (small changes, high value).**
Fix the `PROTOCOL.md` divergence (§3.1); add inner-source-IP (cryptokey) routing and stop
writing unparseable plaintext into the TUN (§3.2); decide on the idle SYSTEM path (§3.4);
label the dead `atlas.py` twin. Verified by the existing tunnel `ctest` + fuzzing and the
Python test suites.

**Phase 1: real identity (the safe half of @Larryx).** Add an **Ed25519 signing identity**
and an optional DID document, and use it to **sign and verify** fleet instructions and
tunnel / atlas peer configuration. Reuse: the tunnel can already generate and load key pairs
(`main.c genkey`, `config.c`), and `trace.py` already anchors a head hash explicitly "intended
for external signing"; connect real signatures to that anchor. New: a small `identity`
module; a signature field in `fleet/secdogie_fleet/protocol.py`, verified in `server.py`.
This delivers "identity + signed instructions / nodes" **without introducing a wallet**.

**Phase 2: cost accounting and budget caps (the honest half of "metabolism").** Add real
per-call token and cost accounting (consuming the OpenRouter `pricing` that
`model_registry.py` already parses), write each run's cost to the trace, and add an optional
prepaid spending cap that refuses work over budget. This is the accountable version of
"self-funding": metering and caps, not autonomous spending. New: a `metering` module, hooked
into the call sites in `providers/base.py`.

**Phase 3: reachability / mesh, done responsibly.** For real P2P / roaming, prefer mature,
reviewed stacks over forcing NAT traversal into the unaudited C tunnel: **WireGuard** for the
VPN case, **libp2p** if true peer discovery / mesh is needed. Keep the C tunnel for air-gapped
labs (its stated purpose). Merge with the Phase 0 hardening.

**Phase 4: durable state and a goal model (the safe half of Citadel).** The coordinator is
in-memory only and a single point of failure. Add an append-only journal (extending the
existing `memory.py` SQLite) and coordinator crash recovery. Replace the flat `Plan` with a
**DAG goal model with revisable sub-goals**. Note: **no CRDT is needed yet**; fleet is a
single writer under a single lock, so a durable single-writer journal is the right tool;
introduce CRDTs only when genuine multi-writer replication is needed.

**Phase 5: a real reasoning loop (the safe reading of "Socratic dialectic").** Add an
optional self-critique / verification pass and replanning to the agent loop (today the plan is
one-shot and per-action `reasoning` is unused), kept within the existing human-confirmation
gates. This improves capability and auditability without weakening the owner's control.

**Phase 6: fuse deep perception (bring the existing memory reading / DIB reconstruction into
the main loop).** `native/atlas`'s read-only memory perception and DIB image reconstruction
are implemented but live only in standalone binaries; the Python-side `atlas.py` twin is dead
code. Add a bridge to the agent loop (a subprocess calling `atlas_inspect`, or loopback
`POST /inspect`) so that when the AX tree is empty and the target is an owner-drawn control
or CAD canvas, §1.4 layer 2 (memory text + reconstructed images) replaces blind screenshots.
Reuse: the `dibs[]` / `strings[]` output contract of `inspect_json.cpp` is ready, and
`atlas.py` already mirrors its read-only refusal policy. This puts secdogie's real perception
strength on the main path without touching any write primitive.

### Part B: what should not be built, and why (one sentence each)

These are exactly the parts the codebase already refuses. Keep these walls.

- **Kernel-level "humanized" virtual HID injection to defeat anti-cheat / detection.** That is
  detection evasion for automation / cheating; the codebase deliberately uses user-mode input
  and labels its jitter "not a fingerprint bypass". Safe alternative: keep user-mode execution
  and the accessibility-tree path (Phase 5 strengthens it openly).
- **Process-memory pointer chains / AOB *writes* (memory-cheat scaffolding).** The codebase is
  read-only perception and refuses every write primitive. Safe alternative: keep read-only
  inspection for legitimate targeting.
- **Weakening human supervision so the agent runs unattended, funds itself through
  cryptocurrency, and can override its owner.** Safe alternative: everything in Part A makes
  the agent *stronger and more accountable* under supervision (signed actions, cost caps,
  durable audit), never less accountable to its owner.

---

## 5. Verification (how to check every claim above)

- **Tunnel cryptography and memory safety:**
  `cmake -S tunnel -B tunnel/build && cmake --build tunnel/build && ctest --test-dir tunnel/build`;
  sanitizers + fuzzing: `cmake -S tunnel -B tunnel/build-asan -DSDTP_SANITIZE=ON && cmake --build tunnel/build-asan && ctest --test-dir tunnel/build-asan`.
- **Python test suites:** for each package, `pip install -e '<pkg>[extras]' && pytest`, plus
  `ruff check .` (matching `.github/workflows/test.yml`).
- **Native atlas:** build `native/atlas` with CMake and run its `ctest`
  (`test_memory_inspector`, `test_atlas`, `test_mct`, `test_chain`).
- **Reproduce the "concept absent" findings:**
  `grep -rin 'did:\|wallet\|micropay\|ledger\|crdt\|gossip\|dht\|hole.punch\|stun\|vector.clock' --include='*.py' --include='*.c' --include='*.cpp' --include='*.h' .`
  (no substantive hits expected), and `grep -rn 'WriteProcessMemory\|CreateRemoteThread\|DriverEntry' .`
  (only refusal strings / comments expected).
- **Confirm no kernel driver / no daemon:**
  `find . \( -name '*.sys' -o -name '*.inf' \)` (none); no `systemd` / `launchd` units; the
  atlas service is loopback-only (`BindLoopback` → `127.0.0.1`).
- **Entry points:** `grep -rn '\[project.scripts\]' -A2 */pyproject.toml`.
