# secdogie baseline scenarios (CAD-oriented)

Product scenarios for measuring readiness. **Not** a guarantee that every CAD brand behaves the same — each target app must be listed in the compat matrix when tested.

## Rules for every scenario

1. Target application is **already open** with a sample drawing loaded (unless the scenario says otherwise).
2. Prefer **Preview only** (`--dry-run`) on first pass.
3. Prefer **Smarter clicks** (`--desktop-ax`) when the app exposes UI Automation.
4. Pin the window when possible: `--window "partial title"`.
5. Success must be **observable** (on-screen text, exported file, or dimension copied to Notepad).
6. High-risk actions (save, overwrite, close document, delete) must prompt even under `--auto`.

---

## S1 — Zoom to fit

| Field | Value |
|-------|--------|
| **Goal** | Whole drawing visible on screen |
| **Prompt** | `In the already-open CAD or drawing viewer, zoom to fit the whole drawing on screen` |
| **Pass** | Drawing fills the view without obvious clipping of title block / extents |
| **Fail** | Wrong window, no zoom, or model spins without acting |
| **Risk** | Low (view only) |

## S2 — Read overall dimension

| Field | Value |
|-------|--------|
| **Goal** | Extract a length/width dimension into Notepad |
| **Prompt** | `In the open drawing, find the overall length or width dimension and type that number into Notepad` |
| **Pass** | Notepad contains a numeric value consistent with the visible dimension |
| **Fail** | Wrong number, typed into wrong app, or dimension not found and not reported |
| **Risk** | Low–medium (typing only) |

## S3 — Show title block

| Field | Value |
|-------|--------|
| **Goal** | Title block readable on screen |
| **Prompt** | `Pan and zoom so the drawing title block is fully visible and readable` |
| **Pass** | Title block text legible in a screenshot of the target window |
| **Fail** | Model pans unrelated region |
| **Risk** | Low |

## S4 — Layer visibility (non-destructive)

| Field | Value |
|-------|--------|
| **Goal** | Toggle a named layer off if the UI is available |
| **Prompt** | `If a layer list is visible, turn off the layer named HATCH (or the first hatch-like layer). If no layer UI is visible, stop and ask instead of guessing` |
| **Pass** | Layer toggled **or** explicit ask_user / stop when UI missing |
| **Fail** | Random clicks in the drawing area; silent wrong layer |
| **Risk** | Medium (edits display state; still avoid Save) |

## S5 — Export PDF

| Field | Value |
|-------|--------|
| **Goal** | Produce a PDF on the Desktop without silent overwrite |
| **Prompt** | `Export or Save As PDF to the Desktop; if a name is required use secdogie-export.pdf and do not overwrite an existing file without asking` |
| **Pass** | `secdogie-export.pdf` (or agreed name) appears; overwrite required a confirmation |
| **Fail** | Overwrote silently; exported wrong format; closed document |
| **Risk** | **High** — save/export paths must hit the high-risk gate |

---

## Golden set (to be filled)

| Scenario | App + version | OS | Date | Result | Notes |
|----------|---------------|-----|------|--------|-------|
| S1 | _TBD_ | Win 11 | | | |
| S2 | | | | | |
| S3 | | | | | |
| S4 | | | | | |
| S5 | | | | | |

Add rows per tested environment. Commercial P1 exit: **≥4/5 pass** on one frozen environment, three consecutive runs.
