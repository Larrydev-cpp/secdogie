// An invitation as a QR code, for the phone's camera: always dark modules on
// paper, whatever the theme, because that is what cameras read.
import QtQuick
import "."

Rectangle {
    id: qr
    property var rows: []                      // strings of "0" / "1", quiet zone included
    color: "#FBF7EE"
    radius: 12
    implicitWidth: 196
    implicitHeight: 196
    Accessible.role: Accessible.Graphic
    Accessible.name: Theme.t("请帖的二维码：用另一台设备的相机扫一扫", "The invitation as a QR code: scan it with the other device's camera")

    Canvas {
        id: modules
        anchors.fill: parent
        anchors.margins: 8
        Connections { target: qr; function onRowsChanged() { modules.requestPaint() } }
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            const n = qr.rows.length
            if (n === 0) return
            const cell = Math.floor(Math.min(width, height) / n)
            const off = (Math.min(width, height) - cell * n) / 2
            ctx.fillStyle = "#14161C"
            for (let y = 0; y < n; y++)
                for (let x = 0; x < n; x++)
                    if (qr.rows[y][x] === "1") ctx.fillRect(off + x * cell, off + y * cell, cell, cell)
        }
    }
}
