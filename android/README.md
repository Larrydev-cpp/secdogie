# secdogie-android

Point [`secdogie-agent`](../agent) at an **Android phone** instead of the
desktop it's running on. Same idea — screenshot, ask a vision model for one
action, execute it, repeat — but the screen comes from `adb screencap` and
the taps/typing go out through `adb shell input`, so no app is installed on
the phone: it's driven entirely over the Android Debug Bridge.

> Read `agent/README.md`'s safety section first — it all applies here. This
> drives a real phone: real taps, real typing, real `am start` intents. Only
> point it at a device you own. Start with `--dry-run` and keep the default
> per-step confirmation on until you trust a task.

## How it works

It reuses `secdogie-agent`'s loop and its provider-agnostic action schema
unchanged; only the *backend* (how a screenshot is taken and how an action is
carried out) is swapped. `AdbBackend` maps the schema onto `adb`:

| Action        | adb                                             |
|---------------|-------------------------------------------------|
| `left_click`  | `input tap x y`                                 |
| `double_click`| two `input tap`                                 |
| `right_click` | `input swipe x y x y 600` (long-press)          |
| `drag`        | `input swipe x1 y1 x2 y2`                        |
| `scroll`      | `input swipe` in the opposite direction         |
| `type`        | `input text` (ASCII; see limitations)           |
| `key`         | `input keyevent KEYCODE_*`                       |
| `hold_key`    | `input keyevent --longpress`                     |
| `open`        | `am start -a android.intent.action.VIEW -d …`   |
| `move`        | no-op (a touchscreen has no hover cursor)       |

Screenshots use `adb exec-out screencap -p`, which returns the device's true
pixel resolution; the agent loop scales the model's coordinates back to those
pixels, so taps land where intended.

## Setup

1. **Install adb** (Android platform-tools) and make sure `adb` runs:
   - Linux: `sudo apt install adb` (or download platform-tools from Google)
   - macOS: `brew install android-platform-tools`
   - Windows: install platform-tools and add it to `PATH`
2. **Enable USB debugging** on the phone (Settings → Developer options → USB
   debugging), plug it in, and accept the "Allow USB debugging?" prompt.
   Wireless debugging works too — `adb connect <ip>:<port>` first.
3. Verify the device shows up:
   ```sh
   adb devices        # should list your device in the `device` state
   ```

## Install

```sh
cd android
python3 -m venv .venv && source .venv/bin/activate
pip install -e ../agent      # the loop/providers/config live here
pip install -e .
```

Set up an API key exactly as for `secdogie-agent` (env var or
`secdogie-android --init-config`).

### Or: a single-file executable (no Python needed)

```sh
./packaging/build.sh          # produces packaging/dist/secdogie-android
./packaging/dist/secdogie-android --help
```

`adb` itself is a separate system tool the binary still needs on `PATH` at
run time (see Setup above) -- PyInstaller bundles the Python side, not `adb`.

## Run

```sh
secdogie-android "open the Clock app and start a 5 minute timer"
secdogie-android "..." --device <serial>     # when several devices are attached
secdogie-android "..." --dry-run             # reason + log actions, never touch the phone
secdogie-android "..." --watch               # act only when a condition on screen occurs
```

Flags mirror `secdogie-agent` (`--model`, `--provider`, `--auto`, `--grid`,
`--max-steps`, `--watch`, …), plus `--device`/`--adb-path` for adb targeting.

## Element targeting (`--snap-to-elements`)

By default the agent taps the raw pixel the model picked from the screenshot.
With `--snap-to-elements` it also reads the on-screen **UI hierarchy**
(`uiautomator dump`) and snaps each tap onto the real widget under that point —
the RPA way of hitting things by identity instead of by pixel guess:

```sh
secdogie-android "open the overflow menu and tap Settings" --snap-to-elements
```

Each tap resolves to the tightest *clickable* widget whose box contains the
model's point, and the tap is moved to that widget's center. To avoid grabbing
a full-screen backdrop the point merely falls inside, snapping is skipped when
the widget covers more than a quarter of the screen (tunable). If a screen
can't be dumped (some secure views block it), it silently falls back to the raw
coordinate, so turning this on never makes a tap worse — only more precise.

This reads the widget tree the same way real RPA tools and screen readers do,
so buttons/menu items get hit reliably even when the model's aim is a few pixels
off. (The tree also drives `AdbBackend.find_element(...)`, a seam for future
select-by-name actions.)

## Known limitations

- **ASCII typing only.** `adb shell input text` can't emit Unicode (Chinese,
  emoji, accents). Non-ASCII `type` actions are skipped with a message; to
  type Unicode, install an on-device IME such as ADBKeyBoard (out of scope
  here).
- **No modifier combos.** Phone key events are sent in sequence; there's no
  true simultaneous `ctrl+c`-style chord over `adb input`.
- **`scroll` distance is approximate** — each scroll becomes a fixed-length
  swipe in the indicated direction, not a precise pixel amount.
- The screen must be **on and unlocked** for `screencap` to return a usable
  image.
