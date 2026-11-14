// A sheet of the home window, set the way Google sets a page: a flat tonal
// surface with generous corners, its title, an optional chip at the right,
// and whatever it holds below. (The wallet's cards are WalletCard; this is
// the paper they sit beside.) `kind`: "paper" (the usual), "seal" (cinnabar:
// it asks for you) or "inverse" (ink on paper, paper on ink).
import QtQuick
import "."

Item {
    id: card
    property string title
    property string chip
    property bool chipLive: false            // the chip as a live mark (a cinnabar dot)
    property string kind: "paper"
    default property alias content: body.data
    readonly property color fg: kind === "seal" ? Theme.sealText : (kind === "inverse" ? Theme.inverseText : Theme.text)
    readonly property color fgSoft: kind === "seal" ? Theme.sealSoft : (kind === "inverse" ? Theme.inverseSoft : Theme.soft)
    readonly property color bg: kind === "seal" ? Theme.seal : (kind === "inverse" ? Theme.inverse : Theme.panel)
    property real pad: 24

    Rectangle {
        id: sheet
        anchors.fill: parent
        radius: Theme.round
        color: card.bg
        border.color: card.kind === "paper" && Theme.dark ? Theme.hairline : "transparent"
        Behavior on color { ColorAnimation { duration: Theme.enter } }
    }
    Text {
        id: heading
        x: card.pad
        y: card.pad - 2
        text: card.title
        color: card.fg
        font.pixelSize: 16
        font.weight: Font.Medium
        textFormat: Text.PlainText
    }
    Rectangle {
        visible: card.chip !== ""
        anchors.right: parent.right
        anchors.rightMargin: card.pad - 4
        anchors.verticalCenter: heading.verticalCenter
        height: 24
        width: chipRow.implicitWidth + 18
        radius: 8
        color: card.kind === "paper" ? Theme.wash : Theme.tinted(card.fg, 0.14)
        Row {
            id: chipRow
            anchors.centerIn: parent
            spacing: 5
            Rectangle { visible: card.chipLive; width: 6; height: 6; radius: 3; color: Theme.seal
                        anchors.verticalCenter: parent.verticalCenter }
            Text { text: card.chip; color: card.fgSoft; font.pixelSize: 12; font.weight: Font.Medium
                   textFormat: Text.PlainText; font.features: ({ "tnum": 1 }) }
        }
    }
    Item {
        id: body
        x: card.pad
        y: heading.y + heading.height + 14
        width: card.width - 2 * card.pad
        height: card.height - y - card.pad
    }
}
