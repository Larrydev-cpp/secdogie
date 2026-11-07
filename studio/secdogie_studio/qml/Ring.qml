// Where the keyboard is; shown only when focus came from the keyboard.
import QtQuick
import "."

Rectangle {
    property real round: 0
    anchors.fill: parent
    anchors.margins: -4
    radius: round + 4
    color: "transparent"
    border.width: 2
    border.color: Theme.focus
}
