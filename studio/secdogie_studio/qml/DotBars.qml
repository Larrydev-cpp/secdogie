// A chart in dots: one column per value, as many lit dots as the value's
// share of the tallest, the rest faint. The last `marked` columns are lit in
// the accent (now, or the latest).
import QtQuick
import "."

Canvas {
    id: chart
    property var values: []
    property int rows: 8
    property real pitch: 7
    property real dot: pitch * 0.7
    property color on: Theme.dotOn
    property color off: Theme.dotOff
    property color accent: Theme.seal
    property int marked: 1

    implicitWidth: Math.max(1, values.length) * pitch
    implicitHeight: rows * pitch
    Accessible.role: Accessible.Graphic

    onValuesChanged: requestPaint()
    onOnChanged: requestPaint()
    onOffChanged: requestPaint()
    onWidthChanged: requestPaint()

    onPaint: {
        const ctx = getContext("2d")
        ctx.reset()
        const n = values.length
        if (n === 0) return
        const top = Math.max(1, Math.max.apply(null, values))
        const r = dot / 2
        for (let i = 0; i < n; i++) {
            const lit = values[i] <= 0 ? 0 : Math.max(1, Math.round(values[i] / top * rows))
            for (let row = 0; row < rows; row++) {
                const fromBottom = rows - 1 - row
                const on_ = fromBottom < lit
                ctx.fillStyle = on_ ? (i >= n - marked ? accent : on) : off
                ctx.beginPath()
                ctx.arc(i * pitch + pitch / 2, row * pitch + pitch / 2, r, 0, Math.PI * 2)
                ctx.fill()
            }
        }
    }
}
