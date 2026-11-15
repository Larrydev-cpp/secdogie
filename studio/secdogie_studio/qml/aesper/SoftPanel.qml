// A sheet with nothing painted on it: the page's working surface. White by
// day and ink at night, generous corners, and a soft shadow that falls a
// little below. The shadow is a falloff of rounded sheets, not a blur, so the
// software renderer draws it cheaply. It repaints only when the size or the
// world changes.
import QtQuick
import ".."

Item {
    id: panel
    property real radius: 28
    property real lift: 1.0             // how far the shadow reaches (0 .. 1)
    property real pad: Soft.s5
    default property alias content: inner.data
    readonly property alias body: inner

    Canvas {
        anchors.fill: parent
        anchors.margins: -36
        visible: panel.lift > 0
        readonly property string sig: [width.toFixed(0), height.toFixed(0), Theme.dark, panel.lift].join("|")
        onSigChanged: requestPaint()
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            const m = 36, W = width - 2 * m, H = height - 2 * m, r = panel.radius
            const c = Theme.dark ? "0,0,0" : "120,86,104"
            for (let i = 10; i >= 1; i--) {
                const g = 26 * panel.lift * Math.pow(i / 10, 1.5)
                ctx.fillStyle = `rgba(${c},${Theme.dark ? 0.04 : 0.024})`
                const x = m - g * 0.5, y = m + 10 * panel.lift - g * 0.3, w = W + g, h = H + g * 0.8, rr = r + g * 0.5
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
        radius: panel.radius
        color: Soft.sheet
        border.color: Theme.dark ? Qt.rgba(1, 1, 1, 0.06) : Qt.rgba(0, 0, 0, 0.04)
    }
    Item {
        id: inner
        x: panel.pad
        y: panel.pad
        width: panel.width - 2 * panel.pad
        height: panel.height - 2 * panel.pad
    }
}
