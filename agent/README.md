# secdogie-agent

A vision-LLM computer-control agent: it takes a screenshot, asks a
vision-capable model what to do next, and executes exactly one action
(click, type, scroll, ...) at a time toward a task you describe in plain
language. Point it at your own machine and give it a task; it loops
screenshot -> model -> action -> repeat until the model says it's done.

> **Read this before running it.** This program moves your real mouse and
> types on your real keyboard. Only run it against a computer you own or
> are explicitly authorized to control. Start with `--dry-run`, keep the
> default per-step confirmation on until you trust it, and never run
> `--auto` unattended against a machine you can't immediately reach to stop
> it. Slamming the mouse cursor into any screen corner triggers pyautogui's
> built-in fail-safe and aborts in-flight actions.

## Why not the vendor's "computer use" tool directly?

Anthropic (and others) expose a purpose-built "computer use" tool/beta in
their APIs. This project deliberately uses the *plain* vision message API
instead — send a screenshot, ask for one JSON action back in a schema this
project owns (see `secdogie_agent/providers/base.py`) — so the same agent
loop works with any vision-capable chat model behind a `VisionProvider`
subclass, not just one vendor's beta feature. Two providers ship today —
`AnthropicProvider` (Claude, the default) and `OpenAIProvider` (GPT / o-series)
— and both ask their model for the *same* action schema (kept in
`secdogie_agent/providers/prompts.py`). Adding another vendor is one more
`VisionProvider` subclass implementing `next_action()`.

### Picking a provider and model

The model id selects the provider: `claude-*` routes to Anthropic, `gpt-*` and
the `o1`/`o3`/`o4` reasoning models route to OpenAI. You can also name the
provider explicitly, either with a `provider/model` ref or the `--provider`
flag:

```sh
secdogie-agent "..."                                 # claude-sonnet-5 (default)
secdogie-agent "..." --model gpt-5.5                 # routes to OpenAI
secdogie-agent "..." --model openai/gpt-5.5          # same, explicit ref
secdogie-agent "..." --provider openai               # OpenAI's default model
```

The OpenAI provider needs the `openai` package, installed as an extra:

```sh
pip install -e '.[openai]'      # or: pip install openai
```

## Install

