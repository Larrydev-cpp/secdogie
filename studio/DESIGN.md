# Studio design system: night letters

Studio has two faces, and only one shows at a time.

- **The home window** opens when the person opens Studio. It has two pages:
  - **对话 (conversation):** what was said, and the one thing that waits on
    the person.
  - **节点与网格 (node and mesh):** this node, its work, its mesh, your keys
    and the rules in force.
- **The light.** Closing the home window leaves the companion as a faint,
  breathing light in the corner of the screen.
  - A whisper shows beside it in passing.
  - A card rises above it only when the work needs the person: one question,
    one signature, one memory, or the first hello.
  - Clicking the light opens the home window again. Right-click or the Menu
    key opens its "inside": state, log and the rules editor.

The code is in `secdogie_studio/qml/`:

- `Home.qml`, `Presence.qml` and `Onboarding.qml` are the two windows and
  the first meeting.
- `Theme.qml` is a singleton that holds the tokens.
- The shared parts are `Orb`, `Icon`, `Pill`, `IconButton`, `Link`, `Field`,
  `Fact`, `MomentCard`, `RulesEditor` and a few smaller ones, listed in
  `qmldir`.

The Python bridge (`app.py`) decides *what* to show; the QML only draws it.

## The brief, and the world it comes from

- **Subject.** A companion that lives on your computer and does what you
  ask. It writes to you and asks before it is unsure. It waits for your
  signature on what cannot be undone, and it belongs to a mesh of nodes.
- **Audience.** The person who owns this computer. **Primary job:** talk to
  it, answer what it asks, and trust what it does.
- **Pinned by the owner.**
  - A partner, not an admin console.
  - A floating light that is faint until needed.
  - A night-time soft glow.
  - Letters at length instead of rows of buttons.
  - A first meeting that feels like signing up.

The visual choices come from that world. It is night. A lamp is left on: the
companion. It writes letters, and the mesh is a constellation of such lights.

## The plan

**Colour.** Six named values, plus moods.

| Name | Hex | Role |
| --- | --- | --- |
| night | `#0E1322` | the ground: blue ink, not black |
| deeper night | `#0A0E1A` | the rail |
| letter paper | `#172038` | what is raised: the composer, a letter, a waiting card |
| moon | `#ECE7DC` | text |
| lamp | `#F2CF86` | **the companion**: its light, its mark, its letters' warmth |
| frost | `#9FB8EE` | **you**: your words, your controls, the keyboard's focus |

