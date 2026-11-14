// One tab of a small tab row: the open one is marked underneath.
import QtQuick
import QtQuick.Controls
import "."

AbstractButton {
    id: tab
    property bool current: false
    hoverEnabled: true
    implicitHeight: 44
    padding: 0
    font.pixelSize: Theme.body
    Accessible.role: Accessible.PageTab
    Accessible.selected: tab.current
    Keys.onReturnPressed: tab.animateClick()
    Keys.onEnterPressed: tab.animateClick()
    HoverHandler { cursorShape: Qt.PointingHandCursor }
    contentItem: Text {
        text: tab.text
        textFormat: Text.PlainText
        font.pixelSize: tab.font.pixelSize
        color: tab.current || tab.hovered ? Theme.text : Theme.soft
        verticalAlignment: Text.AlignVCenter
    }
    background: Item {
        Rectangle {
            anchors.bottom: parent.bottom
            anchors.bottomMargin: 6
            width: parent.width
            height: 2
            radius: 1
            color: Theme.calm
            opacity: tab.current ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: Theme.touch } }
        }
        Ring { visible: tab.visualFocus; round: 6 }
    }
}
