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
export ANTHROPIC_API_KEY=sk-...   # the only thing you need to "plug in an API"
```

### Or: a single-file executable (no Python needed)

To hand someone a program they can run without installing Python at all,
build a standalone binary with PyInstaller — see
[`packaging/README.md`](packaging/README.md):

```sh
./packaging/build.sh          # produces packaging/dist/secdogie-agent
./packaging/dist/secdogie-agent --help
```

## Run

```sh
secdogie-agent "open a text editor and type 'hello world'" --dry-run   # see what it would do first
secdogie-agent "open a text editor and type 'hello world'"             # confirms every action (default)
secdogie-agent "..." --auto                                             # no confirmations -- see warning above
```

Requires a GUI session (X11/most desktop environments; Wayland support
depends on your compositor's support in `mss`/`pyautogui`). It will not do
anything useful over SSH to a headless box with no display.

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
  loop.py                the screenshot -> action -> execute -> repeat loop
  screen.py               screenshot capture (mss)
  actions.py              executes an Action via pyautogui
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
