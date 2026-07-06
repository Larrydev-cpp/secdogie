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
subclass, not just one vendor's beta feature. `AnthropicProvider` is the
reference implementation; swapping in another provider means implementing
`VisionProvider.next_action()`.

## Install

```sh
cd agent
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

### Plugging in your API key

You need an Anthropic API key. The agent looks for it in this order — the
first one found wins:

1. `--api-key sk-...` on the command line
2. the `ANTHROPIC_API_KEY` environment variable
3. a **config file** you fill in once

The config file is the easiest if you don't want to set an env var every
time. Create a template and edit it:

```sh
secdogie-agent --init-config      # writes ~/.config/secdogie/config (chmod 600)
```

```ini
# ~/.config/secdogie/config
ANTHROPIC_API_KEY=sk-...
# SECDOGIE_MODEL=claude-sonnet-5   # optional default model
```

Searched config locations (first that exists wins): `./secdogie.env`,
`~/.config/secdogie/config`, `~/.secdogie/config`. Point at a specific file
with `--config PATH`.

### Or: a single-file executable (no Python needed)

To hand someone a program they can run without installing Python at all,
build a standalone binary with PyInstaller — see
[`packaging/README.md`](packaging/README.md):

```sh
./packaging/build.sh          # produces packaging/dist/secdogie-agent
./packaging/dist/secdogie-agent --help
```

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

## Run (from a terminal)

```sh
secdogie-agent "open a text editor and type 'hello world'" --dry-run   # see what it would do first
secdogie-agent "open a text editor and type 'hello world'"             # confirms every action (default)
secdogie-agent --gui                                                    # pops up a task window instead
secdogie-agent "..." --auto                                             # no confirmations -- see warning above
```

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

Cursor movement is intentionally not instantaneous — teleport-and-click can
miss hover/focus handlers in some apps, so the agent glides to the target
and pauses briefly before pressing.

## Actions it can take

Each step the model picks one action: `left_click` / `right_click` /
`double_click` / `move` / `drag`, `type` (types text — **Chinese/emoji/other
Unicode is handled automatically via the clipboard**), `key` (a press or
hotkey; arrow keys are `up`/`down`/`left`/`right`), `hold_key` (**hold key(s)
down for N seconds** — use for continuous movement like walking in a game or
panning a map), `scroll`, `open` (**open a file/folder/URL with the OS default
program**, no mouse needed), `wait`, plus `done` and `ask_user`.

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
