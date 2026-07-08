# secdogie — hands-on tutorial

A follow-along guide that takes you from a fresh clone to a model actually
driving a screen — first your own desktop, then a phone, several windows at
once, and finally a machine across the network. Every step lists the exact
command and what you should see.

Work through **Part 1 first** — the desktop agent is the core; every other
part reuses the same loop and API-key setup.

> **Safety, once, up front.** These tools take real actions — a real mouse,
> real keystrokes, real taps on a real phone. Only ever point them at a
> device you own or are authorized to control. In every part below you'll
> run `--dry-run` first (nothing is touched) and keep the per-step
> confirmation on until you trust a task. Slam the mouse into a screen corner
> to abort a desktop run (pyautogui's fail-safe).

## Contents

- [Part 0 — Prerequisites](#part-0--prerequisites)
- [Part 1 — Your first run: control the desktop](#part-1--your-first-run-control-the-desktop)
- [Part 2 — A real task, and aiming better](#part-2--a-real-task-and-aiming-better)
- [Part 3 — Watch mode: wait for something, then act](#part-3--watch-mode-wait-for-something-then-act)
- [Part 4 — Control an Android phone](#part-4--control-an-android-phone)
- [Part 5 — Control an iPhone](#part-5--control-an-iphone)
- [Part 6 — Drive several windows at once](#part-6--drive-several-windows-at-once)
- [Part 7 — Reach a machine across the network](#part-7--reach-a-machine-across-the-network)
- [Troubleshooting](#troubleshooting)

---

## Part 0 — Prerequisites

- **Python 3.10+** and **git**.
- A **graphical desktop session** for Part 1 (X11, or Wayland if your
  compositor works with `mss`/`pyautogui`). It won't do anything useful over
  SSH to a headless box.
- An **API key** for a vision-capable model: an **Anthropic** key for the
  default `claude-*` models (get one at <https://console.anthropic.com/>), or
  an **OpenAI** key for `gpt-*` / o-series models.

```sh
git clone https://github.com/Larrydev-cpp/secdogie.git
cd secdogie
```

---

## Part 1 — Your first run: control the desktop

### 1.1 Install

```sh
cd agent
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

Check it runs:

```sh
secdogie-agent --help
```

### 1.2 Add your API key

Create a config file once and fill in your key:

```sh
secdogie-agent --init-config     # writes ~/.config/secdogie/config (chmod 600)
```

Open `~/.config/secdogie/config` and set the line for your provider:

```ini
ANTHROPIC_API_KEY=sk-ant-...
# OPENAI_API_KEY=sk-...            # only if you use gpt-* / o-series models
# SECDOGIE_MODEL=claude-sonnet-5   # optional: change the default model
```

(You can instead export `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`, or pass
`--api-key` on the command line — the CLI flag wins, then the env var, then
this file.)

### 1.3 Dry run — see what it *would* do, touching nothing

Always start here. `--dry-run` still calls the model each step and logs the
action it picked, but never moves the mouse or types:

```sh
secdogie-agent "open a text editor and type 'hello world'" --dry-run
```

You'll see something like (timestamps abbreviated):

```
INFO task: open a text editor and type 'hello world'
INFO running with --dry-run: actions will be logged but not executed
INFO step 1/50: left_click (the Text Editor icon in the dock)
INFO [dry-run] would execute: {'action': 'left_click', 'x': 512, 'y': 1390, 'reasoning': 'open the editor'}
INFO step 2/50: type (type the requested text)
INFO [dry-run] would execute: {'action': 'type', 'text': 'hello world', 'reasoning': '...'}
INFO step 3/50: done (the text has been typed)
INFO done: typed 'hello world' into the editor
```

If that plan looks sane, move on. If the coordinates or steps look wrong, see
[aiming better](#part-2--a-real-task-and-aiming-better).

### 1.4 Real run — you approve every action

Drop `--dry-run`. Now each action pauses for a `y/N` confirmation (default is
**No**, so just pressing Enter skips an action):

```sh
secdogie-agent "open a text editor and type 'hello world'"
```

```
INFO step 1/50: left_click (the Text Editor icon in the dock)
Execute left_click({'action': 'left_click', 'x': 512, 'y': 1390, 'reasoning': 'open the editor'})? [y/N] y
INFO step 2/50: type (type the requested text)
Execute type({'action': 'type', 'text': 'hello world', 'reasoning': '...'})? [y/N] y
INFO done: typed 'hello world' into the editor
```

Type `y` + Enter to let an action run; anything else skips it and the model
sees "skipped (user declined)" and adapts.

### 1.5 Hands-off — `--auto`

Once you trust a task, `--auto` removes the per-step prompt and runs to
completion. Only do this while watching, on a machine you can grab back:

```sh
secdogie-agent "open a text editor and type 'hello world'" --auto
```

**You've done the core loop.** Everything below is the same loop pointed at a
different screen, or reached a different way.

---

## Part 2 — A real task, and aiming better

Vision models reason about a *downscaled* copy of big screenshots, so on
cluttered or high-resolution screens the coordinates can drift. Two knobs fix
almost all of it:

```sh
# overlay a labeled coordinate grid so the model has anchor points
secdogie-agent "in the settings window, turn on Dark Mode" --grid

# keep small text legible by sending a larger image (slower/costlier)
secdogie-agent "..." --max-image-edge 2000
```

`--gui` is nice for a first real task: it shows the model's **plan** in a
popup *before* it touches anything, so you approve the approach up front:

```sh
secdogie-agent --gui "rename the file report.txt to report-final.txt"
```

Full list of accuracy/behavior flags is in
[`agent/README.md`](agent/README.md#click-accuracy).

---

## Part 3 — Watch mode: wait for something, then act

`--watch` turns the agent into a monitor. It polls the screen and does
**nothing** until the situation you described appears, then acts once:

```sh
secdogie-agent --watch "when a red 'BUILD FAILED' banner appears, open /home/me/build.log"
```

```
INFO watch mode: polling every 2.0s until the trigger condition occurs
INFO watching (step 1): no trigger yet
INFO watching (step 2): no trigger yet -- still building
INFO step 3/100000: open (the BUILD FAILED banner is now visible)
```

- `--watch-interval 5` slows the polling to every 5s.
- Add `--auto` for fully unattended monitoring (no confirmation when it
  finally triggers).

---

## Part 4 — Control an Android phone

Same loop, but screenshots come from `adb screencap` and taps go out through
`adb shell input` — **nothing is installed on the phone.**

### 4.1 Enable adb

1. Install Android platform-tools (they ship `adb`): `sudo apt install adb`
   (Linux), `brew install android-platform-tools` (macOS), or Google's
   download on Windows.
2. On the phone: Settings → Developer options → **USB debugging** on, plug in
   over USB, accept the "Allow USB debugging?" prompt.
3. Confirm it's visible:

```sh
adb devices        # your device should be listed in the `device` state
```

### 4.2 Install

```sh
cd android
python3 -m venv .venv && source .venv/bin/activate
pip install -e ../agent      # the loop/providers/config live here
pip install -e .
```

Use the **same API key setup** as Part 1.2 (`secdogie-android --init-config`,
env var, or `--api-key`).

### 4.3 First run

Dry-run first, then for real:

```sh
secdogie-android "open the Clock app and start a 5 minute timer" --dry-run
secdogie-android "open the Clock app and start a 5 minute timer"
secdogie-android "..." --device <serial>    # only if several devices are attached
```

### 4.4 More reliable taps: element snapping

By default the agent taps the raw pixel the model picked. Add
`--snap-to-elements` and it also reads the phone's UI hierarchy
(`uiautomator dump`) and snaps each tap onto the real button/menu-item under
that point — the RPA way of hitting things by identity, not pixel guess:

```sh
secdogie-android "open the overflow menu and tap Settings" --snap-to-elements
```

It only snaps onto control-sized widgets (never a full-screen backdrop) and
falls back to the raw coordinate if a screen can't be dumped, so turning it on
never makes a tap worse. Details: [`android/README.md`](android/README.md#element-targeting---snap-to-elements).

---

## Part 5 — Control an iPhone

iOS won't let a host inject input without an on-device agent, so this path
uses [WebDriverAgent](https://github.com/appium/WebDriverAgent) (WDA), which
you **build once with Xcode** on a Mac and leave running on the phone. After
that it's the same loop over WDA's HTTP API.

The setup (Xcode signing, launching WDA, `iproxy` port-forwarding) is
step-by-step in [`ios/README.md`](ios/README.md#setup-one-time-needs-a-mac--xcode).
Once `http://127.0.0.1:8100/status` returns WDA's status JSON:

```sh
cd ios
python3 -m venv .venv && source .venv/bin/activate
pip install -e ../agent && pip install -e .
secdogie-ios "open Settings and turn on Airplane Mode" --dry-run
secdogie-ios "open Settings and turn on Airplane Mode"
```

> **Not sure you need this?** If your goal is to *trigger predefined actions*
> on a schedule (send a message, run a workflow) rather than have the model
> visually drive arbitrary apps, an iOS Shortcut hitting a small backend is
> far simpler and needs no Mac. WDA is for genuine "see the screen, control
> any app" automation.

---

## Part 6 — Drive several windows at once

`secdogie-open` is a GUI that lists your open windows, lets you select
several, and runs one agent per selected window — each scoped to just that
window, so they don't collide.

```sh
cd open
python3 -m venv .venv && source .venv/bin/activate
pip install -e ../agent && pip install -e .
secdogie-open
```

1. Pick windows from the auto-populated list (Refresh re-scans).
2. Type one task applied to each selected window.
3. It **defaults to dry-run** — leave "Enable real actions" off for your first
   try. Because several windows run unattended together, there's no per-step
   prompt once real actions are on, so trust the task in dry-run first.
4. "Stop all" halts every running window.

More: [`open/README.md`](open/README.md).

---

## Part 7 — Reach a machine across the network

To let the agent (or a cloud model) drive a *different* machine — your home
desktop, a phone on another network — put an encrypted tunnel between the two
boxes with `secdogie-tunnel`, then run the agent so it targets the remote
screen over that tunnel.

### 7.1 Build

```sh
cd tunnel
sudo apt-get install -y build-essential cmake libsodium-dev pkg-config
cmake -S . -B build && cmake --build build -j
./build/test_protocol        # sanity-check the crypto/handshake
```

### 7.2 Point-to-point (two machines)

Generate a key on each machine, exchange the printed **public** keys, and
write a config on each side:

```sh
./build/secdogie-tunnel genkey server.key    # prints its public_key
./build/secdogie-tunnel genkey client.key
```

**Server** (the machine with a reachable address), `server.conf`:

```ini
private_key     = <server private key>
peer_public_key = <client public key>
address         = 10.66.0.1/24
listen_port     = 51820
```

**Client**, `client.conf`:

```ini
private_key     = <client private key>
peer_public_key = <server public key>
address         = 10.66.0.2/24
endpoint        = <server ip or hostname>:51820
```

```sh
sudo ./build/secdogie-tunnel server server.conf   # on the server
sudo ./build/secdogie-tunnel client client.conf   # on the client
```

When both log `handshake completed`, the two machines can reach each other on
`10.66.0.x`. Now run the agent to drive the remote box over that virtual
network (e.g. via VNC/RDP/X11 carried inside the tunnel, or run the agent
directly on the remote machine and use the tunnel just to reach it).

### 7.3 Hub (one node, many machines)

To reach *several* machines through one public node — a controller driving
many agent boxes — run that node as a **hub** instead. Clients are unchanged;
only the hub gets a multi-peer config:

```ini
# hub.conf
private_key = <hub private key>
address     = 10.66.0.1/24
listen_port = 51820
peer = <client 1 public key> 10.66.0.2
peer = <client 2 public key> 10.66.0.3
```

```sh
sudo ./build/secdogie-tunnel hub hub.conf
```

Each client dials in with its own handshake; the hub routes packets between
them by tunnel IP. Full design and its security caveat (a hub decrypts to
route, so it can see inter-client traffic) is in
[`tunnel/README.md`](tunnel/README.md#hub-mode-one-node-many-clients) and
[`tunnel/PROTOCOL.md`](tunnel/PROTOCOL.md).

---

## Troubleshooting

| Symptom | Likely cause & fix |
|---------|--------------------|
| `no API key found for the <provider> provider` | No key resolved. Run `--init-config` and fill it in, export `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`, or pass `--api-key`. |
| `pyautogui unavailable ...; only --dry-run will work` | No usable display (headless/SSH/Wayland issue). Run on a real desktop session; `--dry-run` still works anywhere. |
| Clicks land slightly off-target | Add `--grid`, raise `--max-image-edge`, or on Android add `--snap-to-elements`. |
| Every action is skipped without asking | stdin isn't a terminal, so confirmation fails closed (No). Use `--auto` for unattended, or run in a real terminal. |
| Agent stops after 50 steps | Hit the default `--max-steps`; raise it, or the task may be under-specified. |
| Android: `adb ... device offline` / not listed | Reconnect USB, re-accept the debugging prompt, `adb kill-server && adb start-server`, check `adb devices`. |
| Android: `uiautomator dump returned no hierarchy` | Some secure screens block dumping; snapping silently falls back to raw taps — nothing to fix. |
| iOS: `could not reach WebDriverAgent` | WDA isn't running or not forwarded. Relaunch WDA and `iproxy 8100 8100`; check `http://127.0.0.1:8100/status`. |
| Tunnel: `tun create failed ... are you root / CAP_NET_ADMIN?` | Creating a TUN device needs privilege. Run with `sudo`, or `sudo setcap cap_net_admin+ep build/secdogie-tunnel`. |

Each subproject's README has deeper reference docs; this tutorial is the
happy-path walkthrough that ties them together.
