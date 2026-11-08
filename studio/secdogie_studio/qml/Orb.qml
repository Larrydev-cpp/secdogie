// The companion's light: a soft radial glow around a small bright core,
// tinted by its mood and breathing (the one authored motion). It keeps
// still when the person asked for less motion, or when it is drawn small
// as a mark (``breathing: false``).
import QtQuick
import QtQuick.Shapes
import "."

Item {
    id: orb
    property real size: 84
    property string mood: "rest"
    property int held: 0                        // things waiting for the person: a warm mark
    property bool lit: false                    // hovered or focused: fully bright at rest
    property bool breathing: true
    implicitWidth: size
    implicitHeight: size
    Accessible.ignored: true

    property color tint: Theme.moodColor(mood)
    Behavior on tint { ColorAnimation { duration: Theme.still ? 300 : 1200; easing.type: Easing.InOutSine } }
    property real breath: 0
    readonly property bool moving: breathing && !Theme.still
    readonly property real b: moving ? breath : 0.5
    readonly property int period: mood === "waiting" ? 1700 : (mood === "thinking" ? 2600 : 4200)
    SequentialAnimation on breath {
        running: orb.moving
        loops: Animation.Infinite
        NumberAnimation { from: 0; to: 1; duration: orb.period; easing.type: Easing.InOutSine }
        NumberAnimation { from: 1; to: 0; duration: orb.period; easing.type: Easing.InOutSine }
    }
    opacity: mood === "rest" ? (lit ? 1 : 0.66 + 0.22 * b) : 1
    Behavior on opacity { NumberAnimation { duration: 400 } }

    Shape {                                     // the glow
        anchors.centerIn: parent
        width: orb.size
        height: orb.size
        scale: 0.86 + 0.14 * orb.b
        antialiasing: true
        ShapePath {
            strokeColor: "transparent"
            fillGradient: RadialGradient {
                centerX: orb.size / 2; centerY: orb.size / 2; centerRadius: orb.size / 2
                focalX: orb.size / 2; focalY: orb.size / 2
                GradientStop { position: 0.0; color: Theme.tinted(orb.tint, 0.85) }
                GradientStop { position: 0.14; color: Theme.tinted(orb.tint, 0.58) }
                GradientStop { position: 0.32; color: Theme.tinted(orb.tint, 0.24) }
                GradientStop { position: 0.56; color: Theme.tinted(orb.tint, 0.08) }
                GradientStop { position: 0.8; color: Theme.tinted(orb.tint, 0.02) }
                GradientStop { position: 1.0; color: Theme.tinted(orb.tint, 0.0) }
            }
            PathAngleArc { centerX: orb.size / 2; centerY: orb.size / 2; radiusX: orb.size / 2; radiusY: orb.size / 2
                           startAngle: 0; sweepAngle: 360 }
        }
    }
    Shape {                                     // the core: a small sphere, lit from the upper left
        readonly property real d: Math.max(7, orb.size * (0.21 + 0.02 * orb.b))
        anchors.centerIn: parent
        width: d
        height: d
        antialiasing: true
        ShapePath {
            strokeColor: "transparent"
            fillGradient: RadialGradient {
                centerX: parent.d / 2; centerY: parent.d / 2; centerRadius: parent.d / 2
                focalX: parent.d * 0.38; focalY: parent.d * 0.34
                GradientStop { position: 0.0; color: Qt.lighter(orb.tint, 1.75) }
                GradientStop { position: 0.55; color: Qt.lighter(orb.tint, 1.2) }
                GradientStop { position: 1.0; color: orb.tint }
            }
            PathAngleArc { centerX: parent.d / 2; centerY: parent.d / 2; radiusX: parent.d / 2; radiusY: parent.d / 2
                           startAngle: 0; sweepAngle: 360 }
        }
    }
    Rectangle {
        visible: orb.held > 0
        width: 7; height: 7; radius: 3.5
        color: Theme.warm
        x: parent.width - 18 * orb.size / 84; y: 14 * orb.size / 84
    }
}
