// A small state label: tinted, quiet, never the only cue (the words say it).
import QtQuick
import "."

Rectangle {
    id: pill
    property string text
    property color tint: Theme.faint
    implicitWidth: label.implicitWidth + 18
    implicitHeight: 22
    radius: 11
    color: Theme.tinted(tint, 0.14)
    border.color: Theme.tinted(tint, 0.22)
    Text {
        id: label
        anchors.centerIn: parent
        text: pill.text
        textFormat: Text.PlainText
        color: Qt.lighter(pill.tint, 1.12)
        font.pixelSize: Theme.meta
    }
}
