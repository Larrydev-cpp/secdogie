# Building a single-file `secdogie-agent` executable

This packages the agent into one standalone executable that bundles Python
and every dependency, so an end user can run it **without installing Python
or `pip install`-ing anything** — they download one file and run it.

## Build

From this directory (or run the script from anywhere):

**Linux/macOS:**
```sh
./build.sh
```

**Windows (PowerShell):**
```powershell
.\build.ps1
```
(cmd.exe can't run `.ps1` files directly: `powershell -ExecutionPolicy Bypass -File build.ps1`.)

Or manually — Linux/macOS:

```sh
cd agent
python3 -m venv .build-venv && source .build-venv/bin/activate
pip install -e . pyinstaller
cd packaging
pyinstaller secdogie-agent.spec
```

Windows (PowerShell):

```powershell
cd agent
python -m venv .build-venv
.build-venv\Scripts\Activate.ps1
pip install -e . pyinstaller
cd packaging
pyinstaller secdogie-agent.spec
```

(If `Activate.ps1` refuses to run with a "running scripts is disabled" error,
run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` first — that
only affects the current window, no admin needed. cmd uses
`.build-venv\Scripts\activate.bat` instead.)

Output:

| Platform | File |
|----------|------|
| Linux    | `packaging/dist/secdogie-agent` |
| macOS    | `packaging/dist/secdogie-agent` |
| Windows  | `packaging/dist/secdogie-agent.exe` |

## Important: binaries are platform-specific

PyInstaller does **not** cross-compile. The binary is tied to the OS and CPU
architecture it was built on (a Linux x86_64 build runs only on Linux
x86_64, etc.). To ship binaries for multiple platforms, run the build once
on each — a Linux box, a Mac, a Windows machine (or CI runners for each, see
the repo's CI if configured).

## Running the built binary

**Linux/macOS:**
```sh
export ANTHROPIC_API_KEY=sk-...
./dist/secdogie-agent "open a text editor and type hello" --dry-run
```

**Windows:** either set the env var (PowerShell: `$env:ANTHROPIC_API_KEY =
"sk-..."`; cmd: `set ANTHROPIC_API_KEY=sk-...`), or — simpler, no shell syntax
to remember — create a `secdogie.env` text file next to `secdogie-agent.exe`
containing `ANTHROPIC_API_KEY=sk-...`; it's the first place the binary looks
for a key. Then:
```
.\dist\secdogie-agent.exe "open a text editor and type hello" --dry-run
```

It must run in a **graphical desktop session** — it screenshots and drives
the mouse/keyboard, so it can't do anything useful over plain SSH to a
headless server. If there's no display it exits with a clear message
(exit code 4) rather than a stack trace.

## GUI mode (--gui) and tkinter

The agent's `--gui` dialogs use tkinter. PyInstaller bundles tkinter
automatically **only if it's importable on the build machine**. Standard
python.org builds for Windows and macOS include it; on Linux install it first
(`sudo apt install python3-tk`) before building, or the resulting binary will
just fall back to terminal mode. GUI mode also needs a display at run time.

## What's in here

- `entry.py` — thin launcher PyInstaller freezes (forwards to `secdogie_agent.cli:main`).
- `secdogie-agent.spec` — the PyInstaller build recipe (bundles `anthropic`,
  `pyautogui`, `mss` and their hidden imports; produces a one-file console exe).
- `build.sh` / `build.ps1` — convenience wrappers (Linux/macOS and Windows,
  respectively) that set up an isolated build venv and run PyInstaller.

## Notes on size and startup

A one-file build is ~25–30 MB (it embeds the Python runtime) and unpacks to
a temp dir on first launch, so it starts a beat slower than a normal
program. If that matters, drop `--onefile` behavior by switching the spec to
a `COLLECT` (one-folder) build — faster startup, but it ships as a folder
instead of a single file.

## macOS notes (Apple Silicon + Intel)

### Gatekeeper / quarantine

Downloaded binaries are often quarantined. Symptoms: double-click does nothing,
or a dialog saying the app cannot be opened.

Fix once:

```sh
xattr -d com.apple.quarantine ./secdogie-agent
chmod +x ./secdogie-agent ./open.command
```

Or right-click the binary → **Open** → **Open**.

The included `open.command` launcher already tries to strip the quarantine
attribute automatically.

### Accessibility permission (required for `--desktop-ax`)

Element-level control uses the macOS Accessibility (AX) API. The *host process*
that launches the binary (Terminal, iTerm, or the binary itself when double-clicked)
must be listed under:

**System Settings → Privacy & Security → Accessibility**

Without this permission the AX framework loads but returns an empty tree; the
agent silently falls back to pure screenshot + mouse/keyboard control.

### Architecture

| Runner / machine | Asset name |
|------------------|------------|
| Apple Silicon (M1/M2/M3/…) | `agent-macos-arm64` |
| Intel Mac | `agent-macos-x86_64` |

PyInstaller does **not** cross-compile. Build (or download) the binary that
matches the CPU you will run on. Rosetta 2 can run the x86_64 binary on Apple
Silicon, but native arm64 is preferred.

### Local build on a Mac

```sh
cd agent
./packaging/build.sh
# → packaging/dist/secdogie-agent
```

Then copy `packaging/launchers/open.command` next to the binary for a
double-click friendly start.
