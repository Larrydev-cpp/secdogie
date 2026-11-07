// The one surface every card is made of: deep ink, a hairline, a soft lift.
import QtQuick
import "."

Item {
    id: sheet
    default property alias content: face.data
    property real radius: 20
    property color color: Theme.card
    Shadow { anchors.fill: parent; radius: sheet.radius }
    Rectangle {
        id: face
        anchors.fill: parent
        radius: sheet.radius
        color: sheet.color
        border.color: Theme.hairline
        Rectangle {          // a faint top light, so the sheet reads as a surface, not a hole
            anchors.fill: parent
            radius: parent.radius
            gradient: Gradient {
                GradientStop { position: 0.0; color: Qt.rgba(1, 1, 1, 0.04) }
                GradientStop { position: 0.3; color: "transparent" }
            }
        }
    }
}
