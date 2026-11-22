// Aesper: the soft parts of Studio's pages. A misty backdrop, a sheet with a
// torn painted half, a serif display line, the companion as a glowing figure,
// a link with a pen and a hand-drawn wavy underline, and motion with a little
// bounce. This singleton holds what they share: the mist's colours in both
// worlds, the spacing scale, the display face and the springs.
pragma Singleton
import QtQuick
import ".."

QtObject {
    // ---- the alpine mist (Day: clear sky blue, alpine snow white, garden moss green; Night: glacial dusk & pine) ----
    readonly property color mistGround: Theme.dark ? "#0F1A24" : "#EAF3FA"
    readonly property var mist: Theme.dark ? ["#1D3C5C", "#193A32", "#284B6E", "#24473A", "#162C42"]
                                           : ["#89C2EB", "#DCEEFB", "#88BD97", "#B8DCF2", "#A9D3B4"]
    readonly property color sheet: Theme.dark ? Qt.rgba(0.086, 0.141, 0.184, 0.90)
                                              : Qt.rgba(0.984, 0.996, 1.000, 0.88)
    readonly property color ink: Theme.dark ? "#EDF5FA" : "#152630"          // crisp alpine slate ink
    readonly property color caption: Theme.dark ? "#9BB6C8" : "#446273"      // >= 5.2:1 on frosted sheet

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

    // ---- type: modern geometric sans-serif (Instagram-inspired editorial display) ----
    readonly property string display: Theme.sans
    readonly property int hero: 56
    readonly property int headline: 38

    // ---- springs: subtle, crisp alpine elasticity ----
    readonly property real spring: 4.2
    readonly property real damping: 0.28
    readonly property real overshoot: Theme.still ? 0 : 1.4
    readonly property int arrive: Theme.still ? 120 : 520
    readonly property int stagger: Theme.still ? 0 : 60
}
