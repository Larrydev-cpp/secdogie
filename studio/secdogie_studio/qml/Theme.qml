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
    // Sunlit Snow Mountain & Garden (light, default) or Alpine Twilight (dark): bound to P.theme.
    property bool dark: false
    // Studio's language: bound to P.lang.
    property string lang: "zh"
    function t(zh, en) { return lang === "en" ? en : zh }

    // ---- surfaces: 油画雪山与花园 (clear alpine sky, snow cold white, garden moss green) ----
    readonly property color ground: dark ? "#0F1922" : "#EAF3FA"     // alpine twilight / sunlit sky canvas
    readonly property color sunk: dark ? "#0A1219" : "#DCEAF5"       // glacial well below the ground
    readonly property color panel: dark ? Qt.rgba(0.094, 0.149, 0.192, 0.90)
                                        : Qt.rgba(0.976, 0.992, 1.000, 0.88)  // frosted alpine-white glass sheet
    readonly property color panelHi: dark ? "#1C2B37" : "#F6FAFD"    // snow cold-white field
    readonly property color card: dark ? Qt.rgba(0.098, 0.153, 0.196, 0.95)
                                       : Qt.rgba(0.980, 0.996, 1.000, 0.94)
    readonly property color inkOn: dark ? "#EDF5FA" : "#152630"      // deep pine-slate ink (>= 12:1 contrast)
    readonly property color well: tinted(inkOn, dark ? 0.06 : 0.045)
    readonly property color hairline: dark ? Qt.rgba(0.84, 0.93, 0.99, 0.14)
                                           : Qt.rgba(0.18, 0.36, 0.48, 0.14)
    readonly property color edge: tinted(inkOn, dark ? 0.38 : 0.34)  // crisp frosted control border
    readonly property color wash: tinted(inkOn, dark ? 0.07 : 0.05)
    readonly property color press: tinted(inkOn, dark ? 0.12 : 0.09)
    readonly property color text: inkOn
    readonly property color soft: dark ? "#B3C9D8" : "#355161"       // >= 6.8:1 on frosted snow sheet
    readonly property color faint: dark ? "#85A2B5" : "#4E6A7A"      // >= 4.9:1 on frosted snow sheet
    // ---- primary colour: Alpine Clear Sky Blue + Garden Moss Green ----
    readonly property color skyBlue: dark ? "#7AC0EC" : "#256DA8"
    readonly property color mossGreen: dark ? "#7CC49A" : "#2B6644"
    readonly property color snowWhite: dark ? "#E8F2F9" : "#F8FCFF"
    readonly property color primary: dark ? "#7EC2EE" : "#1F66A3"    // >= 5.6:1 contrast on light sheet
    readonly property color onPrimary: dark ? "#091B29" : "#FFFFFF"
    readonly property color primaryContainer: dark ? "#1B3A52" : "#D6EAF8"
    readonly property color onPrimaryContainer: dark ? "#DDF0FC" : "#10395C"
    // ---- companion state lights ----
    readonly property color lamp: dark ? "#93D1AA" : "#2B6644"       // garden moss green: calm companion
    readonly property color calm: primary
    readonly property color think: dark ? "#A5C8F2" : "#385E9B"      // alpine glacier blue: thinking
    readonly property color warm: dark ? "#E8B968" : "#8F5712"       // sunlit oil-ochre: waiting
    readonly property color sign: dark ? "#F09582" : "#A63726"       // terracotta seal: Gate 2 signature
    readonly property color good: dark ? "#7FD0A1" : "#226643"       // garden moss: online, done
    readonly property color focus: primary
    // the seal: what asks for you
    readonly property color seal: "#B4412B"
    readonly property color sealText: "#FFF7F0"
    readonly property color sealSoft: Qt.rgba(1, 0.965, 0.94, 0.86)
    readonly property color inverse: dark ? "#EDF5FA" : "#152630"
    readonly property color inverseText: dark ? "#152630" : "#F6FAFD"
    readonly property color inverseSoft: dark ? "#355161" : "#B3C9D8"
    readonly property color gold: dark ? "#E0BE74" : "#9E7324"
    // dot-matrix numerals: lit and unlit dots
    readonly property color dotOn: text
    readonly property color dotOff: tinted(inkOn, dark ? 0.12 : 0.12)
    readonly property color shadow: dark ? Qt.rgba(0.02, 0.06, 0.10, 0.55) : Qt.rgba(0.14, 0.28, 0.38, 0.14)

    // ---- the cards' oil-painting glazes (alpine sky, moss garden, snow-glacier, ochre, slate) ----
    readonly property var glazes: ({
        "jilan":  { zh: "晴空蓝", en: "Alpine Sky",  base: "#2E79B8", deep: "#153D69", light: "#CBE8FA", glow: "#2E79B8" },
        "qingci": { zh: "苔藓绿", en: "Garden Moss", base: "#357551", deep: "#183D2A", light: "#C9EBD6", glow: "#357551" },
        "yanzhi": { zh: "玫瑰园", en: "Garden Rose", base: "#A64A62", deep: "#511C2D", light: "#F5CDD8", glow: "#A64A62" },
        "gutong": { zh: "油画赭", en: "Oil Ochre",   base: "#8C6834", deep: "#3F2D12", light: "#F5DFB5", glow: "#8C6834" },
        "dai":    { zh: "雪峰青", en: "Glacier",     base: "#3B5B78", deep: "#182A3B", light: "#D4E8F7", glow: "#3B5B78" },
        "ouhe":   { zh: "暮山紫", en: "Alpine Dusk", base: "#5B6496", deep: "#282D4F", light: "#DCE0FA", glow: "#5B6496" },
        "zhusha": { zh: "朱砂印", en: "Cinnabar",    base: "#B4412B", deep: "#5E190C", light: "#FFD5BE", glow: "#B4412B" }
    })
    readonly property var deviceGlazes: ["jilan", "qingci", "dai", "gutong", "ouhe", "yanzhi"]
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

    // ---- type: modern geometric sans-serif (Instagram-inspired clean clarity) ----
    readonly property string sans: "Google Sans"
    readonly property string serif: sans     // unified modern geometric sans across display & voice
    readonly property string mono: Qt.platform.os === "windows" ? "Consolas" : (Qt.platform.os === "osx" ? "Menlo" : "monospace")
    readonly property int meta: 12
    readonly property int body: 14
    readonly property int voice: 16
    readonly property int lead: 18
    readonly property int title: 22
    readonly property int headline: 32
    readonly property int display: 42        // clean modern display greeting

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
