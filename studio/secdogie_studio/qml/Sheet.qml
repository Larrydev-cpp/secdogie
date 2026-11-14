// The one surface every card on the desktop is made of: letter paper at night, a hairline, a soft lift.
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
    }
}
