// A quiet way somewhere else: text that underlines when you reach it.
import QtQuick
import QtQuick.Controls
import "."

AbstractButton {
    id: link
    property int size: Theme.body
    hoverEnabled: true
    implicitHeight: 32
    padding: 0
    Keys.onReturnPressed: link.animateClick()
    Keys.onEnterPressed: link.animateClick()
    HoverHandler { cursorShape: Qt.PointingHandCursor }
    contentItem: Text {
        text: link.text
        textFormat: Text.PlainText
        font.pixelSize: link.size
        font.underline: link.hovered
        color: link.hovered || link.visualFocus ? Theme.text : Theme.soft
        verticalAlignment: Text.AlignVCenter
    }
    background: Item { Ring { visible: link.visualFocus; round: 6 } }
}
