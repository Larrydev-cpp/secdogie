# Studio design system: 宣纸 and 墨

Studio is the companion on your desktop, and the place your own devices
become one mesh. Its home window is a set of cards you read at a glance. There
are two worlds, rice paper (宣纸) by day and ink (墨) by night, and an ink
switch floods the window from one to the other.

The direction comes from the owner:
- a reference dashboard of cards with dot-matrix numerals and an orange accent
  card, whose light/dark switch drops a thread of ink that floods the page;
- their earlier wishes: 古风 (an old Chinese hand), an introduction to the
  architecture and its strengths, and Chinese and English throughout.

Studio keeps the reference's layout and its switch. The palette and the
details are its own: paper and ink, gold leaf, celadon, and cinnabar only
where something asks for you.

## The two worlds

| Token | 墨 (ink, night) | 宣纸 (paper, day) | What it is for |
| --- | --- | --- | --- |
| `ground` | `#12151D` | `#E8E0CD` | the window |
| `panel` | `#1B202B` | `#F5EFE2` | a card |
| `text` | `#F0E7D3` | `#1E2230` | words (13–15:1) |
| `soft` | `#BCB3A0` | `#4A4436` | secondary words (7–9:1) |
| `faint` | `#978F80` | `#685F4E` | captions (4.8:1 or more) |
| `lamp` | `#D9B46C` 泥金 | `#86590C` | the companion |
| `calm` | `#8EBBA8` 青瓷 | `#2F5D8A` 靛 | you, your controls, focus |
| `warm` | `#E2A65A` 杏黄 | `#8E4C14` | it waits on you |
| `sign` | `#E8826A` | `#A93624` | a signature, in words |
| `seal` | `#B23E27` 朱砂 | (same) | the card that asks for you; seals |
| `inverse` | `#F0E7D3` | `#1C212D` | the node's card, the current page, primary buttons |

Every text colour is 4.5:1 or more on the surfaces it sits on, in both worlds;
`Theme.qml` holds the values, checked by script. QR codes are always dark on
paper, because that is what cameras read.

## Type

- **Serif** (Songti / SimSun / Noto Serif CJK): the companion's voice,
  headlines, the 时辰, the architecture numerals 壹 贰 叁 肆.
- **Sans** (the platform's): the interface.
- **Dot numerals** (`DotText`, a 5×7 grid) for the counters: time here today,
  waiting count, uptime, the six-digit pairing code. The unlit dots stay
  faintly visible, so every place keeps its shape.

## Components

| Component | What it is |
| --- | --- |
| `Card` | the sheet everything sits on: 22 px corners, a soft offset shadow, a title and an optional chip. Kinds: `paper`, `seal` (cinnabar), `inverse` |
| `DotText`, `DotBars` | dot numerals; a chart in dots whose latest column is lit in cinnabar |
| `Dial` | a day in 时辰 (子 at the top): a tick per quarter hour, gold for the 时辰 you were here in, a cinnabar hand at now |
| `NavPill` | the pages as one pill; the current page drawn inverse; a cinnabar count where something waits |
| `ThemeSwitch` | sun and moon in a pill, the cinnabar knob on the world you are in |
| `InkFlood` | the change of world (below) |
| `QrCode` | an invitation for a phone's camera |

## The home window

The top bar holds the mark (nine dots, the middle one cinnabar) and the pages:
总览 Overview, 对话 Talk, 设备 Devices, 规则 Rules, 关于 About. At the right are
中 / EN, the switch and your initial.

**总览 Overview: seven cards, real data only.**
- **你在场 / You, here:** time at this computer today, then the last two hours
  in five-minute dot columns. It is read from the system's idle time only.
- **正在做 / Now:** an ink-wash landscape, what is under way, and the last
  three goals.
- **时辰 / The day:** the dial.
- **等你 / Waiting on you,** in cinnabar: how many things wait, which kinds,
  and a way to them.
- **我的设备 / My devices,** numbered like a chart: each device's state and a
  bar for its round trip.
- **节点 / This node,** inverse: uptime in dot numerals, its roles, carriers
  and key.
- **门的规则 / The gates:** the rules in force.

**对话 Talk.** The companion writes in the serif with its lamp in the margin;
you write in inverse bubbles. What waits on you sits above the composer, one
thing at a time.

**设备 Devices.**
- **Bring in a device:** asks for the passphrase, then shows the QR code, the
  link and the countdown, then the six digits with admit or cancel, then the
  seal 纳.
- **Join with an invitation:** read offline first; agreeing is the only thing
  that connects.
- **My devices:** go online or offline, each device's state, and notes.

**规则 Rules.** The YAML editor: try a sentence, then sign and inject.

**关于 About.** An ink-wash hero with the vertical inscription 吾之器物 自成一网.
Then the architecture (壹 你, 贰 Studio, 叁 节点, 肆 网格) and five strengths.

## The first meeting

hello → the four parts → a name → a passphrase → the keys.

The language and the switch are there from the first moment. The one
orchestrated motion is the lamp lighting, then the words.

## Motion

- **The ink flood (InkFlood.qml),** the one authored moment:
  1. A thread of ink (or paper) drops from the switch's knob to the foot of
     the window (380 ms, ease-in).
  2. It splashes, and the ink rises with a wavering edge until it covers
     everything (880 ms).
  3. The world changes under it.
  4. It lifts away (340 ms).
  A click while it runs is ignored.
- **Reduced motion:** a 240 ms fade, the same change.
- Everything else is plain fades and colour changes of 120–240 ms.

## Checking it

- Tests:
  - every page draws in both worlds without a QML warning;
  - the switch changes the world and back;
  - presence comes from the idle time only;
  - the purity scan: no screen capture, no taking focus, no autostart.
- Offscreen renders of each page, in both worlds and both languages, at
  1240 × 820 (the window's default; it opens no smaller than 1000 × 680).
