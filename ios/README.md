# secdogie-ios

Point [`secdogie-agent`](../agent) at an **iPhone or iPad**. Same loop —
screenshot, ask a vision model for one action, execute it, repeat — but the
screen and input go through [WebDriverAgent](https://github.com/appium/WebDriverAgent)
(WDA), the standard on-device automation server that Appium uses.

Unlike Android (where `adb` needs nothing installed on the phone), iOS won't
let a host inject input without an on-device agent. So the one-time cost here
is **building and launching WDA with Xcode**; after that, this drives the phone
over WDA's HTTP API and installs nothing else.

> Read `agent/README.md`'s safety section first — it all applies. This drives
> a real device: real taps, real typing. Only point it at a device you own.
> Start with `--dry-run` and keep per-step confirmation on until you trust a
> task.

## How it works

It reuses `secdogie-agent`'s loop and action schema unchanged; only the
*backend* is swapped. `IosBackend` maps the schema onto WDA endpoints:

| Action        | WebDriverAgent                                        |
|---------------|-------------------------------------------------------|
| `left_click`  | `POST /wda/tap` `{x, y}`                               |
| `double_click`| `POST /wda/doubleTap`                                  |
| `right_click` | `POST /wda/touchAndHold` (press-and-hold)             |
| `drag`        | `POST /wda/dragfromtoforduration`                     |
| `scroll`      | a drag in the opposite direction                      |
| `type`        | `POST /wda/keys` (Unicode OK — types into focus)      |
| `key`         | `pressButton` (home/volume) or typed characters       |
| `open`        | `POST /url` `{url}`                                    |
| `move`        | no-op (a touchscreen has no hover cursor)             |

Screenshots come from `GET /screenshot`. **Coordinate note:** WDA screenshots
are in device *pixels* but its tap/drag API takes *points* (the Retina 2×/3×
scale). Each frame, the backend reads `GET /window/size` (points) alongside the
screenshot (pixels) to get that ratio and converts model coordinates down to
points before tapping — so taps land where intended.

## Setup (one time, needs a Mac + Xcode)

1. **Get WebDriverAgent and open it in Xcode.** The simplest path is to install
   [Appium](https://appium.io/) and use its bundled WDA, or clone
   `appium/WebDriverAgent`. Open `WebDriverAgent.xcodeproj`.
2. **Sign it:** select the `WebDriverAgentRunner` target → Signing & Capabilities
   → pick your Apple ID team (a free account works for personal devices).
3. **Trust the developer cert on the phone** (Settings → General → VPN & Device
   Management) and make sure the device has Developer Mode enabled (iOS 16+).
4. **Launch WDA on the device**, e.g.:
   ```sh
   xcodebuild -project WebDriverAgent.xcodeproj \
     -scheme WebDriverAgentRunner -destination 'id=<your-device-udid>' test
   ```
   WDA prints a line like `ServerURLHere->http://…:8100<-ServerURLHere`.
5. **Forward the port to your Mac** with `iproxy` (from `libimobiledevice`):
   ```sh
   brew install libimobiledevice
   iproxy 8100 8100
   ```
   Now `http://127.0.0.1:8100/status` should return WDA's status JSON.

## Install

**Linux/macOS/WSL** (the setup above needs a Mac for Xcode, but the client
below runs on any OS, including Windows):
```sh
cd ios
python3 -m venv .venv && source .venv/bin/activate
pip install -e ../agent      # the loop/providers/config live here
pip install -e .
```

**Windows (PowerShell):**
```powershell
cd ios
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ..\agent
pip install -e .
```
(cmd: `.venv\Scripts\activate`. See `agent/README.md`'s Install section for
the PowerShell execution-policy note if `Activate.ps1` is blocked.)

Set up an API key exactly as for `secdogie-agent` (env var,
`secdogie-ios --init-config`, or — simplest on Windows — a `secdogie.env`
text file in the current folder).

### Or: a single-file executable (no Python needed)

```sh
./packaging/build.sh          # Linux/macOS -- produces packaging/dist/secdogie-ios
./packaging/dist/secdogie-ios --help
```

**Windows (PowerShell):**
```powershell
packaging\build.ps1          # produces packaging\dist\secdogie-ios.exe
.\packaging\dist\secdogie-ios.exe --help
```
(cmd.exe can't run `.ps1` files directly: `powershell -ExecutionPolicy Bypass -File packaging\build.ps1`.)

(WDA itself only runs on the iPhone/Mac side; the `secdogie-ios` client that
talks to it over HTTP works from any OS, including Windows.)

WDA itself still needs to be running and port-forwarded (see Setup above) --
this only bundles the Python side.

## Run

```sh
secdogie-ios "open Settings and turn on Airplane Mode"
secdogie-ios "..." --dry-run                 # reason + log actions, never touch the device
secdogie-ios "..." --wda-url http://127.0.0.1:8100
secdogie-ios "..." --watch                   # act only when a condition on screen occurs
```

Flags mirror `secdogie-agent` (`--model`, `--provider`, `--auto`, `--grid`,
`--max-steps`, `--watch`, …), plus `--wda-url` for the WDA server.

## Tap timing (`--humanize-taps`)

WDA's `/wda/tap` has no duration knob — a real finger's contact time varies, this
endpoint's doesn't. `--humanize-taps` issues each **single** tap instead as
`touchAndHold` for a short randomized 50–140ms duration (the same primitive
`right_click`'s long-press already uses, just much shorter):

```sh
secdogie-ios "..." --humanize-taps
```

**What this changes:** the tap stops having a perfectly instantaneous, always-
identical duration — useful only if something is specifically looking at that.

**What this does not change, and cannot:** `touchAndHold` maps to XCUITest's
`press(forDuration:)`, a **different underlying gesture** from `tap()` — this
is a timing tweak riding on the closest available primitive, not a faithful
recreation of a real touch event, and it still goes through the same
WDA-automation path the OS/apps can identify as automated (accessibility/
XCTest driven) regardless of duration. It does not touch, and cannot defeat,
device attestation (e.g. App Attest/DeviceCheck) or any check operating below
the WDA layer. `--humanize-taps` **never** applies to `double_click`: WDA's
`doubleTap` is its own distinct gesture, and substituting two `touchAndHold`
calls risks the app not registering a double-tap at all rather than just
changing its timing.

## Known limitations

- **WDA must be running** and reachable at `--wda-url` (forward it with
  `iproxy`). Building/launching it needs a Mac with Xcode; that part isn't
  automated here.
- **Typing needs a focused text field** — `type` sends to whatever currently
  has keyboard focus. Unicode works (unlike Android's adb).
- **No timed key-holds and few hardware keys** — iOS exposes only home/volume
  buttons via WDA; `hold_key` presses once and can't honor a duration.
- **`scroll` distance is approximate** — each scroll becomes a fixed-length
  swipe in the indicated direction.
- The screen must be **on and unlocked** for `screenshot` to return a usable
  image.
