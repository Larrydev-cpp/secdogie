# secdogie-open

A local web page on top of [`secdogie-agent`](../agent): it lists every open
window on your desktop, lets you pick several, and runs one agent instance
per selected window at once -- each scoped to just that window's screen
region, so clicks/typing from one window's agent can't land on another.

Running it starts a small HTTP server bound to `127.0.0.1` only (never a
public interface) and opens the page in your normal browser -- no separate
GUI toolkit, no webview engine to install. The dark glass-surface look
follows [OpenClaw](https://github.com/openclaw/openclaw)'s published design
system (exact color tokens, blur tiers, and motion values from its
`ui/docs/design-system/`) so this reads as the same visual language as
OpenClaw's own control UI, scaled down to what a one-page picker needs.

> Read `agent/README.md`'s safety section first -- everything there applies
> here too, times however many windows you select at once.

## Why

`secdogie-agent` alone drives the whole primary monitor with one task at a
time. This splits the screen by window instead, so several tasks can run
concurrently against different apps. It's step one toward running each
window's agent off its own API key (avoiding one key's rate limit under
higher concurrency); today every window in a run shares the one model and key
you set on the page (or, as a fallback, whatever `secdogie-agent`'s own config
resolution finds — `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` or a config file, see
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

You don't need to set up a key on the command line first: the page has an
**API key** field you paste your key into (it's stored only in your browser and
sent to the local server). If you'd rather not paste it each machine, the older
routes still work as a fallback — an env var, `secdogie-agent --init-config`,
or a `secdogie.env` file; see `agent/README.md`.

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
secdogie-open                # opens the page in your default browser
secdogie-open --no-browser   # print the URL instead (e.g. over SSH with a forwarded port)
secdogie-open --port 8734    # bind a fixed port instead of picking a free one
```

1. The window list populates automatically (Refresh re-scans). Windows
   smaller than 60px on an edge, minimized, or untitled are filtered out.
2. Enter one task -- it's sent to every window you select.
3. Pick a **model** from the dropdown (Claude and GPT options, or **Custom…**
   to type any model id / `provider/model` ref) and set max steps. Paste an
   **API key** for that model's provider into the key field, or leave it blank
   to fall back to the env var / config file. The model and key are remembered
   in your browser so you don't retype them next launch.
4. Leave **Enable real actions** off to dry-run first: every selected
   window's agent reasons and logs what it would do, but never touches the
   mouse/keyboard. Turn it on only once you trust the task, against windows/
   machines you fully control -- with several windows running unattended at
   once, there's no per-step y/N prompt (it wouldn't make sense across
   multiple windows sharing one browser tab), so review carefully in dry-run
   first; the page shows a native confirm dialog restating that warning
   before it will actually enable real actions.
5. **Start selected** launches one thread per selected window; **Stop all**
   asks every running window to stop before its next step (in-flight
   actions still finish). Each window's live status polls in the page every
   ~0.7s; the run's own step-by-step log still prints to the terminal you
   launched `secdogie-open` from.
6. Close the tab and press Ctrl+C in that terminal to stop the server.

## Known limitations

- Window enumeration uses [PyWinCtl](https://github.com/Kalmat/PyWinCtl).
  On Linux this needs an X11 session -- Wayland blocks listing other
  applications' windows for isolation reasons, so window discovery won't
  work there.
- All windows share one API key/provider today; there's no per-window key
  assignment or coordinating "dispatcher" yet.
- **One physical cursor.** Every window's agent drives the *same* mouse and
  keyboard, so their actions are **serialized** (a shared input lock in
  `secdogie-agent`): each click completes atomically and they never corrupt
  each other's cursor position, but they also can't truly click *simultaneously*
  on one desktop — the models perceive their windows in parallel, the actions
  take turns. For genuinely parallel action, drive separate devices
  (`secdogie-android`/`secdogie-ios`, each with its own input channel) or
  separate machines over the tunnel.
- Stopping is cooperative (checked once per step), not instant -- an
  in-progress click/type finishes before a stop takes effect.
- The server has no auth -- anyone who can reach `127.0.0.1:<port>` on this
  machine (any local user/process) can drive it. Fine for its intended use
  (you, on your own desktop); don't port-forward it to an untrusted network.

## Layout

```
secdogie_open/
  windows.py       enumerate open windows (PyWinCtl), filter to real app windows
  runner.py        one agent thread per window, posts (id, status, detail) to a queue
  controller.py    pure-Python state layer: windows/runner + a status snapshot, no HTTP/GUI import
  server.py        stdlib http.server: static webui/ + a small JSON API over Controller
  cli.py           argument parsing, binds the server, opens the browser
  webui/
    index.html      page structure (task form, window list, banner)
    style.css       openclaw-derived dark glass-surface theme (also ships a light variant)
    app.js          fetch()-based client: render windows, poll status, wire up actions
tests/              unit tests for windows/runner/controller (no display or browser needed)
```