- **Moods** (the light's tint): lamp at rest, dusk `#B9ABF0` while it works,
  amber `#F0A955` while it waits on you, seal `#E8957A` for a signature.
- **Secondary text:** mist `#AEB4C8` and dim `#8C93AC`. Every text colour
  clears 4.5 : 1 on night, deeper night and letter paper, and control edges
  (moon at 40 %) clear 3 : 1.
- **The rule that carries it:** warm is the companion, cool is you. The two
  lights never swap.

**Type.** Two families, clearly distinct, each with one job.

- **The companion's voice is a serif:** Songti SC on macOS, SimSun on
  Windows, Noto Serif CJK SC on Linux. Everything it says is set in it: lines
  in the conversation, letters, whispers, card headings, the first meeting,
  the greeting, the name in the rail.
- **The interface is the platform's sans:** your words, labels, controls and
  facts.
- **Scale:** a classical one (The Elements of Typographic Style): 12, 14, 16
  (the voice), 18, 24, 36.
  - The voice runs at a 1.7–1.75 line height, slightly more than the sans,
    as serif body text wants.
  - Measure: the conversation column is at most 700 px, about 40 characters
    of the serif.

**Layout.**

```
┌ rail ─────┐┌ conversation ────────────────────────────────┐
│ ● secdogie ││ 夜深了，小王。            (its greeting, serif) │
│   醒着      ││                                              │
│ ▢ 对话      ││ ●  好，我开始做「整理周报」。  (its voice, no bubble)
│ ▢ 节点与网格 ││                      ┌───────────────┐       │
│            ││                      │ 把旧备份删掉    │ (you: sans, frost)
│            ││ ●  ┌ letter paper ───────────────────┐      │
│            ││    │ 「旧备份」，我这次不删。          │      │
│            ││    │ 它是一个文件夹……  (serif letter)  │      │
│ 你 小王     ││    └─────────────────────────────────┘      │
│ 让我休息    ││ ( 告诉我你想做什么，回车交给我        (↑) )  │
└───────────┘└──────────────────────────────────────────────┘
```

- **The conversation is left-aligned, like a letter.** Its lines start
  beside its small light in the margin. Your lines sit to the right in frost
  bubbles.
- **The node page opens with the constellation.** This lamp sits in the
  middle, with dashed empty places for the nodes still to come. The facts
  follow as quiet label and value lists.
- **The first meeting is one centred column** in the dark.

**Principles.**

1. **Spend boldness in one place: the companion's voice.** It is the serif,
   set without a bubble, as a letter when it is long. Everything around it
   stays quiet.
2. **Warm is the companion; cool is you.**
3. **One orchestrated moment.** When the first meeting opens, the lamp
   lights up in the night, and then the greeting arrives. Everything else
   moves only in answer to the person: pages fade, steps fade, a card
   arrives. The light's breathing is its life, not decoration. All of it is
   still under reduced motion.
4. **Structure only where it carries meaning.**
   - Four step marks in the first meeting, because it is a sequence.
   - A line of light along a waiting card's top edge, in its kind's colour.
   - The constellation's empty places, because they are real places to fill.

## What it deliberately is not

Reviewed against the generic defaults (frontend-design's calibration list).
What was changed and why:

- **Near-black with one bright accent.** The previous pass was `#0D0F14` with
  a single blue. Now the ground is blue ink (a night, not a void), and there
  are two soft lights with fixed meanings.
- **The SaaS card kit.** The node page was four identical rounded panels with
  top-light gradient washes. Now the constellation leads and the facts are
  plain lists separated by space. The gradient washes are gone from every
  sheet.
- **Template chrome.**
  - The middle-dot meta strings ("正在做 1 件 · 排队中 1 件", "UDP · 中继 1
    个") are now sentences ("正在做 1 件，排队中 1 件", "UDP，另有 1 个中继").
  - The arrows appended to buttons are gone ("开始认识 →" is now "认识一下").
  - There are no all-caps or eyebrow labels anywhere.
- **The feature list with icons in tinted circles** (the old hello step) is
  now four short lines of its voice.
- **Chat bubbles on both sides** are now one-sided: only you speak in
  bubbles; it writes.

## Tokens

All tokens live in `Theme.qml`.

| Token | Value | Use |
| --- | --- | --- |
| `ground` | night `#0E1322` | the home window |
| `sunk` | deeper night `#0A0E1A` | the rail |
| `panel` / `panelHi` | letter paper `#172038` / `#1E2843` | the composer, letters, the waiting card |
| `card` | letter paper at 97 % | the light's sheets on the desktop |
| `text` / `soft` / `faint` | moon / mist / dim | text |
| `hairline` / `edge` / `wash` / `press` / `well` | moon at 9 / 40 / 6 / 11 / 3.5 % | dividers, control edges, hover, pressed, the rules editor |
| `lamp` | `#F2CF86` | the companion at rest |
| `calm` (= `focus`) | frost `#9FB8EE` | you: your bubbles, primary actions, links, focus rings, the caret, selection |
| `think` / `warm` / `sign` | dusk / amber / seal | the light's moods; amber also for a step it stopped and a refusal's heading |
| `serif` | per platform | the companion's voice |
| `meta` / `body` / `voice` / `lead` / `title` / `display` | 12 / 14 / 16 / 18 / 24 / 36 | type |
| `enter` / `exit` / `touch` | 240 / 160 / 120 ms (120 / 90 under reduced motion) | motion; exits take two thirds of entrances |

## Components

- **The light (`Orb`).** A radial glow around a small sphere lit from the
  upper left, drawn with Qt Quick Shapes gradients.
  - It breathes as the companion.
  - It sits still as a node's state or as the large faint glow the work sits
    in (`sphere: false`).
  - At 32 px or less (its mark beside a line) it is drawn with plain
    rectangles, which clip inside a scrolling list on every renderer.
- **Icons (`Icon`).** Drawn on a 24 px grid with one 1.6 px round stroke:
  conversation, mesh, power, send, eye, check. They are decorative; the
  control around them carries the accessible name.
- **Controls.**
  - Pills are primary (frost) or quiet (`edge`). IconButtons are round. Links
    underline on hover.
  - Every control has hover, pressed and disabled states, and a
    keyboard-only focus ring 2 px wide set 4 px out.
  - Nothing shifts on press.
- **Field.** A visible label where the meaning would vanish once you type,
  and a frost underline and caret.
  - A secret can be shown while typing (eye button).
  - The composer's field is `bare`: the box around it lights instead.
- **MomentCard.** What the companion says, in its serif, as the heading,
  then any paragraphs. Below that come the details (sans), fields, actions
  and feedback.
  - A sign card refuses approval for its first 0.8 s.
  - A passphrase is cleared after each attempt.
- **Fact.** A label (dim, sans 12) and a value on one line. A DID is elided
  in the middle and copies on click ("已复制").

## The home window

- **Shape.** A decorated window, 1040 × 720 (at least 820 × 580). Closing it
  never quits; it leaves the light. "让我休息" quits, window and node
  together.
- **Rail.**
  - The light and the name "secdogie" in the serif, with a state dot and its
    mood in words.
  - Two pages, each with an icon. The open page sits on a quiet fill with a
    frost icon. When the conversation waits on you, it shows an amber count.
    Ctrl+1 / Ctrl+2 switch pages.
  - At the foot: you (your initial in a frost circle, your name), then
    "让我休息" and "关掉窗口，我就在屏幕角落。"
- **Conversation.**
  - The heading is its greeting: by the time of day, with your name
    ("夜深了，小王。").
  - Its lines are set in its serif with no bubble. A run opens with its small
    light in the margin. A refusal at length is a letter on letter paper:
    heading in amber, paragraphs in mist.
  - Your lines are sans in frost bubbles on the right, with a sharper corner
    toward you at the start of a run. Quiet notes are centred and dim.
  - Before the first word: the light, "想让我做点什么？", and three things you
    might say. Clicking one fills the composer; it never takes the focus.
  - The one thing that waits on you is a card on letter paper above the
    composer, marked by a line of light in its kind's colour, with
    "还有 N 件在等你" when there are more.
  - The composer is a rounded box whose border turns frost while you type,
    with a round send button. It is the window's first focus item.
  - The list keeps the newest line in view unless you scrolled up. A short
    conversation sits just above the composer. Lines are appended by a
    stable key, so nothing jumps.
- **Node and mesh.**
  - The constellation first: this lamp, with dashed empty places for the
    nodes still to come. Beside it, in its voice: "网格「home」里，现在只有我。"
    Then how long it has been awake, and how other nodes will join
    (invitations, LAN discovery and WebRTC are still being built).
  - Then four quiet sections: this node (its key, roles, address, how it
    connects, whether Studio is connected); its work (a sentence, then the
    latest goals with a coloured dot and words); your keys; and the gate
    rules in force (editable in place).
  - The node supplies this itself over `admin/v1` while the window is open.

## The first meeting (sign-up)

Before there is a profile, the home window is the first meeting
(`Onboarding.qml`).

- **Layout.** One centred column, at most 440 px wide, around a large faint
  lamp-glow. Each step sits at the vertical centre at its own height. Four
  marks show where you are, the current one drawn long in lamp.
- **The steps.** The title is in the serif at 36; the words are in the
  serif at 16.
  1. **你好.** Who it is, and three promises in four short lines.
  2. **怎么称呼你.** An optional name, kept in the profile (`name`, at most 24
     characters, one line).
  3. **签名口令.** The passphrase:
     - it can be shown while typing;
     - a four-mark strength meter with a word;
     - two drawn checks (at least 8 characters; both the same);
     - "生成我的钥匙" is enabled only when both checks hold.
  4. **正在生成你的钥匙.** The light thinks.
  5. **好了.** The three keys, copyable, then "开始吧".
- **The moment.** On first open, the lamp lights up (1.6 s), and then the
  words arrive (0.7 s). After that, steps simply fade. With reduced motion,
  all of it is just there.
- **The bridge.** It reports where the meeting is (`onboarding`: form,
  making or ready); the steps before that live in the QML. If the window is
  closed during the first meeting, the light offers the same hello as a card.

## The light's window

- **Frameless and on top**, and hidden while the home window is open.
- **It is only as large as what it shows:** the light, a whisper (in the
  serif), or a card or the inside sheet on letter paper.
- **A long question.** On the light the card shows three paragraphs, then
  "在主界面里看完整的".

## States

- **Empty.** An empty conversation invites a first request. The inside
  sheet's empty log says how to fill it. There are no other nodes yet, and
  the page says when there will be.
- **Loading.** "正在签名……", "正在注入规则……" and "正在生成你的钥匙……"
  replace the action's feedback; the action is disabled meanwhile.
- **Error.** Amber text that says what to do next.
- **Keyboard.**
  - Every control is reachable, with a focus ring only when focus came from
    the keyboard.
  - Esc puts away what you opened.
  - Neither window moves the focus by itself (no `forceActiveFocus` anywhere;
    `tests/test_purity.py`).
- **Reduced motion** (`motion.py`, from the OS). The light does not breathe,
  nothing slides, and the first meeting's moment is skipped.

## Checking it

- **Tests.** `studio/tests/test_app.py` loads the real QML offscreen, normal
  and with reduced motion, and fails on any QML error. It also covers the
  lifecycle: the home window at launch, the light when it is closed, back
  on a click, and a cold start that opens on the first meeting.
- **Renders.** Every scene is rendered with the software backend: the
  conversation (empty, a question, a letter, a signature), the node page,
  the rules editor, each step of the first meeting, and the light. Each
  render forces a full repaint first: the software renderer's partial
  repaints can leave stale pixels between frames that the GPU backends never
  show.
