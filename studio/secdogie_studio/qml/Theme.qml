// The design tokens every window draws with (studio/DESIGN.md): colour, type,
// motion, and Studio's own words in Chinese and English.
//
// Two worlds, one switch:
//   * 墨 (ink): the night. The ground is ink, the text is rice paper, gold
//     leaf (泥金) marks the companion, celadon (青瓷) marks you.
//   * 宣纸 (rice paper): the day. The same, the other way round: paper ground,
//     ink text, a deeper gold and an ink-blue for you.
// Cinnabar (朱砂) is the seal: signatures, and the one card that asks for you,
// the same in both. The inverse card (ink on paper, paper on ink) carries
// what the gates hold. Every text colour is 4.5:1 or more on the surfaces
// it sits on, in both worlds.
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

    // ---- colour ----
    readonly property color ground: dark ? "#12151D" : "#E8E0CD"     // the window
    readonly property color sunk: dark ? "#0E1118" : "#DED5C0"       // deeper: a well, the rail
    readonly property color panel: dark ? "#1B202B" : "#F5EFE2"      // a card
    readonly property color panelHi: dark ? "#232938" : "#FBF7EE"    // ... raised again
    readonly property color card: dark ? Qt.rgba(0.106, 0.125, 0.169, 0.97)      // the light's sheets, on the desktop
                                       : Qt.rgba(0.961, 0.937, 0.886, 0.97)
    readonly property color inkOn: dark ? "#F0E7D3" : "#1E2230"      // what text is drawn in
    readonly property color well: tinted(inkOn, dark ? 0.035 : 0.04)
    readonly property color hairline: tinted(inkOn, dark ? 0.09 : 0.11)
    readonly property color edge: tinted(inkOn, dark ? 0.40 : 0.42)  // a control's boundary: 3:1 on every surface
    readonly property color wash: tinted(inkOn, dark ? 0.06 : 0.05)
    readonly property color press: tinted(inkOn, dark ? 0.11 : 0.09)
    readonly property color text: inkOn
    readonly property color soft: dark ? "#BCB3A0" : "#4A4436"
    readonly property color faint: dark ? "#978F80" : "#685F4E"
    // the companion's lights, and you
    readonly property color lamp: dark ? "#D9B46C" : "#86590C"       // 泥金: the companion
    readonly property color calm: dark ? "#8EBBA8" : "#2F5D8A"       // 青瓷 / 靛: you, your controls, the keyboard
    readonly property color think: dark ? "#BAA3CC" : "#5E4590"      // 藕荷: it is working
    readonly property color warm: dark ? "#E2A65A" : "#8E4C14"       // 杏黄: it waits on you
    readonly property color sign: dark ? "#E8826A" : "#A93624"       // 朱砂 as text: a signature
    readonly property color focus: calm
    // the seal card (asks for you) and the inverse card (what the gates hold)
    readonly property color seal: "#B23E27"
    readonly property color sealText: "#FBF3E6"
    readonly property color sealSoft: Qt.rgba(0.984, 0.953, 0.902, 0.78)
    readonly property color inverse: dark ? "#F0E7D3" : "#1C212D"
    readonly property color inverseText: dark ? "#1E2230" : "#F0E7D3"
    readonly property color inverseSoft: dark ? "#4A4436" : "#BCB3A0"
    readonly property color gold: dark ? "#D9B46C" : "#B7832A"       // a lit tick on the dial
    // dot-matrix numerals: lit and unlit dots
    readonly property color dotOn: text
    readonly property color dotOff: tinted(inkOn, dark ? 0.10 : 0.12)
    readonly property color shadow: dark ? Qt.rgba(0, 0, 0, 0.45) : Qt.rgba(0.29, 0.22, 0.12, 0.16)

    function moodColor(mood) {
        switch (mood) {
        case "thinking": return think
        case "waiting": return warm
        case "signing": return sign
        default: return lamp
        }
    }
    function tinted(c, a) { return Qt.rgba(c.r, c.g, c.b, a) }

    // ---- type: the companion writes in a serif; the interface is the platform's sans ----
    readonly property string serif: Qt.platform.os === "osx" ? "Songti SC"
                                    : (Qt.platform.os === "windows" ? "SimSun" : "Noto Serif CJK SC")
    // a classical scale (The Elements of Typographic Style): 12 14 16 18 24 36
    readonly property int meta: 12
    readonly property int body: 14
    readonly property int voice: 16          // what the companion says, in the serif
    readonly property int lead: 18
    readonly property int title: 24
    readonly property int display: 36        // the first meeting's greeting

    // ---- shape ----
    readonly property int round: 22          // a card's corners
    readonly property int gap: 16            // between cards

    // ---- motion ----
    readonly property int enter: still ? 120 : 240
    readonly property int exit: still ? 90 : 160              // leaving takes two thirds of arriving
    readonly property int touch: 120                          // hover and press
    readonly property int whisperIn: still ? 120 : 320
    readonly property int whisperOut: still ? 400 : 1100      // let go of slowly: it may be mid-read
    readonly property real rise: still ? 0 : 12               // how far a sheet travels as it fades in
}
