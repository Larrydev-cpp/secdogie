// The design tokens every window draws with (studio/DESIGN.md): colour, type,
// motion. One instance for the whole app.
pragma Singleton
import QtQuick

QtObject {
    // The person asked their computer for less motion; each window sets this
    // from the bridge (P.still) when it opens.
    property bool still: false

    // ---- colour: night, soft light ----
    readonly property color ground: "#0D0F14"                        // the home window
    readonly property color sunk: "#0A0B0F"                          // its rail, a step below
    readonly property color panel: "#151820"                         // a raised area inside it
    readonly property color panelHi: "#1B1F29"                       // ... hovered, or raised again
    readonly property color card: Qt.rgba(0.071, 0.078, 0.106, 0.96) // the light's sheets
    readonly property color well: Qt.rgba(1, 1, 1, 0.04)             // an inset area: the rules editor
    readonly property color hairline: Qt.rgba(1, 1, 1, 0.08)         // edges and dividers that carry nothing
    readonly property color edge: Qt.rgba(1, 1, 1, 0.34)             // a control's boundary: 3:1 on any surface here
    readonly property color wash: Qt.rgba(1, 1, 1, 0.06)             // a quiet control, hovered
    readonly property color press: Qt.rgba(1, 1, 1, 0.11)            // ... and pressed, or the open page
    // text, tinted warm rather than grey (4.5:1 or more on every surface above)
    readonly property color text: "#ECE6DC"
    readonly property color soft: "#BDB5AA"
    readonly property color faint: "#948C82"
    // moods; calm is also where the keyboard is
    readonly property color calm: "#8FB6FF"
    readonly property color think: "#B9A7FF"
    readonly property color warm: "#F2C27F"
    readonly property color sign: "#F3A983"
    readonly property color focus: calm

    function moodColor(mood) {
        switch (mood) {
        case "thinking": return think
        case "waiting": return warm
        case "signing": return sign
        default: return calm
        }
    }
    function tinted(c, a) { return Qt.rgba(c.r, c.g, c.b, a) }

    // ---- type: five steps on a 1.2 scale ----
    readonly property int meta: 12
    readonly property int body: 14
    readonly property int lead: 17
    readonly property int title: 20
    readonly property int display: 28        // the first meeting's headings

    // ---- motion ----
    readonly property int enter: still ? 120 : 240
    readonly property int exit: still ? 90 : 160              // leaving takes two thirds of arriving
    readonly property int touch: 120                          // hover and press
    readonly property int whisperIn: still ? 120 : 320
    readonly property int whisperOut: still ? 400 : 1100      // let go of slowly: it may be mid-read
    readonly property real rise: still ? 0 : 12               // how far a sheet travels as it fades in
}
