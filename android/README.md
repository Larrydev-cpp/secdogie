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
4. **On MIUI/Xiaomi (and often other Chinese ROMs), one more toggle.** Plain
   USB debugging is enough for `adb devices`/screenshots, but taps/typing use
   input *injection*, which MIUI gates behind a separate **"USB debugging
   (Security settings)"** toggle in Developer options. That toggle only
   appears, and can only be turned on, after you **sign in to a Mi account on
   the phone** (Settings → your name/Mi Account) — it is not a root
   requirement. Without it, every tap/swipe/type/key fails with a clear error
   from this tool naming the fix (see Troubleshooting). Root is only relevant
   as a workaround for people who genuinely cannot sign in to a Mi account
   (editing `remote_provider_preferences.xml` via a rooted shell); it is not
   the normal path. Other Chinese ROMs (EMUI/HarmonyOS, ColorOS, OriginOS)
   often have a similarly named extra security toggle alongside plain USB
   debugging — look for it in Developer options if taps fail the same way.

## Install

**Linux/macOS:**
```sh
cd android
python3 -m venv .venv && source .venv/bin/activate
pip install -e ../agent      # the loop/providers/config live here
pip install -e .
```

**Windows (PowerShell):**
```powershell
cd android
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ..\agent
pip install -e .
```
(cmd: `.venv\Scripts\activate`. See `agent/README.md`'s Install section for
the PowerShell execution-policy note if `Activate.ps1` is blocked.)

Set up an API key exactly as for `secdogie-agent` (env var,
`secdogie-android --init-config`, or — simplest on Windows — a
`secdogie.env` text file in the current folder).

### Or: a single-file executable (no Python needed)

```sh
./packaging/build.sh          # Linux/macOS -- produces packaging/dist/secdogie-android
./packaging/dist/secdogie-android --help
```

**Windows (PowerShell):**
```powershell
packaging\build.ps1          # produces packaging\dist\secdogie-android.exe
.\packaging\dist\secdogie-android.exe --help
```
(cmd.exe can't run `.ps1` files directly: `powershell -ExecutionPolicy Bypass -File packaging\build.ps1`.)

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

## RPA macros (`--macro`): record once, replay for free

Same `--macro PATH` flag and behavior as `secdogie-agent` (see
`agent/README.md`'s "RPA macros" section for the full model) — the first run
against a task drives it live and saves the sequence to PATH on success;
every run after that replays from PATH with **zero model calls**, falling
back to the live model the instant a step can't be resolved.

```sh
secdogie-android "open the overflow menu and tap Settings" --macro settings.json --auto
secdogie-android "open the overflow menu and tap Settings" --macro settings.json --auto   # replays -- no model calls
```

What's Android-specific: every tap step is recorded against the **UI
element itself** — resource-id, text, content-desc, and class read from the
uiautomator hierarchy at the moment of the tap (the same hierarchy
`--snap-to-elements` reads) — not a frozen pixel. On replay, that element is
re-found by identity on the current screen, so the macro survives layout
shifts, different screen sizes, and minor content changes that would break a
fixed-coordinate replay. If the app removed or renamed the element, the
lookup returns nothing, replay falls back to the live model for the rest of
the run, and the resulting new sequence is re-saved — the macro heals itself.

`--macro` is independent of `--snap-to-elements`: the latter only adjusts
*live* (non-replayed) taps; a macro's replay step always resolves by element
identity (when one was recorded) regardless of whether `--snap-to-elements`
is passed on that particular run.

## Tap timing (`--humanize-taps`)

`adb shell input tap` always injects a MotionEvent DOWN+UP pair with **zero
elapsed time** and the **exact requested pixel** — a real finger never does
either. `--humanize-taps` issues each tap instead as `input swipe` from the
point to a randomly jittered point 0–2px away, over a randomized 45–130ms
duration (the same mechanism `long_press` already uses for its own duration,
just applied to ordinary taps too):

```sh
secdogie-android "..." --humanize-taps
```

**What this changes:** the injected event's duration and exact coordinate stop
being a fixed, always-identical signature — useful if a target app's *own*
in-app heuristics look at those specifically (e.g. flagging every tap that has
literally 0ms duration and pixel-perfect repeat coordinates).

**What this does not change, and cannot:** every event `adb shell input`
injects — humanized or not — still carries Android's `SOURCE_TOUCHSCREEN`
input-device flag marking it as *synthesized*, not from a real digitizer, and
any check that reads that flag, or that relies on hardware attestation
(Play Integrity / SafetyNet-style checks), sees straight through this
regardless. This flag exists purely to reduce a lookalike-timing heuristic,
**not** to defeat app-level bot detection, anti-cheat, or CAPTCHA/verification
challenges — those operate at a different layer this can't touch. Composes
with `--snap-to-elements`: snapping picks *where* to tap, humanizing changes
*how* the tap itself is issued.

## Known limitations

- **Some Chinese ROMs need an extra toggle for input injection** (see Setup
  step 4) — plain USB debugging alone isn't enough for taps/typing on MIUI
  and often other Chinese ROMs.
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
