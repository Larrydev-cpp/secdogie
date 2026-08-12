# secdogie

**Product:** secdogie — a vision model that can see your screen and drive the mouse and keyboard, focused on **CAD viewing/editing and other professional desktop apps** with weak or no APIs.

We are building toward **commercial readiness** (measurable scenarios, forced confirmation on save/delete/close, audit trail, install path). Track progress in **[`ROADMAP.md`](ROADMAP.md)**. Baseline tasks: **[`docs/SCENARIOS.md`](docs/SCENARIOS.md)**.

Current builds are **not yet a supported commercial release** — no independent security audit, operator-owned machines only. Use Preview / dry-run first; keep confirmation on when changing drawings.

Point it at a task in plain language. It screenshots the desktop, asks a vision model what to do next, and acts one step at a time. By default it shows a plan first and asks before every click. Save / export / close / delete-class shortcuts still require confirmation even under `--auto`. Your API key stays on disk next to the program.

---

## Who this is for (right now)

| Fit | Why |
|-----|-----|
| Open a drawing, zoom, pan, read a dimension, export PDF | Real GUI, not an API wrapper |
| Apps where plugins are missing, locked, or too heavy | Pixel + accessibility clicks |
| You stay at the machine and approve steps | Confirmation is the safety model |

| Not ready yet | Why |
|---------------|-----|
| Unattended batch on production vaults | Policy engine still thin |
| Multi-user / SaaS | Single-operator trust model |
| Fully hands-off commercial CAD AI | Golden-set reliability still in progress — see ROADMAP |

Game-control helpers exist in-repo for experiments; they are **not** the product focus.

---

## Windows: download the `.exe`

No Python. No terminal required.

1. Open **[Releases](../../releases)** → download `secdogie-agent-windows-….exe`
2. Double-click it
3. First run: paste any vision-model API key (Anthropic, OpenAI, DeepSeek, Groq, custom…)
4. Prefer **Preview only (safe)** or **Smarter clicks** before real edits
5. Type a CAD-style task (or use an example) — it asks before each step

Key and config are saved next to the `.exe` (portable folder).

```powershell
.\build-agent.ps1
# → agent\packaging\dist\secdogie-agent.exe
```

---

## 60-second start (any platform)

```sh
cd agent && python3 -m venv .venv && source .venv/bin/activate && pip install -e .

secdogie-agent --menu          # same flow as the Windows exe
secdogie-agent --gui           # task box + examples

# Terminal: always dry-run first on real drawings
secdogie-agent "In the open CAD window, zoom extents and read the overall width dimension" --dry-run
secdogie-agent "In the open CAD window, zoom extents and read the overall width dimension"
```

Tip: leave the target CAD / viewer already open and focused. Pin with `--window "partial title"` when several apps are up. `--desktop-ax` uses the OS accessibility tree when the app exposes it (often steadier than pure pixels).

Full walkthrough: **[`TUTORIAL.md`](TUTORIAL.md)**.

---

## Example tasks (CAD-oriented)

These map to the baseline scenarios in [`docs/SCENARIOS.md`](docs/SCENARIOS.md):

- *In the already-open drawing viewer, zoom to fit the whole drawing on screen.*
- *Read the overall length dimension and type that number into Notepad.*
- *Pan/zoom so the title block is fully visible.*
- *Export or Save As PDF to the Desktop; do not overwrite without asking.*

Start with **Preview only**. Never point `--auto` at drawings you cannot afford to damage — save/close/delete chords still prompt, but clicks can still mis-hit.

---

## What it is / isn’t

| It is | It isn’t |
|-------|----------|
| A vision loop on **real GUI** (CAD, viewers, office apps without APIs) | A native CAD plugin or DWG parser |
| Step-confirmed, local-first, high-risk key gate | Silent unattended production automation |
| Single-file Windows agent + optional tunnel | A hosted cloud CAD service |
| Product under active commercial hardening | A finished, supported enterprise release |

**Only run it against computers you own or are explicitly authorized to control.** See [`SECURITY.md`](SECURITY.md).

---

## Downloads

Pre-built binaries for `agent`, `android`, `ios`, `open`, and `scene3d` (Linux, Windows, macOS) plus `secdogie-tunnel` (Linux) are on **[Releases](../../releases)**. Built on `v*` / `V*` tags — [`docs/RELEASING.md`](docs/RELEASING.md).

---

## Components

| Piece | Role |
|-------|------|
| [`agent/`](agent/) | Vision-LLM desktop control loop (**main path for CAD/desktop**) |
| [`open/`](open/) | Local web UI: one agent per selected window |
| [`fleet/`](fleet/) | One agent per isolated desktop (VM/session) |
| [`tunnel/`](tunnel/) | From-scratch encrypted VPN (C + libsodium) |
| [`android/`](android/) / [`ios/`](ios/) | Same loop on phones (secondary) |
| [`scene3d/`](scene3d/) | Multi-model 3D view aggregation |
| Game stack (`aim/`, `gta/`, …) | Experimental only — not the product focus |

`agent/` drives whatever is on screen. Tunnel is optional for a remote box you already control.

```mermaid
flowchart TB
    subgraph core["agent/ — two-tier control loop"]
        cap["screenshot"] --> prov["vision-LLM next action"]
        prov --> plan["plan / confirm"]
        plan --> act["closed action schema"]
        act --> backend{{backend}}
        backend --> verify["pixel change check"]
        verify --> cap
    end
    backend --> desktop["desktop CAD / apps"]
    backend --> android["android adb"]
    backend --> ios["ios WDA"]
    open["open/ multi-window"] -->|N instances| core
    core -. optional .-> tunnel["tunnel/"] --> remote[(remote machine)]
```

---

## Optional extras

**Game stack** (experiments only):

```sh
./install.sh            # Linux/macOS   (--yolo, --all)
.\install.ps1           # Windows
```

**Layout**

```
agent/     main: vision computer-control (CAD/desktop)
open/      multi-window UI
fleet/     isolated desktops
tunnel/    libsodium VPN
android/ ios/ scene3d/
aim/ commander/ gta/ handoff/   experimental game helpers
```

---

## Development

```sh
pip install ruff && ruff check .
cd agent && pip install -e . pytest && pytest tests/ -q
```

CI: [`.github/workflows/test.yml`](.github/workflows/test.yml). Commercial checklist: [`ROADMAP.md`](ROADMAP.md).
