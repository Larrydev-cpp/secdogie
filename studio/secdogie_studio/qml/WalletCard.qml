// One card in the wallet, drawn like a bank card (ISO ID-1 proportions).
// Across the top, the strip that shows when the card sits in the stack: its
// title at the left and its figure at the right. At the foot: a line of state
// and the masked key where a card number would be. Device cards carry a dark
// panel at the right with Studio's mark (nine dots, the middle one cinnabar)
// and the card's standing ("Owner", "Member"), where a bank card carries its
// network.
//   kind "glaze": a device, or what waits on you (the cinnabar glaze)
//   kind "dots":  this node: the day as a dome of dots
//   kind "add":   an empty slot: bring in another device
// `code` puts large numerals in the middle (the six digits while pairing);
// `stamped` presses the seal 纳 on it once a device is admitted.
import QtQuick
import QtQuick.Controls
import QtQuick.Shapes
import "."

AbstractButton {
    id: wc
    property string kind: "glaze"
    property string glaze: "jilan"
    property string key: ""                    // seeds the art
    property bool panel: false
    property string title
    property string figure
    property string number                     // the tail of a key; drawn after four dots
    property string label                      // on the panel
    property string note                       // a line of state above the number
    property bool live: false                  // a lit dot before the note
    property string code
    property bool stamped: false
    property bool front: true
    property var quarters: []
    property int now: -1
    readonly property real k: width / 420
    readonly property real pad: 22 * k
    readonly property color ink: "#FFFFFF"
    readonly property color inkSoft: Qt.rgba(1, 1, 1, 0.80)

    implicitWidth: 420
    implicitHeight: width / Theme.cardRatio
    hoverEnabled: true
    focusPolicy: Qt.TabFocus
    Accessible.role: Accessible.Button
    Accessible.name: [title, figure, note, number ? Theme.t("尾号 ", "ending ") + number : ""].filter(s => !!s).join(", ")
    HoverHandler { cursorShape: Qt.PointingHandCursor }
    Keys.onReturnPressed: wc.animateClick()
    Keys.onSpacePressed: wc.animateClick()
    background: null

    // lift a little under the pointer
    property real lifted: (hovered && !pressed) ? 1 : 0
    Behavior on lifted { NumberAnimation { duration: Theme.touch; easing.type: Easing.OutCubic } }
    transform: Translate { y: -3 * wc.lifted }

    contentItem: Item {
        CardArt {
            visible: wc.kind !== "add"
            anchors.fill: parent
            anchors.margins: -margin
            kind: wc.kind === "dots" ? "dots" : "glaze"
            glaze: wc.glaze
            seed: Theme.hash(wc.key || wc.title)
            panel: wc.panel
            radius: Theme.cardRound * wc.k
            lift: Math.max(wc.lifted, wc.front ? 0.5 : 0)
            quarters: wc.quarters
            now: wc.now
        }

        // -- an empty slot: a dashed outline on the ground, its title in the strip, a plus in the middle --
        Rectangle {                                         // opaque: it hides the cards behind it in the stack
            visible: wc.kind === "add"
            anchors.fill: parent
            radius: Theme.cardRound * wc.k
            color: Theme.ground
        }
        Shape {
            visible: wc.kind === "add"
            anchors.fill: parent
            ShapePath {
                strokeColor: wc.hovered ? Theme.primary : Theme.edge
                strokeWidth: 1.5
                strokeStyle: ShapePath.DashLine
                dashPattern: [5, 4]
                fillColor: wc.hovered ? Qt.tint(Theme.ground, Theme.wash) : Theme.ground
                PathRectangle { x: 1; y: 1; width: wc.width - 2; height: wc.height - 2; radius: Theme.cardRound * wc.k }
            }
        }
        Row {
            visible: wc.kind === "add"
            x: wc.pad
            y: wc.pad - 2 * wc.k
            height: 26 * wc.k
            spacing: 10 * wc.k
            Rectangle {
                anchors.verticalCenter: parent.verticalCenter
                width: 24 * wc.k; height: width; radius: width / 2
                color: Theme.primaryContainer
                Rectangle { anchors.centerIn: parent; width: 10 * wc.k; height: 1.6 * wc.k; radius: 1; color: Theme.onPrimaryContainer }
                Rectangle { anchors.centerIn: parent; width: 1.6 * wc.k; height: 10 * wc.k; radius: 1; color: Theme.onPrimaryContainer }
            }
            Text { anchors.verticalCenter: parent.verticalCenter; text: wc.title; color: Theme.text
                   font.pixelSize: 16 * wc.k; font.weight: Font.Medium; textFormat: Text.PlainText }
        }
        Text {
            visible: wc.kind === "add" && wc.note !== ""
            x: wc.pad
            anchors.bottom: parent.bottom
            anchors.bottomMargin: wc.pad
            width: wc.width - 2 * wc.pad
            text: wc.note
            color: Theme.soft
            font.pixelSize: 13 * wc.k
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
        }

        // -- the strip at the top: title and figure --
        Row {
            visible: wc.kind !== "add"
            x: wc.pad
            y: wc.pad - 2 * wc.k
            height: 26 * wc.k
            spacing: 9 * wc.k
            Grid {                                                   // the node card wears the mark at its name
                visible: wc.kind === "dots"
                anchors.verticalCenter: parent.verticalCenter
                columns: 3
                spacing: 2.5 * wc.k
                Repeater {
                    model: 9
                    Rectangle { required property int index; width: 4 * wc.k; height: width; radius: width / 2
                                color: index === 4 ? "#F0603F" : wc.ink }
                }
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: wc.title
                color: wc.ink
                font.pixelSize: (wc.kind === "dots" ? 21 : 16) * wc.k
                font.weight: wc.kind === "dots" ? Font.Bold : Font.Medium
                font.letterSpacing: wc.kind === "dots" ? -0.3 : 0
                elide: Text.ElideRight
                width: Math.min(implicitWidth, wc.width * (wc.panel ? 0.6 : 0.62))
                textFormat: Text.PlainText
            }
        }
        Text {
            visible: wc.kind !== "add" && wc.figure !== ""
            anchors.right: parent.right
            anchors.rightMargin: wc.pad
            y: wc.pad - 6 * wc.k
            height: 30 * wc.k
            verticalAlignment: Text.AlignVCenter
            text: wc.figure
            color: wc.ink
            font.pixelSize: (wc.panel ? 15 : 28) * wc.k
            font.weight: wc.panel ? Font.Medium : Font.Bold
            font.features: ({ "tnum": 1 })
            textFormat: Text.PlainText
        }

        // -- the middle: the six digits while pairing --
        Text {
            visible: wc.code !== ""
            anchors.verticalCenter: parent.verticalCenter
            x: wc.pad
            width: (wc.panel ? wc.width * 0.685 : wc.width) - 2 * wc.pad
            horizontalAlignment: Text.AlignHCenter
            text: wc.code
            color: wc.ink
            font.pixelSize: 40 * wc.k
            font.weight: Font.Bold
            font.letterSpacing: 4 * wc.k
            font.features: ({ "tnum": 1 })
            style: Text.Raised
            styleColor: Qt.rgba(0, 0, 0, 0.25)
        }

        // -- the foot: state, and the masked key --
        Column {
            visible: wc.kind !== "add"
            x: wc.pad
            anchors.bottom: parent.bottom
            anchors.bottomMargin: wc.pad - 4 * wc.k
            spacing: 6 * wc.k
            Row {
                visible: wc.note !== ""
                spacing: 7 * wc.k
                Rectangle { visible: wc.live; width: 7 * wc.k; height: width; radius: width / 2; color: "#7CF0BE"
                            anchors.verticalCenter: parent.verticalCenter }
                Text { text: wc.note; color: wc.inkSoft; font.pixelSize: 12.5 * wc.k; textFormat: Text.PlainText
                       font.features: ({ "tnum": 1 })
                       width: Math.min(implicitWidth, wc.width * (wc.panel ? 0.58 : 0.86)); elide: Text.ElideRight }
            }
            Row {
                visible: wc.number !== ""
                spacing: 12 * wc.k
                Row {
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 5 * wc.k
                    Repeater { model: 4
                               Rectangle { width: 5 * wc.k; height: width; radius: width / 2; color: wc.inkSoft } }
                }
                Text { text: wc.number; color: wc.ink; font.pixelSize: 22 * wc.k; font.letterSpacing: 0.6 * wc.k
                       font.features: ({ "tnum": 1 }); textFormat: Text.PlainText }
            }
        }

        // -- the panel: Studio's mark and the card's standing --
        Column {
            visible: wc.panel && wc.kind === "glaze"
            x: wc.width * 0.685
            width: wc.width * 0.315
            anchors.bottom: parent.bottom
            anchors.bottomMargin: wc.pad + 2 * wc.k
            spacing: 12 * wc.k
            Grid {
                anchors.horizontalCenter: parent.horizontalCenter
                columns: 3
                spacing: 6 * wc.k
                Repeater {
                    model: 9
                    Rectangle { required property int index; width: 11 * wc.k; height: width; radius: width / 2
                                color: index === 4 ? "#E5533A" : (index % 2 === 0 ? "#E9B95C" : "#F3EEE4") }
                }
            }
            Text { anchors.horizontalCenter: parent.horizontalCenter; text: wc.label; color: wc.ink
                   font.pixelSize: 16 * wc.k; font.weight: Font.Medium; textFormat: Text.PlainText }
        }

        // -- the seal, once admitted --
        Rectangle {
            visible: wc.stamped
            width: 58 * wc.k; height: width; radius: 6 * wc.k
            x: (wc.panel ? wc.width * 0.685 : wc.width) * 0.5 - width / 2 + 60 * wc.k
            anchors.verticalCenter: parent.verticalCenter
            rotation: -8
            color: "transparent"
            border.color: "#F2C4B4"
            border.width: 2.5 * wc.k
            Rectangle { anchors.fill: parent; anchors.margins: 5 * wc.k; radius: 3 * wc.k; color: Theme.seal; opacity: 0.92 }
            Text { anchors.centerIn: parent; text: "纳"; color: Theme.sealText; font.family: Theme.serif
                   font.pixelSize: 28 * wc.k; font.weight: Font.Bold }
        }

        // the keyboard's ring
        Rectangle {
            anchors.fill: parent
            anchors.margins: -4
            radius: Theme.cardRound * wc.k + 4
            color: "transparent"
            border.color: Theme.focus
            border.width: 2
            visible: wc.visualFocus
        }
    }
}