**Linux/macOS:**
```sh
cd agent
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

**Windows (PowerShell):**
```powershell
cd agent
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```
(cmd: `.venv\Scripts\activate` instead of `Activate.ps1`. If PowerShell
refuses to run the script, `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`
first.) Or skip Python entirely — see [single-file executable](#or-a-single-file-executable-no-python-needed)
below.

### Plugging in your API key

You need an API key for whichever provider your model uses — an Anthropic key
for `claude-*` models, an OpenAI key for `gpt-*` / o-series models. The agent
looks for it in this order — the first one found wins:

1. `--api-key sk-...` on the command line
2. the provider's environment variable (`ANTHROPIC_API_KEY` or `OPENAI_API_KEY`)
3. a **config file** you fill in once

The config file is the easiest if you don't want to set an env var every
time. Create a template and edit it:

```sh
secdogie-agent --init-config      # writes ~/.config/secdogie/config (chmod 600)
```

```ini
# ~/.config/secdogie/config
ANTHROPIC_API_KEY=sk-...
# OPENAI_API_KEY=sk-...            # only if you use gpt-* / o-series models
# SECDOGIE_MODEL=claude-sonnet-5   # optional default model
```

Searched config locations (first that exists wins): `./secdogie.env`,
`~/.config/secdogie/config`, `~/.secdogie/config`. Point at a specific file
with `--config PATH`.

**On Windows**, `--init-config` still works the same way (writes to
`%USERPROFILE%\.config\secdogie\config` — an unusual-looking but valid path;
`~` above means your home directory on every OS). The simplest option,
especially for the standalone `.exe`, is the first-checked location: create a
plain text file named `secdogie.env` **in the same folder you're running
from** (Notepad is fine) with the `ANTHROPIC_API_KEY=sk-...` line — no
special path needed at all.

### Or: a single-file executable (no Python needed)

To hand someone a program they can run without installing Python at all,
build a standalone binary with PyInstaller — see
[`packaging/README.md`](packaging/README.md):

```sh
./packaging/build.sh          # Linux/macOS -- produces packaging/dist/secdogie-agent
./packaging/dist/secdogie-agent --help
```

**Windows (PowerShell):**
```powershell
packaging\build.ps1          # produces packaging\dist\secdogie-agent.exe
.\packaging\dist\secdogie-agent.exe --help
```
(cmd.exe can't run `.ps1` files directly: `powershell -ExecutionPolicy Bypass -File packaging\build.ps1`.)

(CI also builds and publishes `secdogie-agent.exe` on tagged releases — see
[`docs/RELEASING.md`](../docs/RELEASING.md) — check the
[Releases](../../releases) page before building your own.)

## Opening it (downloaded binary — no command line needed)

If you downloaded a release zip (from the
[Releases](../../releases) page), it already contains a **double-click
launcher** next to the program — you don't need to touch a terminal:

| Your OS | Double-click this |
|---------|-------------------|
| Windows | `open.bat` |
| macOS   | `open.command` (first time: right-click → **Open** to get past Gatekeeper) |
| Linux   | `run.sh` (from a terminal: `./run.sh`) |

On the **first run** the launcher creates a config file and tells you where
it is; open that file, paste your Anthropic API key after
`ANTHROPIC_API_KEY=`, save, and launch again. After that it opens a **window
asking what you want it to do** (that's `--gui` mode), shows you the model's
plan, and asks you to approve before it acts.

## Run

```sh
secdogie-agent "open a text editor and type 'hello world'" --dry-run   # see what it would do first
secdogie-agent "open a text editor and type 'hello world'"             # confirms every action (default)
secdogie-agent --gui                                                    # pops up a task window instead
secdogie-agent "..." --auto                                             # no confirmations -- see warning above
secdogie-agent "..." --auto --allow-risky                              # ...not even for high-risk actions
```

**High-risk actions still confirm under `--auto`.** `--auto` trusts the model
to click and type unattended, but not to reach *outside* the screen — the
`open` action hands an arbitrary file/URL to the OS default handler, so it can
launch a program or open a link. That one kind prompts for a `y/N` even under
`--auto` (the prompt is labelled `HIGH-RISK`). On a run with no terminal to
answer (piped stdin, a service), an unconfirmed high-risk action **fails closed
— it's skipped, never silently launched.** Pass `--allow-risky` to opt back
into running those unattended too.

### GUI mode: task dialog + plan briefing

`--gui` opens graphical dialogs instead of using the terminal:

```sh
secdogie-agent --gui                 # a window prompts for the task
secdogie-agent --gui "book a table"  # task given, still shows the plan dialog
```

The flow is: (1) if you didn't pass a task, a window asks for it; (2) the
model looks at your current screen and, **before touching anything**, shows a
popup restating the task as it understood it plus a short numbered plan — you
click **Proceed** or **Cancel**; (3) any `ask_user` question during the run
appears as a Yes/No popup. GUI mode needs tkinter (bundled with standard
Python; on Linux `sudo apt install python3-tk`). If it isn't available, the
agent prints a notice and falls back to the terminal automatically.

Requires a GUI session (X11/most desktop environments; Wayland support
depends on your compositor's support in `mss`/`pyautogui`). It will not do
anything useful over SSH to a headless box with no display.

## Click accuracy

Vision models reason about a *downscaled* copy of large screenshots, so raw
pixel coordinates they emit drift off-target. The agent controls this
itself: it resizes each screenshot to a known size (`--max-image-edge`,
default 1568px long edge — the size large images are reduced to internally
anyway), tells the model that exact size, and scales the returned
coordinates back to real screen pixels. This keeps clicks landing where the
model intends.

Extra knobs:

| Flag | Effect |
|------|--------|
| `--grid` | overlay a labeled coordinate grid on the screenshot to give the model anchor points (helps on cluttered screens) |
| `--max-image-edge N` | trade detail vs. speed/cost; higher keeps small text legible, lower is faster/cheaper |
| `--move-duration S` | seconds to glide the cursor to a target (default 0.15; smoother, triggers hover events) |
| `--settle S` | seconds to hover before clicking (default 0.05; lets the UI react) |
| `--action-pause S` | seconds to wait *after* each action before the next screenshot (default 0.4). This is the timing safeguard: without it a fast model takes the next screenshot before a slow-animating app has updated, sees a stale frame, and repeats itself. Lower is faster but riskier; `0` disables. |
| `--stall-limit N` | stop if the model picks the same action against an unchanged screen `N` times in a row (default 4) — the action isn't landing (a dead control, a frozen render), so bail with exit code 6 instead of spinning to `--max-steps`. `0` disables. |
| `--plan` | decompose the task into sub-tasks up front and work one at a time (see below). |
| `--subtask-step-limit N` | with `--plan`, skip a sub-task that runs `N` steps without finishing (default 15; `0` disables). |
| `--trace PATH` | write a tamper-evident hash-chained audit trace of every step to `PATH` (JSONL); verify later with `python -m secdogie_agent.trace PATH` (see below). |
| `--memory PATH` | give the agent persistent cross-run memory in the SQLite file `PATH`: it saves durable facts with a `remember` action and they're recalled into its prompt on later runs (see below). Plaintext — never have it store secrets. |
| `--allow-risky` | with `--auto`, run high-risk actions (currently `open`, which launches a file/URL) without confirmation; by default those still prompt even under `--auto` (see [Before you run](../README.md#before-you-run-any-of-this)). |

Cursor movement is intentionally not instantaneous — teleport-and-click can
miss hover/focus handlers in some apps, so the agent glides to the target
and pauses briefly before pressing.

## Action verification & retry

The biggest source of flaky automation is an action that silently doesn't land:
a click a few pixels off the button, a window that wasn't focused, an overlay
covering the target. After each mutating action the agent takes a fresh
screenshot and compares it to the pre-action frame (`screen.changed_ratio`, a
downscaled grayscale pixel diff). If nothing visibly changed:

- **idempotent actions** (`left_click`, `right_click`, `double_click`, `move`,
  `scroll`) are **retried** up to `action_retries` times (default 1) — the
  common cause is a transient miss (focus not ready, mid-animation), and
  re-clicking an unresponsive spot is harmless;
- **side-effectful actions** (`type`, `key`, `hold_key`, `drag`, `open`) are
  **never auto-retried** — re-sending them would double-type text or re-submit —
  but the result still carries a note;
- if it still didn't land, the result fed back to the model gets
  `"(no visible change detected ...)"`, and the system prompt tells the model to
  **change target or approach** rather than repeat a dead action.

This is a heuristic: it detects *visible* change, not semantic success (an action
that correctly changed something off-screen reads as "no change"). It's tuned to
cut the most common failure — a repeated action that never lands — not to judge
task completion. Controlled by `AgentConfig.verify_actions` (default on),
`verify_threshold` (changed-pixel fraction, default 0.005), and `action_retries`
(default 1); it composes with `--stall-limit`, which remains the cross-step
backstop. The one-frame diff is a few milliseconds locally.

## Verifiable execution trace

For high-stakes or zero-trust runs, `--trace run.jsonl` writes a **tamper-evident
audit log**: one entry per step recording *what the model saw* (a SHA-256 of the
exact screenshot), *what it decided* (the action + its reasoning), and *what
happened* (the result), each stamped with a time and sequence number.

The entries form a **hash chain** — every entry commits to the previous one's
hash — so altering, reordering, or dropping any past entry changes its hash and
breaks every entry after it. The last entry's hash (`head`) is a single
commitment to the whole ordered history. Verify a trace afterwards:

```bash
python -m secdogie_agent.trace run.jsonl
# trace OK: 42 entry(ies), chain intact. head=ca769f78...
# (or) trace TAMPERED: entry 7: content does not match its hash (entry was edited)
```

The file is written incrementally (each step is flushed as it happens), so it
survives a crash mid-run. **Honest scope:** the chain proves *internal
consistency* — no entry was edited without recomputing all following hashes. It
is not a signature, so a party who can rewrite the whole file can also recompute
a fresh valid chain, and truncation from the end is only detectable against a
known head. To make it genuinely tamper-*proof*, anchor the `head` somewhere the
rewriter doesn't control (print it to a monitored log, sign it, or publish it at
run time). A Merkle tree would add per-entry inclusion proofs; the chain is the
simpler tool that fits an append-only, ordered log.

## Planning (task decomposition)

`--plan` adds a small planning layer for longer, multi-part jobs. Before acting,
the model breaks the task into a short ordered list of sub-tasks; the agent then
works **one at a time**, carrying the plan and progress into every prompt:

```
PLAN PROGRESS (1/4 done). Work ONLY on the CURRENT sub-task...
  [x] open the File menu
  -> [ ] click Save As   <-- CURRENT sub-task
     [ ] type the filename
     [ ] click Save
