// A raised area of the home window: one group of related things.
import QtQuick
import "."

Rectangle {
    id: panel
    default property alias content: column.data
    property alias spacing: column.spacing
    property real inset: 20
    implicitHeight: column.implicitHeight + 2 * inset
    radius: 14
    color: Theme.panel
    border.color: Theme.hairline
    Column {
        id: column
        x: panel.inset
        y: panel.inset
        width: panel.width - 2 * panel.inset
        spacing: 14
    }
}
