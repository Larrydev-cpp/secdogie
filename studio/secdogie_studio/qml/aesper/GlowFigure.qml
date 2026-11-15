// The companion as a figure of light: a soft, many-coloured glow in the shape
// of someone holding a dark card (the node's card from the wallet). The glow is
// painted as layered auras, not a blur shader, so it draws the same in the
// software renderer; it is painted once, and only breathes (a slow scale and a
// warmer core) while it is on screen.
import QtQuick
import ".."

Item {
    id: figure
    implicitWidth: 260
    implicitHeight: 320
    property bool breathing: !Theme.still

    Canvas {
        id: glow
        anchors.fill: parent
        transformOrigin: Item.Bottom
        readonly property string sig: [width.toFixed(0), height.toFixed(0), Theme.dark].join("|")
        onSigChanged: requestPaint()
        readonly property url grain: Qt.resolvedUrl("../grain.png")
        Component.onCompleted: loadImage(grain)
        onImageLoaded: requestPaint()

        // the silhouette, centred on the origin, h tall: a head, a neck, sloping shoulders, a body that narrows a little
        function shape(ctx, h) {
            ctx.beginPath()
            ctx.moveTo(0.1 * h, -0.38 * h)
            ctx.arc(0, -0.38 * h, 0.1 * h, 0, Math.PI * 2)
            ctx.moveTo(-0.055 * h, -0.29 * h)
            ctx.quadraticCurveTo(-0.06 * h, -0.25 * h, -0.13 * h, -0.235 * h)
            ctx.quadraticCurveTo(-0.235 * h, -0.215 * h, -0.235 * h, -0.1 * h)
            ctx.quadraticCurveTo(-0.215 * h, 0.2 * h, -0.205 * h, 0.44 * h)
            ctx.quadraticCurveTo(-0.2 * h, 0.5 * h, -0.13 * h, 0.5 * h)
            ctx.lineTo(0.13 * h, 0.5 * h)
            ctx.quadraticCurveTo(0.2 * h, 0.5 * h, 0.205 * h, 0.44 * h)
            ctx.quadraticCurveTo(0.215 * h, 0.2 * h, 0.235 * h, -0.1 * h)
            ctx.quadraticCurveTo(0.235 * h, -0.215 * h, 0.13 * h, -0.235 * h)
            ctx.quadraticCurveTo(0.06 * h, -0.25 * h, 0.055 * h, -0.29 * h)
            ctx.closePath()
        }
        function blob(ctx, x, y, r, c, a) {          // one soft light: full at its centre, gone at its rim
            const q = Qt.color(c)
            const g = ctx.createRadialGradient(x, y, 0, x, y, r)
            g.addColorStop(0, Qt.rgba(q.r, q.g, q.b, a))
            g.addColorStop(0.5, Qt.rgba(q.r, q.g, q.b, a * 0.55))
            g.addColorStop(1, Qt.rgba(q.r, q.g, q.b, 0))
            ctx.fillStyle = g
            ctx.fillRect(x - r, y - r, 2 * r, 2 * r)
        }
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            const W = width, H = height
            if (W <= 0 || H <= 0) return
            const h = Math.min(H * 0.86, W * 1.45)
            ctx.save()
            ctx.translate(W / 2, H * 0.5)
            // a faint cool halo around the whole figure
            blob(ctx, 0, 0.02 * h, 0.6 * h, Theme.dark ? "#5F6CC8" : "#C9DCFF", Theme.dark ? 0.3 : 0.32)
            // the body: soft lights laid over one another along a standing figure, cool at the left,
            // a warm orange core, rose at the right; where they overlap, the colour runs together
            const parts = [
                [0, -0.38, 0.11, "#FFD2B4", 0.85],   [0, -0.27, 0.075, "#FFC0A0", 0.6],
                [-0.16, -0.19, 0.13, "#A3DAF7", 0.62], [0.16, -0.19, 0.13, "#F6B5D5", 0.62],
                [-0.05, -0.07, 0.2, "#FF9C58", 0.72], [0.09, -0.05, 0.17, "#F7A8CB", 0.58],
                [-0.15, 0.1, 0.15, "#A8DCF6", 0.6],  [0, 0.1, 0.22, "#FF8838", 0.8],
                [0.15, 0.13, 0.15, "#F5B0D2", 0.6],  [0, 0.3, 0.2, "#FFB079", 0.62],
                [-0.13, 0.35, 0.14, "#C6B6F2", 0.55], [0.13, 0.36, 0.14, "#F8C3DA", 0.55],
                [0, 0.47, 0.17, "#FFD9C6", 0.3]
            ]
            // first the figure itself, so it reads as someone: a soft silhouette, feathered at its edge
            const body = ctx.createLinearGradient(-0.25 * h, 0, 0.25 * h, 0)
            body.addColorStop(0, "#A3DAF7")
            body.addColorStop(0.34, "#F4B8D2")
            body.addColorStop(0.58, "#FF9A55")
            body.addColorStop(1, "#F6C2DD")
            for (const [s, a] of [[1.2, 0.06], [1.13, 0.09], [1.08, 0.14], [1.04, 0.22], [1.0, 0.64]]) {
                ctx.save()
                ctx.scale(s, s)
                shape(ctx, h)
                ctx.fillStyle = body
                ctx.globalAlpha = a
                ctx.fill()
                ctx.restore()
            }
            ctx.globalAlpha = 1
            // then the lights inside it, smaller, so the colour runs together without spilling into a cloud
            for (const p of parts) blob(ctx, p[0] * h, p[1] * h, p[2] * h * 0.72, p[3], p[4] * 0.7)
            blob(ctx, -0.04 * h, -0.43 * h, 0.07 * h, "#FFFFFF", 0.55)   // light on the head

            // the card it holds at the chest, tilted, its edge a little fuzzy; hands at its corners
            ctx.save()
            ctx.translate(0.06 * h, 0.02 * h)
            ctx.rotate(-0.26)
            const cw = 0.3 * h, ch = 0.19 * h
            blob(ctx, -cw * 0.46, ch * 0.4, 0.075 * h, "#FFAE84", 0.9)
            blob(ctx, cw * 0.5, ch * 0.28, 0.07 * h, "#FFAE84", 0.85)
            for (const [dx, dy, a] of [[-1.2, 0.8, 0.35], [1.1, -0.9, 0.35], [0, 0, 1]]) {
                ctx.fillStyle = Qt.rgba(0.075, 0.07, 0.09, a)
                ctx.beginPath()
                ctx.moveTo(-cw / 2 + 3 + dx, -ch / 2 + dy)
                ctx.lineTo(cw / 2 + dx, -ch / 2 - 2 + dy)
                ctx.lineTo(cw / 2 + 2 + dx, ch / 2 + dy)
                ctx.lineTo(-cw / 2 + dx, ch / 2 + 2 + dy)
                ctx.closePath()
                ctx.fill()
            }
            ctx.fillStyle = "#F0603F"                       // the node card's cinnabar
            ctx.beginPath()
            ctx.arc(cw * 0.3, -ch * 0.22, 0.011 * h, 0, Math.PI * 2)
            ctx.fill()
            ctx.restore()
            ctx.restore()

            if (isImageLoaded(grain)) {                   // grain on the light only
                ctx.globalCompositeOperation = "source-atop"
                ctx.fillStyle = ctx.createPattern(grain, "repeat")
                for (let i = 0; i < 3; i++) ctx.fillRect(0, 0, W, H)
                ctx.globalCompositeOperation = "source-over"
            }
        }

        SequentialAnimation on scale {
            running: figure.breathing && figure.visible
            loops: Animation.Infinite
            NumberAnimation { from: 1.0; to: 1.025; duration: 3200; easing.type: Easing.InOutSine }
            NumberAnimation { from: 1.025; to: 1.0; duration: 3600; easing.type: Easing.InOutSine }
        }
    }
}
