// A sheet of the home window, set the way Google sets a page: a flat tonal
// surface with generous corners, its title, an optional chip at the right,
// and whatever it holds below. (The wallet's cards are WalletCard; this is
// the paper they sit beside.) `kind`: "paper" (the usual), "seal" (cinnabar:
// it asks for you) or "inverse" (ink on paper, paper on ink).
import QtQuick
import "."
import "aesper"

Item {
    id: card
    property string title
    property string chip
    property bool chipLive: false            // the chip as a live mark (a cinnabar dot)
    property string kind: "paper"
    default property alias content: body.data
    readonly property color fg: kind === "seal" ? Theme.sealText : (kind === "inverse" ? Theme.inverseText : Theme.text)
    readonly property color fgSoft: kind === "seal" ? Theme.sealSoft : (kind === "inverse" ? Theme.inverseSoft : Theme.soft)
    readonly property color bg: kind === "seal" ? Theme.seal : (kind === "inverse" ? Theme.inverse : Soft.sheet)
    property real pad: 28

    Canvas {                                   // a soft shadow below the sheet (layered, never a blur)
        anchors.fill: parent
        anchors.margins: -32
        readonly property string sig: [width.toFixed(0), height.toFixed(0)].join("|")
        // a canvas on a hidden page only marks itself stale, and paints when it is shown: a change of world
        // repaints what is on screen, not every page
        property bool stale: false
        onSigChanged: if (visible) requestPaint(); else stale = true
        onVisibleChanged: if (visible && stale) { stale = false; requestPaint() }
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            const m = 32, W = width - 2 * m, H = height - 2 * m, r = sheet.radius
            const c = "60,40,52"  // one warm shadow for both worlds: nothing to repaint when the world changes
            for (let i = 9; i >= 1; i--) {
                const g = 22 * Math.pow(i / 9, 1.5)
                ctx.fillStyle = `rgba(${c},0.03)`
                const x = m - g * 0.5, y = m + 8 - g * 0.3, w = W + g, h = H + g * 0.8, rr = r + g * 0.5
                ctx.beginPath()
                ctx.moveTo(x + rr, y)
                ctx.arcTo(x + w, y, x + w, y + h, rr)
                ctx.arcTo(x + w, y + h, x, y + h, rr)
                ctx.arcTo(x, y + h, x, y, rr)
                ctx.arcTo(x, y, x + w, y, rr)
                ctx.closePath()
                ctx.fill()
            }
        }
    }
    Rectangle {
        id: sheet
        anchors.fill: parent
        radius: 28
        color: card.bg
        border.color: card.kind === "paper" && Theme.dark ? Qt.rgba(1, 1, 1, 0.06) : "transparent"
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
