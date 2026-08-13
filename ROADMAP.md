# secdogie — path to commercial readiness

**Product:** secdogie  
**Focus:** vision-driven control of CAD viewing/editing and similar professional desktop apps  
**Goal:** reliable enough for daily internal use and, later, paid deployment — not a permanent “experimental” label.

This document is the working plan. Update checkboxes as work lands.

---

## Definition of “commercial-ready” (for us)

A Windows operator can:

1. Install secdogie without a terminal.
2. Complete five fixed CAD-oriented scenarios on a documented app matrix with **measurable success rate**.
3. Rely on **forced confirmation** for save / overwrite / delete / close-document class actions even under `--auto`.
4. Export an **audit trail** of what was decided and done.
5. Hand a colleague a short guide and have them succeed within 15 minutes.

Not required for v1 commercial: multi-tenant SaaS, phone backends, game stack, fleet orchestration.

---

## Phase overview

| Phase | Name | Exit criteria |
|-------|------|----------------|
| **P0** | Usable baseline | 5 scenarios documented; risk gate for destructive keys; default safe path |
| **P1** | Reliable & safe | Window lock + audit default path; stall/fail messages; ≥80% on golden set |
| **P2** | Productized | Signed/clear install, docs, compat matrix, config hygiene |
| **P3** | Near-commercial | License text, support pack, cost controls, optional read-only mode |

---

## P0 — Usable baseline

| ID | Item | Status |
|----|------|--------|
| P0.1 | Product focus = CAD view/edit (not games) | Done (README + examples) |
| P0.2 | First-run key + example tasks | Done |
| P0.3 | Five baseline scenarios documented | Done — [`docs/SCENARIOS.md`](docs/SCENARIOS.md) |
| P0.4 | High-risk gate includes save/delete/close shortcuts | Done — `actions.is_high_risk()` |
| P0.5 | Default UX prefers Preview / confirm | Done — menu order Preview → Recommended (CAD) |
| P0.6 | Golden fixtures (sample drawings + pass criteria) | **Todo** — needs real sample files in `fixtures/` |
| P0.7 | Pin window + AX recommended in flow | Done — Recommended (CAD) = `--gui --desktop-ax` |

**P0 exit:** scenarios written; destructive shortcuts cannot run silent under `--auto`; installer path exists.

---

## P1 — Reliable & safe

| ID | Item | Status |
|----|------|--------|
| P1.1 | Force confirm on high-risk **kinds and keys** | Done |
| P1.2 | Audit trail easy path (GUI auto `--trace`) | Done — `config.default_trace_path()` |
| P1.3 | Hard window/process lock (abort if focus lost) | Partial — focus note exists; abort policy **Todo** |
| P1.4 | Pre-edit “save a copy” prompt for edit-class tasks | **Todo** |
| P1.5 | Clear stop reasons (stall / no window / user decline) | Partial |
| P1.6 | Golden-set runner script + reported success rate | **Todo** |
| P1.7 | Public known-limitations per target app | **Todo** |

**P1 exit:** golden set ≥80% on one pinned Windows + one CAD/viewer build; zero silent high-risk executes in tests.

---

## P2 — Productized

| ID | Item | Status |
|----|------|--------|
| P2.1 | Code-signed Windows exe or documented SmartScreen steps | **Todo** |
| P2.2 | One-page “3 minute start” (EN + ZH) | **Todo** |
| P2.3 | Compat matrix page (OS × app × scenario × result) | **Todo** |
| P2.4 | API keys not written to logs; optional DPAPI/keychain | **Todo** |
| P2.5 | Optional anonymous telemetry (off by default) | **Todo** |
| P2.6 | Clean uninstall / config wipe instructions | **Todo** |

**P2 exit:** non-author succeeds first scenario from docs alone in ≤15 minutes.

---

## P3 — Near-commercial

| ID | Item | Status |
|----|------|--------|
| P3.1 | License + limitation of liability | **Todo** |
| P3.2 | Support bundle (logs + last trace export) | **Todo** |
| P3.3 | Token/step budget caps per run | **Todo** |
| P3.4 | Read-only mode (block type/key except pan/zoom) | **Todo** |
| P3.5 | Security one-pager for IT review | **Todo** |
| P3.6 | Packaging for internal paid pilot | **Todo** |

**P3 exit:** internal pilot pack: installer + matrix + security note + 5 guaranteed scenarios.

---

## Explicitly out of the commercial mainline (for now)

- Game stack (`aim/`, `gta/`, …) — keep code, hide from product narrative  
- Android / iOS agents — secondary  
- Multi-machine fleet / tunnel hub as default UX — after P1  
- Competing with general personal agents (OpenClaw/Hermes) — different product  

---

## Next engineering slice (ordered)

1. ~~High-risk keyboard classification~~  
2. ~~GUI profile: auto `--trace` + recommend `--desktop-ax`~~  
3. `fixtures/` + scenario runner  
4. Abort-on-focus-loss policy  
5. Read-only mode flag  

Track progress by editing the Status column in this file in the same PR that implements the item.
