// A button: primary (filled with its tint, the primary colour by default) or quiet
// (outlined). Hover, pressed, disabled and
// a keyboard-only focus ring; Space or Enter presses it; nothing shifts on press.
import QtQuick
import QtQuick.Controls
import "."

Button {
    id: pill
    property bool primary: false
    property color tint: Theme.calm
    hoverEnabled: true
    implicitHeight: 40
    leftPadding: 20
    rightPadding: 20
    topPadding: 0
    bottomPadding: 0
    font.pixelSize: Theme.body
    font.weight: Font.Medium
    Keys.onReturnPressed: pill.animateClick()
    Keys.onEnterPressed: pill.animateClick()
    HoverHandler { cursorShape: Qt.PointingHandCursor }
    contentItem: Text {
        text: pill.text
        textFormat: Text.PlainText
        font: pill.font
        color: pill.primary ? (Theme.dark ? "#121318" : "#FFFFFF") : Theme.text
        opacity: pill.enabled ? 1 : 0.4
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    background: Rectangle {
        radius: height / 2
        opacity: pill.enabled ? 1 : 0.4
        color: pill.primary ? (pill.down ? Qt.darker(pill.tint, 1.12) : (pill.hovered ? Qt.lighter(pill.tint, 1.08) : pill.tint))
                            : (pill.down ? Theme.press : (pill.hovered ? Theme.wash : "transparent"))
        border.color: pill.primary ? "transparent" : Theme.edge
        Behavior on color { ColorAnimation { duration: Theme.touch } }
        Ring { visible: pill.visualFocus; round: parent.radius }
    }
}
