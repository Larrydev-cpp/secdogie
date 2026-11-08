// A button: primary (tinted) or quiet (outlined). Hover, pressed, disabled and
// a keyboard-only focus ring; Space or Enter presses it; nothing shifts on press.
import QtQuick
import QtQuick.Controls
import "."

Button {
    id: pill
    property bool primary: false
    property color tint: Theme.calm
    property string trailing                    // an icon after the words ("arrow" for "go on")
    hoverEnabled: true
    implicitHeight: 40
    leftPadding: 20
    rightPadding: 20
    topPadding: 0
    bottomPadding: 0
    font.pixelSize: Theme.body
    Keys.onReturnPressed: pill.animateClick()
    Keys.onEnterPressed: pill.animateClick()
    HoverHandler { cursorShape: Qt.PointingHandCursor }
    contentItem: Item {
        implicitWidth: words.implicitWidth + (pill.trailing ? 24 : 0)
        implicitHeight: words.implicitHeight
        opacity: pill.enabled ? 1 : 0.4
        Row {
            anchors.centerIn: parent
            spacing: 8
            Text {
                id: words
                text: pill.text
                textFormat: Text.PlainText
                font: pill.font
                color: pill.primary ? Qt.lighter(pill.tint, 1.12) : Theme.text
                anchors.verticalCenter: parent.verticalCenter
            }
            Icon {
                visible: pill.trailing !== ""
                name: pill.trailing
                size: 16
                color: words.color
                anchors.verticalCenter: parent.verticalCenter
            }
        }
    }
    background: Rectangle {
        radius: height / 2
        opacity: pill.enabled ? 1 : 0.4
        color: pill.primary ? Theme.tinted(pill.tint, pill.down ? 0.34 : (pill.hovered ? 0.25 : 0.16))
                            : (pill.down ? Theme.press : (pill.hovered ? Theme.wash : "transparent"))
        border.color: pill.primary ? Theme.tinted(pill.tint, 0.62) : Theme.edge
        Behavior on color { ColorAnimation { duration: Theme.touch } }
        Ring { visible: pill.visualFocus; round: parent.radius }
    }
}