```

Two reasons this helps a long task:

- **State management** — the model always knows where it is in the job instead of
  re-deriving the whole plan from the last 10 actions each step. With planning
  on, `done` means "**this sub-task** is finished" and advances to the next; the
  run ends only when the last one completes.
- **Error recovery** — a sub-task that burns `--subtask-step-limit` steps without
  finishing is **skipped** (the loop moves to the next one and logs it) rather
  than spinning the whole run to `--max-steps`. A run that finished but skipped
  something exits `3` (incomplete), not `0`.

Planning costs one extra model call up front and is **off by default**; it's most
worth it for tasks with clear sequential stages, less so for a single click.
Providers implement it via `plan_task` (Anthropic and OpenAI both do); a provider
that doesn't just runs unplanned.

## Memory (persistent facts across runs)

The loop is otherwise stateless — each run starts fresh. `--memory mem.sqlite`
gives it a small key/value store that survives between runs (`secdogie_agent/memory.py`,
backed by SQLite). The model saves a durable fact with a `remember` action:

```json
{"action": "remember", "text": "the Save button is bottom-right", "key": "save_btn"}
```

- A `key` makes it an **upsert** — re-remembering the same key updates that fact.
  Omit the key for a one-off note (auto-keyed, time-ordered).
- On the next run, everything remembered is **recalled into the prompt** so the
  model reads what it learned before instead of rediscovering it — where a
  control lives, a preference you confirmed, how far it got on a long job. The
  block is rebuilt each step (and capped), so a fact saved mid-run is visible on
  the very next step, not just next time.

```sh
secdogie-agent "learn where things are in this app, remember them" --memory app.sqlite --auto
secdogie-agent "now use what you learned to export a report" --memory app.sqlite --auto
```

**Never have it store secrets.** The file is plaintext on disk — it's your
machine, your file. The prompt tells the model not to save passwords/tokens, and
`remember` refuses values that obviously look like credentials (a key named
`password`, a value shaped like an API token) as a *best-effort backstop* — a
backstop, not a guarantee. Memory is **off by default**; without `--memory` a
`remember` is a harmless no-op.

## Actions it can take

Each step the model picks one action: `left_click` / `right_click` /
`double_click` / `move` / `drag`, `type` (types text — **Chinese/emoji/other
Unicode is handled automatically via the clipboard**), `key` (a press or
hotkey; arrow keys are `up`/`down`/`left`/`right`), `hold_key` (**hold key(s)
down for N seconds** — use for continuous movement like walking in a game or
panning a map), `scroll`, `open` (**open a file/folder/URL with the OS default
program**, no mouse needed — this one still asks for confirmation even under
`--auto`, see [`--allow-risky`](#click-accuracy)), `wait`, `remember` (**save a durable
fact** to cross-run memory when `--memory` is on, see above), plus `done` and
`ask_user`.

## Watch mode (monitor a screen, act on a trigger)

`--watch` turns the agent into a monitor: it polls the screen frame by frame
and does **nothing** until the situation you described occurs, then acts.

```sh
# keep watching; when the condition appears, it opens a file
secdogie-agent --watch "when a red 'BUILD FAILED' banner shows, open /home/me/build.log"
secdogie-agent --watch --watch-interval 5 --auto "when the download finishes, double-click setup.exe"
```

- `--watch-interval N` sets the minimum seconds between frames (default 2).
- While the trigger hasn't occurred, the model returns `wait` and the loop
  logs "watching…" — no confirmation prompts for these idle frames.
- When it triggers, the action runs (still confirmed unless `--auto`; use
  `--watch --auto` for fully unattended monitoring).
- Watch mode runs long by default (up to 100000 frames); cap it with
  `--max-steps`.

## Can it play games?

Only **slow, turn-based ones** — and that's a hard limit, not a tuning issue.
Every action costs one screenshot → API round-trip → one move, so the agent
makes roughly one decision every **1–3 seconds**. That's fine for games where
nothing happens until you move, and hopeless for anything real-time.

- **Works:** Minesweeper, Solitaire and other card games, 2048, Sudoku,
  chess/checkers/Go, turn-based strategy, point-and-click and text adventures,
  simple board/puzzle games.
- **Doesn't work:** platformers, shooters, racing, fighting, rhythm, or any
  action game needing reactions faster than a second. `hold_key` lets it hold
  a direction to move, but the *next* decision still waits on the model, so it
  can't dodge or aim in real time.

Think "a patient assistant taking one considered move at a time", not "a reflex
bot". For reactive setups, `--watch` fits better (wait for a condition, then
make one move) than trying to play frame-by-frame.

## RPA macros: record once, replay for free

`--macro PATH` gives a task you've already driven successfully a repeatable,
zero-model-call fast path — the "robotic" half of RPA, on top of the vision
model that figures out *what* to do the first time.

```sh
secdogie-agent "log into example.com and open the dashboard" --macro dashboard.json --auto
# first run: PATH doesn't exist yet, so this drives it live (asking the model
# every step) exactly as without --macro, and on success saves dashboard.json

