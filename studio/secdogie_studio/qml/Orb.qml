// The companion's light: glow rings and a core, tinted by its mood, breathing
// (the one authored motion; still when the person asked for less motion).
import QtQuick
import "."

Item {
    id: orb
    property real size: 84
    property string mood: "rest"
    property int held: 0                        // things waiting for the person: a warm mark
    property bool lit: false                    // hovered or focused: fully bright at rest
    implicitWidth: size
    implicitHeight: size

    property color tint: Theme.moodColor(mood)
    Behavior on tint { ColorAnimation { duration: Theme.still ? 300 : 1200; easing.type: Easing.InOutSine } }
    property real breath: 0
    readonly property real b: Theme.still ? 0.5 : breath
    readonly property int period: mood === "waiting" ? 1700 : (mood === "thinking" ? 2600 : 4200)
    readonly property real k: size / 84
    SequentialAnimation on breath {
        running: !Theme.still
        loops: Animation.Infinite
        NumberAnimation { from: 0; to: 1; duration: orb.period; easing.type: Easing.InOutSine }
        NumberAnimation { from: 1; to: 0; duration: orb.period; easing.type: Easing.InOutSine }
    }
    opacity: mood === "rest" ? (lit ? 0.95 : 0.5 + 0.18 * b) : 0.95
    Behavior on opacity { NumberAnimation { duration: 400 } }

    Repeater {
        model: [0.018, 0.026, 0.036, 0.05, 0.07, 0.1, 0.15, 0.24, 0.38]
        Rectangle {
            required property real modelData
            required property int index
            anchors.centerIn: parent
            width: (84 - index * 7.5 + orb.b * (9 - index)) * orb.k
            height: width
            radius: width / 2
            color: Theme.tinted(orb.tint, modelData * (0.8 + 0.2 * orb.b))
        }
    }
    Rectangle {
        anchors.centerIn: parent
        width: (16 + orb.b * 2) * orb.k
        height: width
        radius: width / 2
        gradient: Gradient {
            GradientStop { position: 0.0; color: Qt.lighter(orb.tint, 1.5) }
            GradientStop { position: 1.0; color: orb.tint }
        }
    }
    Rectangle {
        visible: orb.held > 0
        width: 7; height: 7; radius: 3.5
        color: Theme.warm
        x: parent.width - 18 * orb.k; y: 14 * orb.k
    }
}
