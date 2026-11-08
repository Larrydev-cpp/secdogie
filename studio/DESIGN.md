# Studio design system: the home window and the light

Studio has two faces, and only one shows at a time.

- **The home window** opens when the person opens Studio. It has two pages:
  - **对话 (conversation):** what was said, and what waits on the person.
  - **节点与网格 (node and mesh):** this node, its work, its mesh, and the
    rules in force.
- **The light.** Closing the home window leaves the companion as a faint,
  breathing light in the corner of the screen.
  - A whisper shows beside it when it has something to say in passing.
  - A card rises above it only when the work needs the person: one question,
    one signature, one memory, or the first hello.
  - Clicking the light opens the home window again. Right-click or the Menu
    key opens its "inside": state, log and the rules editor.

The code is in `secdogie_studio/qml/`:

- `Home.qml` and `Presence.qml` are the two windows.
- `Theme.qml` is a singleton that holds the tokens.
- The shared parts are `Sheet`, `Shadow`, `Pill`, `Link`, `CloseButton`,
  `Tab`, `Field`, `Caret`, `Bar`, `Ring`, `Fact`, `Orb`, `MomentCard` and
  `RulesEditor`, listed in `qmldir`.

The Python bridge (`app.py`) decides *what* to show; the QML only draws it.

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

All tokens live in `Theme.qml`, a singleton both windows share.

### Colour

| Token | Value | Use | On the card |
| --- | --- | --- | --- |
| `sunk` | `#0A0B0F` | the home window's rail, a step below | n/a |
| `ground` | `#0D0F14` | the home window | n/a |
| `panel` | `#151820` | a raised area in the home window: panels, chat bubbles, the composer, the waiting card | n/a |
| `panelHi` | `#1B1F29` | the open page in the rail | n/a |
| `card` | rgba(0.071, 0.078, 0.106, 0.96) | the light's sheets and the whisper | n/a |
| `text` | `#ECE6DC` | primary text | 14.7 : 1 |
| `soft` | `#BDB5AA` | secondary text, explanations, the log | 9.0 : 1 (8.6 on `panel`) |
| `faint` | `#948C82` | labels, placeholders, notes | 5.5 : 1 (5.3 on `panel`) |
| `edge` | white 34 % | control boundaries (ghost pill, field underline) | 3.1 : 1 |
| `hairline` | white 8 % | sheet outline and dividers (decorative only) | n/a |
| `wash` / `press` | white 6 % / 11 % | hover and pressed fill on quiet controls | n/a |
| `calm` | `#8FB6FF` | resting mood; also the focus ring, the caret and selection | 8.9 : 1 |
| `think` | `#B9A7FF` | the light while it works | n/a |
| `warm` | `#F2C27F` | waiting for the person, memories, feedback | 11.1 : 1 |
| `sign` | `#F3A983` | a signature is needed | 9.4 : 1 |

The secondary text colours are tinted warm, never grey. Every text colour
clears 4.5 : 1 on the card, and every control boundary clears 3 : 1.

### Type

