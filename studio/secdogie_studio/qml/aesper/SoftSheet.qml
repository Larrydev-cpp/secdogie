// The big sheet: white (or ink at night), generous corners, a soft shadow that
// falls a little below it, and a painted half behind a torn edge. What it holds
// goes in the words' half.
import QtQuick
import ".."

Item {
    id: sheet
    property real radius: 36
    property real split: 0.5            // the words' share of the width
    property bool painted: true
    property int seed: 11
    default property alias content: words.data
    readonly property alias wordsArea: words

    Canvas {                            // the shadow: a falloff of rounded sheets, offset below
        anchors.fill: parent
        anchors.margins: -48
        readonly property string sig: [width.toFixed(0), height.toFixed(0)].join("|")
        // a canvas on a hidden page only marks itself stale, and paints when it is shown: a change of world
        // repaints what is on screen, not every page
        property bool stale: false
        onSigChanged: if (visible) requestPaint(); else stale = true
        onVisibleChanged: if (visible && stale) { stale = false; requestPaint() }
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            const m = 48, W = width - 2 * m, H = height - 2 * m, r = sheet.radius
            const c = "60,40,52"  // one warm shadow for both worlds: nothing to repaint when the world changes
            for (let i = 14; i >= 1; i--) {
                const g = 34 * Math.pow(i / 14, 1.5)
                ctx.fillStyle = `rgba(${c},0.03)`
                const x = m - g * 0.5, y = m + 16 - g * 0.3, w = W + g, h = H + g * 0.8, rr = r + g * 0.5
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
        anchors.fill: parent
        radius: sheet.radius
        color: Soft.sheet
        border.color: Theme.dark ? Qt.rgba(1, 1, 1, 0.06) : "transparent"
    }
    Item {
        id: words
        width: sheet.painted ? sheet.width * sheet.split : sheet.width
        height: sheet.height
    }
    PaintedPanel {
        visible: sheet.painted
        x: sheet.width * sheet.split - 8
        width: sheet.width - x
        height: sheet.height
        corner: sheet.radius
        seed: sheet.seed
    }
}
