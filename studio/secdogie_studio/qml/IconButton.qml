// A round button with a drawn icon; its text is the accessible name.
import QtQuick
import QtQuick.Controls
import "."

AbstractButton {
    id: btn
    property string symbol                      // which drawn icon (Icon.qml)
    property bool primary: false
    property color tint: Theme.calm
    property real diameter: 36
    property real iconSize: 18
    hoverEnabled: true
    implicitWidth: diameter
    implicitHeight: diameter
    padding: 0
    Keys.onReturnPressed: btn.animateClick()
    Keys.onEnterPressed: btn.animateClick()
    HoverHandler { cursorShape: Qt.PointingHandCursor }
    contentItem: Item {
        Icon {
            anchors.centerIn: parent
            name: btn.symbol
            size: btn.iconSize
            opacity: btn.enabled ? 1 : 0.45
            color: btn.primary ? Qt.lighter(btn.tint, 1.15) : (btn.hovered || btn.visualFocus ? Theme.text : Theme.soft)
        }
    }
    background: Rectangle {
        radius: width / 2
        opacity: btn.enabled ? 1 : 0.45
        color: btn.primary ? Theme.tinted(btn.tint, btn.down ? 0.36 : (btn.hovered ? 0.27 : 0.18))
                           : (btn.down ? Theme.press : (btn.hovered ? Theme.wash : "transparent"))
        border.color: btn.primary ? Theme.tinted(btn.tint, 0.55) : "transparent"
        Behavior on color { ColorAnimation { duration: Theme.touch } }
        Ring { visible: btn.visualFocus; round: parent.radius }
    }
}
