// Depth, not a halo: six soft layers, each a little wider and a little lower.
import QtQuick

Item {
    id: shadow
    property real radius: 20
    property real strength: 1
    Repeater {
        model: 6
        Rectangle {
            required property int index
            readonly property real spread: index * 2.5
            readonly property real drop: 3 + index * 1.5
            anchors.fill: parent
            anchors.leftMargin: -spread
            anchors.rightMargin: -spread
            anchors.topMargin: drop - spread
            anchors.bottomMargin: -drop - spread
            radius: shadow.radius + spread
            color: Qt.rgba(0, 0, 0, 0.055 * shadow.strength)
        }
    }
}