secdogie-agent "log into example.com and open the dashboard" --macro dashboard.json --auto
# second run: PATH exists, so each step first tries to replay the recorded
# sequence -- no model calls, no API cost -- falling back to the live model
# only if a step can no longer be resolved
```

- Each step first tries the next recorded step before ever asking the model.
  The moment one can't be resolved (its target is no longer on screen — the
  UI changed), the agent gives up on replay for the rest of that run and
  drops back to the normal live loop, same as if `--macro` had never been
  passed.
- Each positional step re-resolves its target on replay through a ladder, from
  strongest anchor to weakest, so a click keeps landing even as the UI shifts:
  1. **Semantic selector** — the UI element by identity (its accessibility
     name / automation-id / role), so a re-find is exact regardless of where the
     element moved. The Android backend always does this via the uiautomator
     hierarchy (see `android/README.md`); on the **desktop** it's opt-in with
     [`--desktop-ax`](#desktop-accessibility---desktop-ax), which reads the OS
     accessibility tree (UI Automation on Windows).
  2. **Visual anchor** — a tiny grayscale snapshot of the clicked element,
     re-found on the current screen by [reflex](#latency-and-the-local-reflex-layer)
     NCC template matching, then mapped back to the click point. This is what
     makes **desktop** replay robust: instead of trusting a fixed spot, it finds
     *what the button looked like* even after the window moved or the layout
     reflowed. Needs numpy (the `[reflex]` extra); the patch is embedded in the
     macro JSON. Falls through if the element genuinely isn't on screen.
  3. **Normalized `(0..1, 0..1)` coordinate** — the last-resort position, used
     when there's no selector and the visual anchor can't be found.

  If none of these resolves the target, the step is treated as unresolvable and
  the run drops back to the live model (above).
- A run that finishes successfully — replayed, live, or a mix of both — always
  re-saves the full sequence to PATH, so a macro can self-heal after a UI
  change without you re-recording it by hand.
- `--watch` and `--macro` don't compose: watch mode waits for a
  variable-length trigger, which doesn't fit a fixed recorded sequence, so
  `--macro` is ignored entirely (never loaded or written) whenever `--watch`
  is on.
- The macro file is plain, human-readable JSON (`secdogie_agent/macro.py`) —
  inspect or hand-edit it if useful.

### Desktop accessibility (`--desktop-ax`)

By default the desktop backend drives raw pixels, so macro replay leans on the
visual anchor (tier 2 above). `--desktop-ax` makes it **element-aware**: it reads
the OS accessibility tree and records each click against the widget's identity
(its accessibility name / automation-id / role) — the strongest tier, exact even
when the window moves or the layout reflows.

```sh
pip install 'secdogie-agent[windows-ax]'   # Windows: UI Automation (uiautomation)
sudo apt install python3-pyatspi            # Linux: AT-SPI bindings (+ enable a11y); a system pkg
pip install 'secdogie-agent[macos-ax]'      # macOS: AX API (pyobjc) — also grant Accessibility
secdogie-agent "..." --macro flow.json --desktop-ax --auto
```

The tree-reading half is on-machine (it needs a real desktop), and its provider
is platform-specific — all three are now wired against the same
`axtree.AxElement` contract (`secdogie_agent/desktop_ax.py`): **Windows** via UI
Automation (the `uiautomation` package), **Linux** via AT-SPI (`pyatspi`), and
**macOS** via the AX API (pyobjc's `ApplicationServices`). macOS has one extra
gate the others don't: the host app (Terminal, your IDE, the built `.app`) must
be granted **Accessibility** permission in System Settings → Privacy & Security →
Accessibility, or the AX API returns nothing. If the accessibility library isn't
installed — or that permission isn't granted — the flag no-ops with a one-line
hint (replay falls back to the visual anchor, the live loop to pixels), so
nothing breaks. The matching logic itself (`secdogie_agent/axtree.py`,
`elements.py`) is pure and unit-tested without a desktop; only the live tree walk
is machine-specific, and each provider's walk/mapping is proved against a faked
platform API in `tests/test_axtree.py`.

**Click by identity, not by pixel (live loop).** `--desktop-ax` also changes how
the model drives *live*, not just how macros replay. On each step the current
interactable elements (buttons, fields, menu items, …) are read from the
accessibility tree and appended to the prompt as a short list, each with a stable
ref:

```
Interactable elements detected on screen (from the accessibility tree)...
  [e1] Button "Save" (id=saveBtn)
  [e2] Button "Cancel"
  [e3] Edit "Filename" (id=fileBox)
