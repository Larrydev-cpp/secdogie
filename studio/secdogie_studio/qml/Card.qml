// One card of the home window: a soft-cornered sheet with its title, an
// optional chip at the right ("30 天", "直连"), and whatever it holds below.
// `kind`: "paper" (the usual), "seal" (cinnabar: it asks for you) or
// "inverse" (ink on paper, paper on ink: what the gates hold).
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
    property real pad: 20

    Rectangle {                                 // its shadow: offset and soft, never a halo
        anchors.fill: sheet
        anchors.topMargin: 6
        anchors.leftMargin: 2
        anchors.rightMargin: 2
        anchors.bottomMargin: -6
        radius: Theme.round
        color: Theme.shadow
        opacity: 0.55
    }
    Rectangle {
        id: sheet
        anchors.fill: parent
        radius: Theme.round
        color: card.bg
        border.color: card.kind === "paper" ? Theme.hairline : "transparent"
        Behavior on color { ColorAnimation { duration: Theme.enter } }
    }
    Text {
        id: heading
        x: card.pad
        y: card.pad - 2
        text: card.title
        color: card.fg
        font.pixelSize: Theme.body
        font.weight: Font.DemiBold
        textFormat: Text.PlainText
    }
    Rectangle {
        visible: card.chip !== ""
        anchors.right: parent.right
        anchors.rightMargin: card.pad - 4
        anchors.verticalCenter: heading.verticalCenter
        height: 22
        width: chipRow.implicitWidth + 16
        radius: 11
        color: card.kind === "paper" ? Theme.wash : Theme.tinted(card.fg, 0.14)
        Row {
            id: chipRow
            anchors.centerIn: parent
            spacing: 5
            Rectangle { visible: card.chipLive; width: 6; height: 6; radius: 3; color: Theme.seal
                        anchors.verticalCenter: parent.verticalCenter }
            Text { text: card.chip; color: card.fgSoft; font.pixelSize: 11; font.letterSpacing: 0.6
                   textFormat: Text.PlainText; font.features: ({ "tnum": 1 }) }
        }
    }
    Item {
        id: body
        x: card.pad
        y: heading.y + heading.height + 12
        width: card.width - 2 * card.pad
        height: card.height - y - card.pad
    }
}
