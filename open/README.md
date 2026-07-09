# secdogie-open

A GUI on top of [`secdogie-agent`](../agent): it lists every open window on
your desktop, lets you pick several, and runs one agent instance per
selected window at once -- each scoped to just that window's screen region,
so clicks/typing from one window's agent can't land on another.

> Read `agent/README.md`'s safety section first -- everything there applies
> here too, times however many windows you select at once.

## Why

`secdogie-agent` alone drives the whole primary monitor with one task at a
time. This splits the screen by window instead, so several tasks can run
concurrently against different apps. It's step one toward running each
window's agent off its own API key (avoiding one key's rate limit under
higher concurrency); today every window still shares the single key
`secdogie-agent`'s own config resolution finds (`--model`'s provider,
`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`, or a config file -- see
`agent/README.md`).

## Install

**Linux/macOS:**
```sh
cd open
python3 -m venv .venv && source .venv/bin/activate
pip install -e ../agent      # secdogie-open drives it as a library
pip install -e .
```

**Windows (PowerShell):**
```powershell
cd open
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ..\agent
pip install -e .
```
(cmd: `.venv\Scripts\activate`. See `agent/README.md`'s Install section for
the PowerShell execution-policy note if `Activate.ps1` is blocked.)

Set up an API key the same way you would for `secdogie-agent` (env var,
`secdogie-agent --init-config`, or — simplest on Windows — a `secdogie.env`
text file in the current folder; see `agent/README.md`) before running.

### Or: a single-file executable (no Python needed)

```sh
./packaging/build.sh          # Linux/macOS -- produces packaging/dist/secdogie-open
./packaging/dist/secdogie-open
```

**Windows (PowerShell):**
```powershell
packaging\build.ps1          # produces packaging\dist\secdogie-open.exe
.\packaging\dist\secdogie-open.exe
```
(cmd.exe can't run `.ps1` files directly: `powershell -ExecutionPolicy Bypass -File packaging\build.ps1`.)

## Run

```sh
secdogie-open
```

1. The window list populates automatically (Refresh re-scans). Windows
   smaller than 60px on an edge, minimized, or untitled are filtered out.
2. Enter one task -- it's sent to every window you select.
3. Pick a model (default `claude-sonnet-5`) and max steps.
4. Leave **Enable real actions** off to dry-run first: every selected
   window's agent reasons and logs what it would do, but never touches the
   mouse/keyboard. Turn it on only once you trust the task, against windows/
   machines you fully control -- with several windows running unattended at
   once, there's no per-step y/N prompt (it wouldn't make sense across
   multiple windows sharing one terminal), so review carefully in dry-run
   first.
5. **Start selected** launches one thread per selected window; **Stop all**
   asks every running window to stop before its next step (in-flight
   actions still finish).

## Known limitations

- Window enumeration uses [PyWinCtl](https://github.com/Kalmat/PyWinCtl).
  On Linux this needs an X11 session -- Wayland blocks listing other
  applications' windows for isolation reasons, so window discovery won't
  work there.
- All windows share one API key/provider today; there's no per-window key
  assignment or coordinating "dispatcher" yet.
- Stopping is cooperative (checked once per step), not instant -- an
  in-progress click/type finishes before a stop takes effect.