```

The model can then reply `{"action": "click_element", "element": "e2", ...}` and
the loop resolves that ref to the element's true bounds — a real-pixel click with
no coordinate-scaling round-off and no near-miss, because the tree *knows* the
widget is there. It's the non-vision path for desktop control (the same idea as
reading a game's native state instead of its pixels): the screenshot still goes
to the model for everything the tree can't name (canvases, custom-drawn UI, a
remote screen), so vision is the fallback, not the only sense. A ref that no
longer resolves is reported back as a miss rather than clicked blindly, and with
no provider the listing is simply absent — the pixel path is byte-for-byte
unchanged. Perception + resolution are pure and headless-tested
(`secdogie_agent/elements.py`, `tests/test_elements.py`); only the tree walk is
on-machine.

**The model decides when to spend a fresh look.** In this mode the *tree* is the
fresh, authoritative sense re-read every step, so the loop stops re-capturing a
fresh screenshot each time — it sends the **cached** frame as visual context (the
model isn't blind) and re-captures only when the model emits a `look` action, on
the first step, or when the tree comes back empty (nothing to click by identity →
fall back to real vision). That inverts the default "screenshot every step":
vision becomes a tool the model reaches for when the pixels actually matter,
rather than a cost paid on every turn — the same "structured-first, vision on
demand" shape a personal-assistant harness like OpenClaw uses for desktop
control. The `look` gating and frame cache are exercised through the real loop in
`tests/test_loop.py`.

## Programmable skills: sub-flows, conditions, loops

A recorded macro is a flat, one-shot sequence — no parameters, no reuse, no
branching. A **skill** is the step up: an authored JSON program of named,
parameterized flows that call each other, branch on what's on screen, and loop.
Run one with `--skill`:

```bash
secdogie-agent --skill skills.example.json --skill-entry main \
  --skill-arg user=alice --skill-arg pass=secret --skill-arg count=20 --auto
