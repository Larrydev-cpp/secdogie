// The design tokens every window draws with: colour, type, shape, motion, and
// Studio's words in Chinese and English.
//
// Studio is a wallet. The node, what waits on you, and each of your devices are
// cards, drawn like bank cards: glazed art, a masked key where a card number
// would be, a dark panel carrying the mark. Around them the page is set the
// way Google sets its pages: Google Sans, generous space, tonal surfaces,
// pill buttons, one primary colour.
//
// Two worlds, one switch:
//   * 墨 (ink): the night. An ink ground, paper-white words.
//   * 宣纸 (rice paper): the day. A warm paper ground, white sheets, ink words.
// 霁蓝 (the cobalt glaze) is the primary colour: your controls and the focus.
// Gold (泥金) is the companion; cinnabar (朱砂) is the seal, for what asks for
// you. Every text colour is 4.5:1 or more on the surfaces it sits on.
pragma Singleton
import QtQuick

QtObject {
    // The person asked their computer for less motion; each window sets this
    // from the bridge (P.still) when it opens.
    property bool still: false
    // 墨 or 宣纸: bound to the bridge's P.theme by the home window.
    property bool dark: true
    // Studio's language: bound to P.lang.
    property string lang: "zh"
    function t(zh, en) { return lang === "en" ? en : zh }

    // ---- surfaces ----
    readonly property color ground: dark ? "#111216" : "#F3F1EC"     // the window
    readonly property color sunk: dark ? "#0B0C0F" : "#E9E6DF"       // a well below the ground
    readonly property color panel: dark ? "#1C1D22" : "#FFFFFF"      // a sheet
    readonly property color panelHi: dark ? "#25272D" : "#FBFAF7"    // ... raised again, a field
    readonly property color card: dark ? Qt.rgba(0.110, 0.114, 0.133, 0.97)      // the light's sheets, on the desktop
                                       : Qt.rgba(1, 1, 1, 0.97)
    readonly property color inkOn: dark ? "#ECE8E1" : "#1D1C1A"      // what text is drawn in
    readonly property color well: tinted(inkOn, dark ? 0.04 : 0.035)
    readonly property color hairline: tinted(inkOn, dark ? 0.10 : 0.10)
    readonly property color edge: tinted(inkOn, dark ? 0.42 : 0.44)  // a control's boundary: 3:1 on every surface
    readonly property color wash: tinted(inkOn, dark ? 0.06 : 0.05)
    readonly property color press: tinted(inkOn, dark ? 0.11 : 0.09)
    readonly property color text: inkOn
    readonly property color soft: dark ? "#BDB8AE" : "#47443E"
    readonly property color faint: dark ? "#98938A" : "#6A665D"
    // ---- the primary colour: 霁蓝, you and your controls ----
    readonly property color primary: dark ? "#AFC2FF" : "#2648B0"
    readonly property color onPrimary: dark ? "#0E1E52" : "#FFFFFF"
    readonly property color primaryContainer: dark ? "#253566" : "#DCE4FA"
    readonly property color onPrimaryContainer: dark ? "#DDE5FF" : "#122A6B"
    // ---- the companion's lights ----
    readonly property color lamp: dark ? "#E0B867" : "#86590C"       // 泥金: the companion
    readonly property color calm: primary                            // you, your controls, the keyboard
    readonly property color think: dark ? "#BFA9D6" : "#5E4590"      // 藕荷: it is working
    readonly property color warm: dark ? "#E8A95C" : "#8E4C14"       // 杏黄: it waits on you
    readonly property color sign: dark ? "#F0907A" : "#A93624"       // 朱砂 as text: a signature
    readonly property color good: dark ? "#8FD3B6" : "#1D6B50"       // 青瓷: online, done
    readonly property color focus: primary
    // the seal: what asks for you
    readonly property color seal: "#B23E27"
    readonly property color sealText: "#FFF4EA"
    readonly property color sealSoft: Qt.rgba(1, 0.957, 0.918, 0.82)
    readonly property color inverse: dark ? "#ECE8E1" : "#1D1C1A"
    readonly property color inverseText: dark ? "#1D1C1A" : "#F3F1EC"
    readonly property color inverseSoft: dark ? "#47443E" : "#BDB8AE"
    readonly property color gold: dark ? "#E0B867" : "#B7832A"
    // dot-matrix numerals: lit and unlit dots
    readonly property color dotOn: text
    readonly property color dotOff: tinted(inkOn, dark ? 0.10 : 0.12)
    readonly property color shadow: dark ? Qt.rgba(0, 0, 0, 0.5) : Qt.rgba(0.20, 0.17, 0.10, 0.14)

    // ---- the cards' glazes: the same by day and by night, as a real card is ----
    // base, deep, light (the sheen), and the colour of the card's shadow
    readonly property var glazes: ({
        "jilan":  { zh: "霁蓝", en: "Cobalt",   base: "#2344C4", deep: "#121F6E", light: "#B8C6F6", glow: "#2344C4" },
        "qingci": { zh: "青瓷", en: "Celadon",  base: "#2F7A6C", deep: "#123F39", light: "#CDEBDD", glow: "#2F7A6C" },
        "yanzhi": { zh: "胭脂", en: "Rouge",    base: "#A3304A", deep: "#4E0F22", light: "#F6C0CB", glow: "#A3304A" },
        "gutong": { zh: "古铜", en: "Bronze",   base: "#7A5426", deep: "#2E1E0B", light: "#F5D6A8", glow: "#7A5426" },
        "dai":    { zh: "黛",   en: "Indigo",   base: "#38405C", deep: "#12151F", light: "#C4CCE6", glow: "#262C40" },
        "ouhe":   { zh: "藕荷", en: "Lotus",    base: "#6E5596", deep: "#2E2147", light: "#E2D3F7", glow: "#6E5596" },
        "zhusha": { zh: "朱砂", en: "Cinnabar", base: "#B23E27", deep: "#5E170B", light: "#FFD2B8", glow: "#B23E27" }
    })
    readonly property var deviceGlazes: ["jilan", "qingci", "yanzhi", "gutong", "ouhe", "dai"]
    function hash(s) {                       // a stable number from a key, for a card's glaze and art
        let h = 5381
        for (let i = 0; i < (s || "").length; i++) h = ((h * 33) ^ s.charCodeAt(i)) >>> 0
        return h
    }
    function glazeFor(key) { return deviceGlazes[hash(key) % deviceGlazes.length] }

    function moodColor(mood) {
        switch (mood) {
        case "thinking": return think
        case "waiting": return warm
        case "signing": return sign
        default: return lamp
        }
    }
    function tinted(c, a) { return Qt.rgba(c.r, c.g, c.b, a) }

    // ---- type: Google Sans for the page; a serif for the companion's voice and the 古风 marks ----
    readonly property string sans: "Google Sans"
    readonly property string serif: Qt.platform.os === "osx" ? "Songti SC"
                                    : (Qt.platform.os === "windows" ? "SimSun" : "Noto Serif CJK SC")
    readonly property string mono: Qt.platform.os === "windows" ? "Consolas" : (Qt.platform.os === "osx" ? "Menlo" : "monospace")
    readonly property int meta: 12
    readonly property int body: 14
    readonly property int voice: 16          // what the companion says, in the serif
    readonly property int lead: 18
    readonly property int title: 22
    readonly property int headline: 32
    readonly property int display: 44        // the greeting

    // ---- shape ----
    readonly property int round: 24          // a sheet's corners
    readonly property int cardRound: 18      // a wallet card's corners (a bank card's, scaled)
    readonly property real cardRatio: 1.586  // ISO/IEC 7810 ID-1
    readonly property int gap: 16

    // ---- motion ----
    readonly property int enter: still ? 120 : 240
    readonly property int exit: still ? 90 : 160              // leaving takes two thirds of arriving
    readonly property int touch: 120                          // hover and press
    readonly property int shuffle: still ? 0 : 460            // a card coming to the front of the wallet
    readonly property int whisperIn: still ? 120 : 320
    readonly property int whisperOut: still ? 400 : 1100      // let go of slowly: it may be mid-read
    readonly property real rise: still ? 0 : 12               // how far a sheet travels as it fades in
}
