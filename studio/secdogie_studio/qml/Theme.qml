// The design tokens every window draws with (studio/DESIGN.md): colour, type,
// motion. One instance for the whole app.
//
// The world is a night of correspondence. The ground is blue ink, not black.
// Two lights share it:
//   * lamp (warm) is the companion: its light, its mark, its voice;
//   * frost (cool) is you: your words, your controls, where your keyboard is.
// The companion writes in a serif, like a letter; the interface speaks sans.
pragma Singleton
import QtQuick

QtObject {
    // The person asked their computer for less motion; each window sets this
    // from the bridge (P.still) when it opens.
    property bool still: false

    // ---- colour ----
    readonly property color ground: "#0E1322"                        // night: the home window
    readonly property color sunk: "#0A0E1A"                          // deeper night: its rail
    readonly property color panel: "#172038"                         // letter paper: what is raised (the composer, a card)
    readonly property color panelHi: "#1E2843"                       // ... raised again
    readonly property color card: Qt.rgba(0.090, 0.125, 0.220, 0.97) // the light's sheets, on the desktop
    readonly property color well: Qt.rgba(0.925, 0.906, 0.863, 0.035) // an inset area: the rules editor
    readonly property color hairline: Qt.rgba(0.925, 0.906, 0.863, 0.09) // edges and dividers that carry nothing
    readonly property color edge: Qt.rgba(0.925, 0.906, 0.863, 0.40)  // a control's boundary: 3:1 on every surface
    readonly property color wash: Qt.rgba(0.925, 0.906, 0.863, 0.06)  // a quiet control, hovered
    readonly property color press: Qt.rgba(0.925, 0.906, 0.863, 0.11) // ... and pressed
    // text: moon-white, then mist and dim, tinted from the night (4.5:1 or more on every surface)
    readonly property color text: "#ECE7DC"
    readonly property color soft: "#AEB4C8"
    readonly property color faint: "#8C93AC"
    // the two lights, and the companion's moods
    readonly property color lamp: "#F2CF86"                          // the companion, at rest
    readonly property color calm: "#9FB8EE"                          // frost: you, your controls, the keyboard
    readonly property color think: "#B9ABF0"                         // dusk: it is working
    readonly property color warm: "#F0A955"                          // amber: it is waiting on you; a step it stopped
    readonly property color sign: "#E8957A"                          // seal: a signature is needed
    readonly property color focus: calm

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

    // ---- motion ----
    readonly property int enter: still ? 120 : 240
    readonly property int exit: still ? 90 : 160              // leaving takes two thirds of arriving
    readonly property int touch: 120                          // hover and press
    readonly property int whisperIn: still ? 120 : 320
    readonly property int whisperOut: still ? 400 : 1100      // let go of slowly: it may be mid-read
    readonly property real rise: still ? 0 : 12               // how far a sheet travels as it fades in
}
