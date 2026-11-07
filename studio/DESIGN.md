# Studio design system: the companion window

Studio is not a control panel. It is a companion that lives in the corner of
the screen as a faint, breathing light. It shows a whisper when it has
something to say in passing. A card rises above the light only when the work
needs the person: one question, one signature, one thing to remember, or the
first hello. Everything else, its state and its rules, sits behind the light
(click to talk, right-click or the Menu key for "inside").

The code is `secdogie_studio/qml/Presence.qml`. The Python bridge (`app.py`)
decides *what* to show; the QML only draws it.

This pass took its guidance from three skills vendored in `.claude/`
(impeccable, ui-ux-pro-max, Vercel's web-design-guidelines) and from the
principles of Taste, read upstream. Each rule below is there because one of
them asks for it. `.claude/VENDORED.md` lists the sources.

## Mode and world

- **Mode: Operate.** The person completes a task: answer, sign, keep a
  memory, write a rule. Clarity, keyboard reach and native expectations come
  before expression. The brand lives in the light, the ink and the voice.
- **World: night, soft light.** Deep ink surfaces, warm white text, one cool
  accent. There is no alarm red: even "this cannot be undone" is said in warm
  orange, by a partner, not by a siren.
- **Voice.** The companion speaks in the first person ("想让我做什么？",
  "看看我的里面", "让我休息"). Controls name their action ("签名同意",
  "这次不签", "生成钥匙", "签名并注入"). Errors name the problem *and* the
  recovery ("口令不对，再输一次。").

## Tokens

All tokens live at the top of `Presence.qml` (`ink`, `typo`, `motion`).

### Colour (`ink`)

| Token | Value | Use | On the card |
| --- | --- | --- | --- |
| `card` | rgba(0.071, 0.078, 0.106, 0.96) | every sheet and the whisper | n/a |
| `text` | `#ECE6DC` | primary text | 14.7 : 1 |
| `soft` | `#BDB5AA` | secondary text, explanations, the log | 9.0 : 1 |
| `faint` | `#948C82` | labels, placeholders, notes | 5.5 : 1 |
| `edge` | white 34 % | control boundaries (ghost pill, field underline) | 3.1 : 1 |
| `hairline` | white 8 % | sheet outline and dividers (decorative only) | n/a |
| `wash` / `press` | white 6 % / 11 % | hover and pressed fill on quiet controls | n/a |
| `calm` | `#8FB6FF` | resting mood; also the focus ring, the caret and selection | 8.9 : 1 |
| `think` | `#B9A7FF` | the light while it works | n/a |
| `warm` | `#F2C27F` | waiting for the person, memories, feedback | 11.1 : 1 |
| `sign` | `#F3A983` | a signature is needed | 9.4 : 1 |

The secondary text colours are tinted warm, never grey. Every text colour
clears 4.5 : 1 on the card, and every control boundary clears 3 : 1.

### Type (`typo`)

The scale has three steps at a ratio of about 1.2: `meta` 12, `body` 14,
`lead` 17.

- What the companion says, which is the heading of a card, is `lead` at
  medium weight.
- Values and controls are `body`. Labels, notes and the log are `meta`.
- There is no eyebrow or kicker above a heading, and no letter-spaced CJK. A
  card's kind is spoken by its own sentence ("我想记住「…」：…") and by the
  tint of its primary action. The old label survives only as the card's
  accessible name.
- Numbers in data (expiry times, fingerprints, ports, DIDs) use tabular
  figures (`font.features: { "tnum": 1 }`).
- Monospace is used only where the content is code: the YAML rules editor.

### Space and shape

- **Rhythm:** 4 / 8 / 12 / 16 / 24.
- **Sheets:** padding 24, radius 20. Groups inside a sheet are 16 apart; a
  label sits 6 above its field.
- **Controls:** pills are 40 high (radius 20). Tabs are 44 and the close
  button is 36.
- **Edges:** the window keeps 20 px around every surface for its shadow. The
  light's box and the sheets share the same right edge.
- **Depth:** a soft, offset shadow, built from six stacked translucent layers
  (each 2.5 px wider and 1.5 px lower). It is not a zero-offset glow and not a
  hard block shadow. It is drawn rather than blurred, so it renders the same
  on the software backend and on every OS.

### Motion (`motion`)

| Token | Normal | Reduced | Use |
| --- | --- | --- | --- |
| `enter` | 240 ms, exponential ease-out | 120 ms | a sheet arrives (fades in and rises 12 px) |
| `exit` | 160 ms, ease-in | 90 ms | a sheet leaves: two thirds of the entrance |
| `touch` | 120 ms | 120 ms | hover and press colour |
| `whisperIn` / `whisperOut` | 320 / 1100 ms | 120 / 400 ms | a whisper; it is let go of slowly because it may be mid-read |
| breath | 4.2 s rest, 2.6 s thinking, 1.7 s waiting | still, half-lit | the one authored motion |

**Reduced motion.** The preference comes from the OS (`motion.py`). Qt 6.10+
reports it itself. Older Qt falls back to macOS `reduceMotion`, Windows
"Show animations" or GNOME `enable-animations`, and
`SECDOGIE_REDUCE_MOTION=1/0` overrides both. With reduced motion on, the
light stops breathing, sheets cross-fade without travelling, and mood changes
are quick.

## Components

- **Light (orb).**
  - Glow rings and a core, tinted by mood, with a warm dot when something is
    held for the person.
  - It is a button for assistive technology. Tab reaches it and draws a focus
    ring. Enter or Space opens talk, and the Menu key opens inside.
- **Whisper.** A pill-shaped surface to the left of the light, at most 4
  lines. It is plain text, never rich text.
- **Card.** What the companion says (the heading), then the details as
  label/value rows (a refusal row in warm), then fields, then actions, then
  feedback.
  - A sign card refuses approval for its first 0.8 s.
  - The passphrase field is cleared after each attempt.
- **Pill (`Button`).** Primary (tinted fill and border) or quiet (`edge`
  border). It has hover, pressed and disabled states (40 % opacity), and a
  keyboard-only focus ring 2 px wide set 4 px out. Space and Enter press it.
  The layout never shifts on press.
- **Field (`TextField`).** A visible label wherever the meaning would vanish
  once you type (passphrases), and an example as placeholder (ending in
  "……"). It has an underline that turns `calm` and 2 px when focused, a
  `calm` caret that blinks at the platform's rate, and themed selection. Paste
  is never blocked. Secret fields are marked sensitive to input methods.
- **Tab, Link, Close.**
  - Each is a real button with an accessible name and a focus ring.
  - The close cross is drawn (two 1.5 px strokes), not a glyph.
  - "看看我的里面" in the talk sheet gives a click path to what right-click
    opens.
- **Rules editor.** An inset well whose border turns `calm` while editing. It
  has thin themed scrollbars (no system chrome) and monospace YAML.
- **Status rows.** A DID is one line, elided in the middle. Click it (or
  press Enter on it) to copy, and it says "已复制".

## States the window covers

- **Empty.**
  - A log with nothing in it explains how to fill it.
  - With no node yet, the first-meeting card opens on a cold start.
- **Loading.** "正在签名……" and "正在注入规则……" appear in place of
  feedback, and the action is disabled meanwhile.
- **Error.** The feedback text is warm and says what to do next.
- **Disabled.** Shown by dimming. A disabled control is also unreachable
  (`enabled: false`).
- **Keyboard.**
  - Every control is reachable and shows a focus ring only when focus came
    from the keyboard.
  - Esc puts away the talk and inside sheets.
  - The window never takes focus by itself. The one place it moves focus is
    into the talk field, right after the person clicked the light
    (`tests/test_purity.py`).

## Window

- **Frameless and on top.** The window is only as large as what it shows:
  the light alone, the light with a whisper, or the light with one sheet
  above it. The desktop around it stays the person's.
- **Resizing.** It grows at once and shrinks after the fade has finished.

## Checking it

- `studio/tests/test_app.py` loads the real QML offscreen, normal and with
  reduced motion, and fails on any QML error.
- The design was checked by rendering every scene with the software backend:
  rest, whisper, ask (and its keyboard focus), sign, memory, talk, inside
  (now and rules), and first meeting.