```

Statements are `action` (any of the actions above, with `{param}` substitution),
`call` (invoke another skill with args), `if` (then/else), `while`, `repeat`
(with an optional 1-based index var), and `set`/`incr` for counters. Conditions
are either `screen` (a yes/no question answered by the model about the current
screenshot — "a cookie banner is covering the page") or `expr` (a pure compare
like `{i} < 10`), optionally wrapped in `not`. See
[`skills.example.json`](skills.example.json) for a login-then-process-N-rows
program.

```json
{"op": "while",
 "cond": {"kind": "screen", "description": "there is another unread row"},
 "body": [{"op": "call", "skill": "handle_row"}]}
```

Design and honesty:

- **The interpreter is deterministic and unit-tested** (`secdogie_agent/skill.py`)
  — control flow, parameter binding, recursion/loop guards. A runaway `while` or
  recursion is bounded (`max_loop` / `max_statements` / call-depth), so a bad
  program stops rather than spins forever.
- **Only `screen` conditions call the model** (one cheap yes/no per check); the
  actions and control flow don't. So a skill is far cheaper than the live loop
  while being far more capable than a flat macro.
- It is *authored*, not recorded — you write (or generate) the JSON. Coordinates
  are real screen pixels; `skill_runner.py` wires the interpreter to the backend
  (hands) and provider (eyes).

## Latency, and the local reflex layer

A cloud vision model runs at roughly **1 Hz** — 1–3 seconds per screenshot →
action round trip. That's fine for *deciding what to do*, but hopeless for
anything that has to keep up with a **60 Hz** screen (a moving target, a value
you're dragging to a mark, a fast event): by the time one screenshot has
round-tripped, ~16 frames have already gone by. No amount of tuning fixes this —
it's the physics of a network call to a large model, not an optimization target.

So secdogie uses the standard two-tier split that every real-time control system
uses: the **model is the slow planner**, and a **fast local loop is the
controller**. Two things keep the model *out* of the tight loop:

- **Macros** (`--macro`, above) replay a known sequence with **zero** model
  calls.
- **The reflex layer** (`secdogie_agent/reflex.py`, needs
  `pip install 'secdogie-agent[reflex]'`) closes a tight, goal-directed loop
  *locally* — capture a small region, match a target template, move/click — at
  frame rate, no network call per frame. `pursue()` tracks a moving target and
  clicks it once it settles; `track_and_click_desktop(region, target)` wires
  that to mss + pyautogui:

  ```python
  from secdogie_agent.reflex import track_and_click_desktop
  # chase a moving element inside this screen region and click it when it stops
  result = track_and_click_desktop(region=(600, 300, 400, 400), target=(800, 480))
  print(result.outcome, result.fps)   # e.g. "clicked" 55.0
  ```

  Template matching is FFT-based normalized cross-correlation: about **4 ms
  (~230 Hz)** for a 200×200 search region and ~14 ms (~70 Hz) for 320×240 on a
  laptop CPU — so keep the search region bounded around the target and the loop
  comfortably clears 30–60 Hz. It is a CPU reflex, not a game engine; sub-
  millisecond control needs C/GPU and is out of scope. The model decides *what*
  to pursue; the reflex layer does the chasing.

The model can hand off to the reflex layer itself, without any glue code: it
emits a **`track_click`** action naming where the moving target is *right now*,
and the loop drops into the local pursuit — a window around that point is
captured and matched at frame rate, and the target is clicked the instant it
settles, all with no model call per frame. Control returns to the model with a
one-line result (`clicked` / `lost` / `timeout` and the frame rate reached).
This runs inside the same input lock as every other action, so a multi-second
chase owns the physical cursor exclusively — no other window's actor can inject
input mid-pursuit. It's desktop-only (mss + pyautogui + numpy); the prompt marks
it that way, since over adb/WDA a phone can't be captured at frame rate anyway.

**Sharpening a fuzzy detection (`reflex.refine_point`).** A vision model works
off a *downscaled* screenshot, so the coordinate it returns is approximate — off
by tens of pixels, because it never saw the full-resolution image. Precision
doesn't come from feeding it a bigger blurry frame; it comes from a coarse→fine
(foveated) step: take the model's rough point, crop a small window around it at
the frame's **native** resolution, and NCC-match the target's appearance there
to pin the exact center — the same few-millisecond match. If the target isn't in
the window it returns the coarse point unchanged (`refined=False`) rather than
jumping somewhere wrong. So: cheap fuzzy model detection to find *roughly* where,
then a sharp local match to pin *exactly* where.

## How it decides what to do

Each step, the model sees the current screenshot, the task, and a short
history of the last 10 actions and their results, and must reply with
exactly one JSON action (`left_click`, `type`, `key`, `scroll`, `wait`,
`done`, `ask_user`, ...). The system prompt instructs it to use `ask_user`
instead of acting whenever a step would involve credentials, payments,
sending messages on your behalf, or deleting data — a guardrail, not a
guarantee; you are still the backstop via the per-step confirmation.

## Layout

```
secdogie_agent/
  cli.py                 argument parsing, wires a provider into the loop
  config.py              API-key/model resolution (CLI > env > config file)
  dialog.py              optional tkinter dialogs (task entry, plan briefing, ask_user)
  loop.py                the screenshot -> action -> execute -> repeat loop
  backend.py              Backend protocol (setup/capture/execute) + optional Locatable capability
  macro.py                RPA macro record/replay: Macro, MacroRecorder, resolve_replay_step (selector/anchor/coord tiers)
  axtree.py               pure desktop accessibility-tree model + queries (element_at / find / selector_for) -- tested
  desktop_ax.py           on-machine seam: read the live OS accessibility tree (UI Automation) into AxElements
  skill.py                programmable skill interpreter (call/if/while/repeat/params) -- pure, tested
  skill_runner.py         wires skills to a real backend + a model yes/no for conditions (--skill)
  plan.py                 task decomposition + sub-task progress tracking (used with --plan)
  trace.py                tamper-evident hash-chained audit trace (used with --trace; `python -m` verifies)
  memory.py               persistent cross-run key/value memory (SQLite; used with --memory) -- tested
  reflex.py               local reflex layer: FFT template matching + a frame-rate pursue loop (needs numpy)
  screen.py               screenshot capture + resize/coordinate scaling (mss + Pillow)
  actions.py              executes an Action via pyautogui (smooth move + settle)
  safety.py                logging + y/N confirmation
  providers/
    base.py               Action schema + VisionProvider interface + JSON parsing
    anthropic_provider.py  reference implementation using the Anthropic API
tests/                    unit tests (fake provider + monkeypatched I/O, no display needed)
```

## Tests

```sh
pip install -e . pytest
pytest tests/ -q
```

The test suite exercises the control flow (`done`, `ask_user`, `--auto`,
`--dry-run`, `max_steps`) with a scripted fake provider and a monkeypatched
screen/actions layer, so it runs in a headless CI environment with no
display and no API key.
