// The misty backdrop: soft clouds of colour that thin toward the middle, with
// grain. Painted once for its size and world (nothing redraws per frame); it
// drifts slowly as a whole, and holds still for someone who asked for less
// motion. The clouds are nothing but soft gradients, so they are painted at a
// third of the size and scaled up (a ninth of the work, the same to the eye);
// the grain lies over them at full size, as a tiled image.
import QtQuick
import ".."

Item {
    id: backdrop
    clip: true
    property int seed: 7
    property bool drift: !Theme.still

    Canvas {
        id: clouds
        readonly property real k: 3            // painted at a third of the size, scaled up
        // a little larger than the window, so the drift never shows an edge
        width: Math.ceil(parent.width * 1.08 / k)
        height: Math.ceil(parent.height * 1.08 / k)
        scale: k
        transformOrigin: Item.TopLeft
        smooth: true
        x: -parent.width * 0.04 + shift.dx
        y: -parent.height * 0.04 + shift.dy
        readonly property string sig: [width.toFixed(0), height.toFixed(0), Soft.mistGround, Soft.mist.join(","),
                                       backdrop.seed].join("|")
        // a canvas on a hidden page only marks itself stale, and paints when it is shown: a change of world
        // repaints what is on screen, not every page
        property bool stale: false
        onSigChanged: if (visible) requestPaint(); else stale = true
        onVisibleChanged: if (visible && stale) { stale = false; requestPaint() }

        function rng(s) {
            let x = (s >>> 0) || 1
            return function () { x = (x * 1664525 + 1013904223) >>> 0; return x / 4294967296 }
        }
        function rgba(c, a) { return Qt.rgba(c.r, c.g, c.b, a) }
        function puff(ctx, x, y, r, c, a) {          // one soft lump of cloud
            const g = ctx.createRadialGradient(x, y, 0, x, y, r)
            g.addColorStop(0, rgba(c, a))
            g.addColorStop(0.55, rgba(c, a * 0.45))
            g.addColorStop(1, rgba(c, 0))
            ctx.fillStyle = g
            ctx.fillRect(x - r, y - r, 2 * r, 2 * r)
        }

        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            const W = width, H = height, R = Math.max(W, H)
            if (W <= 0 || H <= 0) return
            // Oil-painting alpine sky -> cold snow glow -> sunlit garden moss wash
            const sky = ctx.createLinearGradient(0, 0, W * 0.2, H)
            if (Theme.dark) {
                sky.addColorStop(0.0, "#0F2235")
                sky.addColorStop(0.45, "#152B3D")
                sky.addColorStop(0.75, "#152C2B")
                sky.addColorStop(1.0, "#10241E")
            } else {
                sky.addColorStop(0.0, "#CBE6F8")
                sky.addColorStop(0.44, "#EEF6FC")
                sky.addColorStop(0.74, "#E2F1E7")
                sky.addColorStop(1.0, "#C7E2CD")
            }
            ctx.fillStyle = sky
            ctx.fillRect(0, 0, W, H)

            const rand = rng(backdrop.seed * 2654435761)
            const colors = Soft.mist.map(c => Qt.color(c))
            // Soft oil-painted cloud & foliage puffs framing the alpine horizon
            const anchors = [[0.12, 0.08], [0.38, 0.04], [0.78, 0.10], [0.95, 0.34], [0.04, 0.42],
                             [0.10, 0.88], [0.42, 0.96], [0.76, 0.92], [0.96, 0.78], [0.58, 0.16]]
            for (let i = 0; i < anchors.length; i++) {
                const c = colors[i % colors.length]
                const cx = anchors[i][0] * W + (rand() - 0.5) * W * 0.10
                const cy = anchors[i][1] * H + (rand() - 0.5) * H * 0.10
                const lumps = 4 + Math.floor(rand() * 3)
                for (let j = 0; j < lumps; j++) {
                    const r = R * (0.12 + rand() * 0.16)
                    puff(ctx, cx + (rand() - 0.5) * R * 0.20, cy + (rand() - 0.5) * R * 0.16, r, c,
                         Theme.dark ? 0.44 : 0.48)
                }
            }
            // Distant snow-capped mountain ridge silhouette (upper-right alpine horizon)
            ctx.beginPath()
            ctx.moveTo(0, H * 0.66)
            ctx.lineTo(W * 0.24, H * 0.48)
            ctx.lineTo(W * 0.46, H * 0.56)
            ctx.lineTo(W * 0.72, H * 0.34)
            ctx.lineTo(W, H * 0.52)
            ctx.lineTo(W, H)
            ctx.lineTo(0, H)
            ctx.closePath()
            const peak = ctx.createLinearGradient(0, H * 0.34, 0, H * 0.78)
            if (Theme.dark) {
                peak.addColorStop(0.0, "rgba(186,219,242,0.16)")
                peak.addColorStop(0.5, "rgba(44,82,108,0.18)")
                peak.addColorStop(1.0, "rgba(28,68,50,0.22)")
            } else {
                peak.addColorStop(0.0, "rgba(255,255,255,0.68)")
                peak.addColorStop(0.45, "rgba(182,216,238,0.34)")
                peak.addColorStop(1.0, "rgba(96,158,116,0.24)")
            }
            ctx.fillStyle = peak
            ctx.fill()

            // A calm frosty veil over the middle so foreground cards remain effortless to read
            const v = ctx.createRadialGradient(W * 0.5, H * 0.50, 0, W * 0.5, H * 0.50, R * 0.52)
            v.addColorStop(0, rgba(Qt.color(Soft.mistGround), Theme.dark ? 0.42 : 0.46))
            v.addColorStop(1, rgba(Qt.color(Soft.mistGround), 0))
            ctx.fillStyle = v
            ctx.fillRect(0, 0, W, H)
        }
    }
    Image {                                    // the grain, at full size
        anchors.fill: parent
        source: Qt.resolvedUrl("../grain.png")
        fillMode: Image.Tile
        smooth: false
    }

    QtObject {
        id: shift
        property real t: 0
        readonly property real dx: Math.sin(t) * backdrop.width * 0.018
        readonly property real dy: Math.cos(t * 0.7) * backdrop.height * 0.014
    }
    NumberAnimation {
        target: shift; property: "t"; from: 0; to: Math.PI * 2; duration: 60000
        loops: Animation.Infinite; running: backdrop.drift && backdrop.visible
    }
}
