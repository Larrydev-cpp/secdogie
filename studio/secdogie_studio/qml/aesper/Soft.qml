// Aesper: the soft parts of Studio's pages. A misty backdrop, a sheet with a
// torn painted half, a serif display line, the companion as a glowing figure,
// a link with a pen and a hand-drawn wavy underline, and motion with a little
// bounce. This singleton holds what they share: the mist's colours in both
// worlds, the spacing scale, the display face and the springs.
pragma Singleton
import QtQuick
import ".."

QtObject {
    // ---- the mist (宣纸 by day: peach, lilac, warm grey; 墨 by night: plum, indigo, rose) ----
    readonly property color mistGround: Theme.dark ? "#100F17" : "#F5F1EF"
    readonly property var mist: Theme.dark ? ["#4B2A4E", "#272B5C", "#5C3346", "#1E2B4A", "#3A2350"]
                                           : ["#F5C6B3", "#CBC0E4", "#F3D5CB", "#D9CFEA", "#F8D9C9"]
    readonly property color sheet: Theme.dark ? "#17161E" : "#FFFFFF"
    readonly property color ink: Theme.dark ? "#F2EEE8" : "#141414"         // the display line, the underline
    readonly property color caption: Theme.dark ? "#A39FAE" : "#7A7680"      // 4.6:1 or more on the sheet

    // ---- the spacing scale: an 8-point rhythm with two half steps ----
    readonly property int s1: 4
    readonly property int s2: 8
    readonly property int s3: 12
    readonly property int s4: 16
    readonly property int s5: 24
    readonly property int s6: 32
    readonly property int s7: 48
    readonly property int s8: 64
    readonly property int s9: 96

    // ---- type: a serif display line (Latin), the platform's serif for Chinese ----
    readonly property string display: Theme.lang === "en" ? "Newsreader Display" : Theme.serif
    readonly property int hero: 60
    readonly property int headline: 40

    // ---- springs: Q弹. A press gives way and springs back; things arrive with a small overshoot ----
    readonly property real spring: 4.2          // stiffness
    readonly property real damping: 0.24        // < 1: it bounces a little before it settles
    readonly property real overshoot: Theme.still ? 0 : 1.7
    readonly property int arrive: Theme.still ? 120 : 620
    readonly property int stagger: Theme.still ? 0 : 70
}