The scale has five steps at a ratio of about 1.2: `meta` 12, `body` 14,
`lead` 17, `title` 20 (a home page's title), `display` 28 (the first
meeting's headings).

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

### Motion

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

- **Icons (`Icon.qml`).** Drawn, never glyphs: one small set authored on a
  24 px grid (conversation, mesh, power, send, eye, check, shield, key,
  arrow), one 1.6 px stroke with round caps. They are decorative; the
  control around them carries the accessible name.
- **The light (`Orb.qml`).** A soft radial glow around a small sphere lit
  from the upper left, drawn with Qt Quick Shapes gradients.
  - Large and breathing as the companion itself.
  - Still and small (26–56 px) as its mark beside what it says, or as a
    node's state.
  - Huge and faint (7–16 %) as the light the work sits in.
- **IconButton, StatusPill, Panel.**
  - IconButton: a round button around one icon, used for send and show
    passphrase.
  - StatusPill: a tinted label for a state that is always also written in
    words (排队中, 正在做, 做完了, 生效中).
  - Panel: a raised group on `panel` with a hairline and a faint top light.

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

## States the windows cover

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
  - Neither window moves the focus by itself (no `forceActiveFocus` anywhere;
    `tests/test_purity.py`). The home window's input line is just its first
    focus item, in a window the person opened.

## The first meeting (sign-up)

Before there is a profile, the home window is the first meeting
(`Onboarding.qml`).

- **Layout.** One centred column, at most 460 px wide. Above it sits a huge,
  faint light. At the top are four marks for the steps; the current one is
  drawn long.
- **The steps.**
  1. **你好.** Who it is, in three promises, each with a drawn icon:
     everything stays on this computer; only your signature counts; it asks
     in prose when unsure.
  2. **怎么称呼你.** An optional name, kept in the profile (`name`, at most
     24 characters, one line). It is used in the greeting, on the rail and
     in the empty conversation.
  3. **签名口令.** The passphrase and its confirmation:
     - the passphrase can be shown while typing (eye button);
     - a four-mark strength meter with a word (太短了 / 还可以 / 不错 /
       很好);
     - two checks drawn as icons (at least 8 characters; both the same);
     - "生成我的钥匙" is enabled only once both checks hold.
  4. **正在生成.** The light thinks while the keys are made.
  5. **好了.** The three keys (the node's, the App's, your signing key),
     each copyable, then "开始吧".
- **Motion.** Steps cross-fade and slide 24 px (no slide when reduced
  motion). The bridge says when the keys are being made and when they are
  ready (`onboarding`: form / making / ready). The steps before that live in
  the QML.
- **The light.** If the window is closed during the first meeting, the
  light offers the same hello as a card.

## The home window

- **Shape.** It is a normal, decorated window: 1040 × 720, and never
  smaller than 820 × 580. Pages cross-fade. It is drawn on `ground`.
- **Closing.** Closing it never quits. It leaves the light, and "让我休息"
  in the rail is what quits (window and node together).
- **Rail (236 px, on `sunk`).**
  - The companion's light at 40 px, its name, and a state dot with its mood
    in words ("醒着", "正在做事", "有事等你", "等你签名").
  - Each page has a drawn icon. The open page sits on `panelHi` with a
    hairline; there is no side stripe.
  - At the foot: you (an initial in a calm circle, your name, "签名钥匙在这台
    电脑上"), then "让我休息" and "关掉窗口，我就在屏幕角落。"
  - Two pages, switched by click, Tab and Enter, or Ctrl+1 / Ctrl+2. The page
    you are on is filled with `press`. When you are on the other page,
    "对话" shows how many things wait on you (a warm count).
- **对话.**
  - A readable column, at most 720 px wide.
  - A title, and under it a greeting by the time of day and your name
    ("晚上好，小王。").
  - The person's lines sit on the right in a calm-tinted bubble; the
    companion's lines sit on the left on `panel`.
    - Lines from the same speaker run together (6 px apart, 18 px between
      speakers).
    - The first line of a run has a sharper corner toward its speaker, and
      the companion's run opens with its small light.
    - A step it stopped is written in warm. Quiet notes sit centred in a
      faint chip.
  - Before the first word: the light, "想让我做点什么？", and three
    suggestions that fill the composer (they never take the focus).
  - The composer is a rounded box on `panel` whose border lights calm while
    you type. Inside it are the line and a round send button.
  - A short conversation sits just above the input line. The list keeps the
    newest line in view unless you scrolled up, and lines are appended by a
    stable key, so the list never jumps.
  - What waits on the person shows as one card (`MomentCard` on `panel`, its
    kind a line of light along the top edge) above the composer, with "还有 N 件在等你" when there are more. A
    question that is still waiting appears only in that card. Once it is
    answered, question and answer take their place in the conversation.
  - **A refusal at length (a letter).** It sits on the left like any of the
    companion's lines, but 90 % wide.
    - Its first paragraph is the heading, at `lead` and medium weight, in
      warm.
    - The rest follows as paragraphs in `soft`, with line height 1.6 and
      10 px between paragraphs.
    - The automatic Socratic gate's own questions and answers read as
      "问题——回答" lines.
    - On the light, only the first paragraph is whispered.
  - **A question in prose.** It has no buttons. Its card shows the same
    heading-and-paragraphs, and the answer goes in the card's own line
    ("直接告诉我……"). On the light the card shows at most three
    paragraphs, then "在主界面里看完整的".
  - The input line is the window's first focus item. "交给我" is enabled
    once there is something to send.
- **节点与网格.** Four panels. The facts are label/value rows, and DIDs
  copy on click.
  - **This node:** its state (a light and words), how long it has been awake,
    DID, roles, listen address, how it connects, and whether Studio is
    connected.
  - **Its work:** a one-line count, then the latest goals, each with a
    status pill.
  - **The mesh:** a small drawing of this node with dashed empty places
    around it, the operator's signing key and the App. The other nodes have
    an honest empty state: there are none yet, and invitations, LAN
    discovery and WebRTC are still being built.
  - **The gate rules:** which bundles are in force and which are only
    recorded. "编辑规则" opens the same editor the light uses, inline (no
    modal).
  - The node itself supplies this, over `admin/v1` (read-only, every 3 s
    while the window is open). The controller and `policy/v1` supply the
    rest.

## The light's window

- **Frameless and on top**, and hidden while the home window is open. It is
  only as large as what it shows: the light alone, the light with a whisper,
  or the light with one sheet above it. The desktop around it stays the
  person's.
- **Resizing.** It grows at once and shrinks after the fade has finished.

## Checking it

- `studio/tests/test_app.py` loads the real QML offscreen, normal and with
  reduced motion, and fails on any QML error. It also checks the lifecycle:
  the home window at launch; closing it leaves the light; clicking the light
  brings it back; a cold start opens on the first hello.
- The design was checked by rendering every scene with the software backend:
  the home window (empty conversation, a question waiting, a signature, node
  and mesh, the rules editor, first hello) and the light (rest, whisper, ask,
  sign, memory, inside).
