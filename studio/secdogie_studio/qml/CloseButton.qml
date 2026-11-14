// Close: a drawn cross (two 1.5 px strokes, the weight of the text beside it).
import QtQuick
import QtQuick.Controls
import "."

AbstractButton {
    id: close
    text: "收起"                                   // its accessible name; the cross is drawn
    hoverEnabled: true
    implicitWidth: 36
    implicitHeight: 36
    padding: 0
    Keys.onReturnPressed: close.animateClick()
    Keys.onEnterPressed: close.animateClick()
    HoverHandler { cursorShape: Qt.PointingHandCursor }
    background: Rectangle {
        radius: width / 2
        color: close.down ? Theme.press : (close.hovered ? Theme.wash : "transparent")
        Behavior on color { ColorAnimation { duration: Theme.touch } }
        Ring { visible: close.visualFocus; round: parent.radius }
    }
    contentItem: Item {
        Repeater {
            model: [45, -45]
            Rectangle {
                required property real modelData
                anchors.centerIn: parent
                width: 14
                height: 1.5
                radius: 0.75
                rotation: modelData
                antialiasing: true
                color: close.hovered || close.visualFocus ? Theme.text : Theme.soft
            }
        }
    }
}
