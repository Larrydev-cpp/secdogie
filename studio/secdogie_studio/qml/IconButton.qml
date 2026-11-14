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
            color: btn.primary ? (Theme.dark ? "#121318" : "#FFFFFF") : (btn.hovered || btn.visualFocus ? Theme.text : Theme.soft)
        }
    }
    background: Rectangle {
        radius: width / 2
        opacity: btn.enabled ? 1 : 0.45
        color: btn.primary ? (btn.down ? Qt.darker(btn.tint, 1.12) : (btn.hovered ? Qt.lighter(btn.tint, 1.08) : btn.tint))
                           : (btn.down ? Theme.press : (btn.hovered ? Theme.wash : "transparent"))
        border.color: "transparent"
        Behavior on color { ColorAnimation { duration: Theme.touch } }
        Ring { visible: btn.visualFocus; round: parent.radius }
    }
}
