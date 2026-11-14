// A thin scrollbar in the palette, not the system's.
import QtQuick
import QtQuick.Controls

ScrollBar {
    id: bar
    padding: 2
    contentItem: Rectangle {
        implicitWidth: 4
        implicitHeight: 4
        radius: 2
        color: Qt.rgba(1, 1, 1, bar.pressed ? 0.42 : (bar.hovered ? 0.32 : 0.2))
        opacity: bar.size < 1.0 ? 1 : 0
    }
    background: null
}
