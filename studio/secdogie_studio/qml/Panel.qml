// A raised area of the home window: one group of related things.
import QtQuick
import "."

Rectangle {
    id: panel
    default property alias content: column.data
    property alias spacing: column.spacing
    property real inset: 20
    implicitHeight: column.implicitHeight + 2 * inset
    radius: 16
    color: Theme.panel
    border.color: Theme.hairline
    Rectangle {                                 // a faint top light
        anchors.fill: parent
        radius: parent.radius
        gradient: Gradient {
            GradientStop { position: 0.0; color: Qt.rgba(1, 1, 1, 0.035) }
            GradientStop { position: 0.4; color: "transparent" }
        }
    }
    Column {
        id: column
        x: panel.inset
        y: panel.inset
        width: panel.width - 2 * panel.inset
        spacing: 14
    }
}
