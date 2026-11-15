// The painted half of a sheet, behind a torn paper edge: a night garden.
// - light falls through a dark blue room;
// - flecks drift in the light;
// - blossoms stand in a celadon (青瓷) vase;
// - one has fallen onto a floor that holds their reflection.
// It is painted, not photographed: layered washes, petals laid twice for a
// brushed edge, short strokes for texture, and grain. Drawn once for its
// size; the same in 墨 and 宣纸, as a picture on a wall would be.
import QtQuick
import ".."

Canvas {
    id: art
    property int seed: 11
    property string tear: "left"        // the torn side: "left" or "right"; the others are the sheet's
    property real corner: 36            // the sheet's corners, on the untorn side
    property color paper: Soft.sheet    // the paper the tear shows

    readonly property string sig: [width.toFixed(0), height.toFixed(0), seed, tear, corner, paper].join("|")
    onSigChanged: requestPaint()
    readonly property url grain: Qt.resolvedUrl("../grain.png")
    Component.onCompleted: loadImage(grain)
    onImageLoaded: requestPaint()

    function rng(s) {
        let x = (s >>> 0) || 1
        return function () { x = (x * 1664525 + 1013904223) >>> 0; return x / 4294967296 }
    }
    function rgba(c, a) { const q = Qt.color(c); return Qt.rgba(q.r, q.g, q.b, a) }
    function blob(ctx, x, y, r, c, a) {
        const g = ctx.createRadialGradient(x, y, 0, x, y, r)
        g.addColorStop(0, rgba(c, a))
        g.addColorStop(1, rgba(c, 0))
        ctx.fillStyle = g
        ctx.fillRect(x - r, y - r, 2 * r, 2 * r)
    }
    // the torn edge: where it runs at height y
    function edgeAt(y, rand) {
        return 7 + 2.6 * Math.sin(y * 0.29) + 1.8 * Math.sin(y * 0.91 + 1.3) + 1.2 * Math.sin(y * 2.3) + rand() * 2.4
    }
    function outline(ctx, W, H, edge) {
        const r = Math.min(corner, W / 3, H / 3)
        const left = tear === "left"
        ctx.beginPath()
        if (left) {
            ctx.moveTo(edge[0], 0)
            ctx.lineTo(W - r, 0)
            ctx.arcTo(W, 0, W, r, r)
            ctx.lineTo(W, H - r)
            ctx.arcTo(W, H, W - r, H, r)
            ctx.lineTo(edge[edge.length - 1], H)
            for (let i = edge.length - 1; i >= 0; i--) ctx.lineTo(edge[i], i * 3)
        } else {
            ctx.moveTo(W - edge[0], 0)
            ctx.lineTo(r, 0)
            ctx.arcTo(0, 0, 0, r, r)
            ctx.lineTo(0, H - r)
            ctx.arcTo(0, H, r, H, r)
            ctx.lineTo(W - edge[edge.length - 1], H)
            for (let i = edge.length - 1; i >= 0; i--) ctx.lineTo(W - edge[i], i * 3)
        }
        ctx.closePath()
    }
    // a petal as a painter lays it: an uneven outline, a wash from a warm heart, and patches of other colours
    function petalPath(ctx, rx, ry, k) {
        ctx.beginPath()
        for (let i = 0; i <= 28; i++) {
            const t = i / 28 * Math.PI * 2
            const wob = 1 + 0.07 * Math.sin(t * 3 + k) + 0.05 * Math.sin(t * 7 + k * 2.3) + 0.04 * Math.sin(t * 11 + k)
            const x = Math.cos(t) * rx * wob, y = Math.sin(t) * ry * wob
            if (i === 0) ctx.moveTo(x, y)
            else ctx.lineTo(x, y)
        }
        ctx.closePath()
    }
    function petal(ctx, cx, cy, ang, rx, ry, off, inner, outer, a, rand, patches) {
        const k = rand() * 10
        ctx.save()
        ctx.translate(cx + Math.cos(ang) * off, cy + Math.sin(ang) * off)
        ctx.rotate(ang)
        petalPath(ctx, rx, ry, k)
        const g = ctx.createRadialGradient(-rx * 0.7, 0, 0, -rx * 0.2, 0, rx * 1.1)
        g.addColorStop(0, rgba(inner, a))
        g.addColorStop(0.45, rgba(outer, a))
        g.addColorStop(1, rgba(outer, a * 0.85))
        ctx.fillStyle = g
        ctx.fill()
        // patches of other colours, kept inside the petal by where they sit and how far they reach
        // (not by clip(): the canvas does not undo a clip on restore(), and every later petal would be cut by it)
        for (let i = 0; i < patches.length; i++)
            blob(ctx, (rand() * 0.8 - 0.45) * rx, (rand() - 0.5) * ry * 0.7, rx * (0.25 + rand() * 0.17), patches[i], 0.5 * a)
        ctx.strokeStyle = rgba("#FFFFFF", 0.25 * a)    // the light catching its rim
        ctx.lineWidth = 1
        ctx.stroke()
        ctx.restore()
    }
    function blossom(ctx, cx, cy, r, rand, tilt) {
        const colors = ["#F26AA6", "#FDEBD8", "#A9D8F2", "#F7C4DE", "#FFF6EC", "#F59AC4", "#C7B6F2"]
        const pick = () => colors[Math.floor(rand() * colors.length)]
        for (let k = 0; k < 2; k++)                  // two dark petals behind
            petal(ctx, cx, cy, tilt + Math.PI * (0.85 + k * 0.42), r * 0.62, r * 0.47, r * 0.48, "#D0336A", "#7A1032", 0.95,
                  rand, ["#B01E4E"])
        for (let i = 0; i < 5; i++) {
            const ang = tilt + i * Math.PI * 2 / 5 + (rand() - 0.5) * 0.4
            const c = pick()
            petal(ctx, cx, cy, ang, r * 0.6, r * 0.45, r * 0.4, "#FFE27D", c, 0.96, rand, [pick(), pick()])
            ctx.strokeStyle = "rgba(176,18,63,0.55)"
            ctx.lineWidth = 1
            for (let v = -1; v <= 1; v++) {          // veins from the heart
                ctx.beginPath()
                ctx.moveTo(cx + Math.cos(ang) * r * 0.08, cy + Math.sin(ang) * r * 0.08)
                ctx.quadraticCurveTo(cx + Math.cos(ang + v * 0.16) * r * 0.32, cy + Math.sin(ang + v * 0.16) * r * 0.32,
                                     cx + Math.cos(ang + v * 0.25) * r * 0.5, cy + Math.sin(ang + v * 0.25) * r * 0.5)
                ctx.stroke()
            }
        }
        blob(ctx, cx, cy, r * 0.2, "#FFB43C", 0.95)
        blob(ctx, cx, cy, r * 0.08, "#7A1032", 0.8)
    }
    function stroke(ctx, x, y, len, ang, w, c, a) {
        ctx.strokeStyle = rgba(c, a)
        ctx.lineWidth = w
        ctx.lineCap = "round"
        ctx.beginPath()
        ctx.moveTo(x, y)
        ctx.lineTo(x + Math.cos(ang) * len, y + Math.sin(ang) * len)
        ctx.stroke()
    }

    onPaint: {
        const ctx = getContext("2d")
        ctx.reset()
        const W = width, H = height
        if (W <= 0 || H <= 0) return
        const rand = rng(seed * 2654435761)
        const edge = []
        for (let y = 0; y <= H + 3; y += 3) edge.push(edgeAt(y, rand))

        // paper fibres along the tear, on the sheet's side: a thin ragged band of paper, and a few loose hairs
        ctx.fillStyle = rgba(Theme.dark ? "#EAE3D6" : "#FFFFFF", Theme.dark ? 0.85 : 1)
        ctx.beginPath()
        const side = tear === "left" ? -1 : 1
        const at = x => tear === "left" ? x : W - x
        ctx.moveTo(at(edge[0]), 0)
        for (let i = 0; i < edge.length; i++) ctx.lineTo(at(edge[i]), i * 3)
        for (let i = edge.length - 1; i >= 0; i--)
            ctx.lineTo(at(edge[i]) + side * (1.5 + 2.5 * Math.abs(Math.sin(i * 0.7)) + rand() * 1.2), i * 3)
        ctx.closePath()
        ctx.fill()
        ctx.strokeStyle = rgba(Theme.dark ? "#EAE3D6" : "#FFFFFF", 0.7)
        ctx.lineWidth = 0.8
        for (let i = 0; i < 26; i++) {
            const j = Math.floor(rand() * edge.length), x = at(edge[j]), y = j * 3
            ctx.beginPath()
            ctx.moveTo(x, y)
            ctx.lineTo(x - side * (2 + rand() * 4), y + (rand() - 0.5) * 6)
            ctx.stroke()
        }

        ctx.save()
        outline(ctx, W, H, edge)
        ctx.clip()

        // the room: dark blue, violet low down, teal in the corner
        const bg = ctx.createLinearGradient(0, 0, 0, H)
        bg.addColorStop(0, "#2D4886")
        bg.addColorStop(0.45, "#4A3B7A")
        bg.addColorStop(0.75, "#2C2F60")
        bg.addColorStop(1, "#1D2447")
        ctx.fillStyle = bg
        ctx.fillRect(0, 0, W, H)
        blob(ctx, W * 0.08, H * 0.82, W * 0.55, "#2E8C86", 0.55)

        // light through a high window: a warm glow, a rose haze, a shaft, a faint rainbow
        blob(ctx, W * 0.18, H * 0.02, W * 0.55, "#FFD9BC", 0.75)
        blob(ctx, W * 0.42, H * 0.28, W * 0.5, "#F5A8CB", 0.5)
        ctx.save()
        ctx.translate(W * 0.3, -H * 0.05)
        ctx.rotate(0.38)
        const shaft = ctx.createLinearGradient(-W * 0.12, 0, W * 0.12, 0)
        shaft.addColorStop(0, "rgba(255,240,232,0)")
        shaft.addColorStop(0.5, "rgba(255,240,232,0.42)")
        shaft.addColorStop(1, "rgba(255,240,232,0)")
        ctx.fillStyle = shaft
        ctx.fillRect(-W * 0.12, 0, W * 0.24, H * 1.3)
        const bow = ctx.createLinearGradient(-W * 0.32, 0, -W * 0.2, 0)
        for (const [s, c] of [[0, "rgba(255,90,90,0)"], [0.2, "rgba(255,120,90,0.2)"], [0.4, "rgba(255,220,90,0.2)"],
                              [0.6, "rgba(90,220,140,0.2)"], [0.8, "rgba(90,150,255,0.2)"], [1, "rgba(160,90,255,0)"]])
            bow.addColorStop(s, c)
        ctx.fillStyle = bow
        ctx.fillRect(-W * 0.32, H * 0.15, W * 0.12, H * 0.8)
        ctx.restore()

        // washes of paint
        const wash = ["#8FB7E8", "#F3B6D3", "#5E4CA0", "#2F6F86", "#F7D2C0", "#3B3473"]
        for (let i = 0; i < 110; i++)
            stroke(ctx, rand() * W, rand() * H, 14 + rand() * 30, Math.PI * (0.15 + rand() * 0.7), 10 + rand() * 16,
                   wash[Math.floor(rand() * wash.length)], 0.02 + rand() * 0.035)

        // flecks in the light
        for (let i = 0; i < 150; i++) {
            const x = W * (0.15 + rand() * 0.7), y = H * (0.05 + rand() * 0.6)
            ctx.fillStyle = rgba(rand() < 0.7 ? "#FFFFFF" : "#FFD1E6", 0.3 + rand() * 0.5)
            ctx.fillRect(x, y, 1 + rand() * 1.8, 1 + rand() * 2.4)
        }

        // the floor, and what it reflects
        const floor = ctx.createLinearGradient(0, H * 0.8, 0, H)
        floor.addColorStop(0, "rgba(231,185,230,0.0)")
        floor.addColorStop(0.15, "rgba(231,185,230,0.55)")
        floor.addColorStop(1, "rgba(120,96,196,0.45)")
        ctx.fillStyle = floor
        ctx.fillRect(0, H * 0.8, W, H * 0.2)
        const refl = ["#F26AA6", "#A9D8F2", "#9ED3C2", "#FDEBD8", "#7E6BD0"]
        for (let i = 0; i < 46; i++)
            stroke(ctx, W * (0.45 + rand() * 0.5), H * (0.84 + rand() * 0.06), 10 + rand() * 22, Math.PI / 2 + (rand() - 0.5) * 0.15,
                   3 + rand() * 4, refl[Math.floor(rand() * refl.length)], 0.1 + rand() * 0.14)

        // the vase: celadon, with a highlight
        const vx = W * 0.7, vy = H * 0.74, vw = W * 0.16, vh = H * 0.15
        ctx.save()
        const vg = ctx.createRadialGradient(vx - vw * 0.25, vy - vh * 0.2, 0, vx, vy, vw)
        vg.addColorStop(0, "#D8F0E6")
        vg.addColorStop(0.5, "#8FCDB9")
        vg.addColorStop(1, "#3D8576")
        ctx.fillStyle = vg
        ctx.beginPath()
        ctx.moveTo(vx - vw * 0.22, vy - vh * 0.55)
        ctx.quadraticCurveTo(vx - vw * 0.62, vy - vh * 0.1, vx - vw * 0.5, vy + vh * 0.45)
        ctx.quadraticCurveTo(vx, vy + vh * 0.62, vx + vw * 0.5, vy + vh * 0.45)
        ctx.quadraticCurveTo(vx + vw * 0.62, vy - vh * 0.1, vx + vw * 0.22, vy - vh * 0.55)
        ctx.closePath()
        ctx.fill()
        ctx.restore()
        for (let i = 0; i < 26; i++)
            stroke(ctx, vx + (rand() - 0.5) * vw * 0.8, vy + (rand() - 0.3) * vh * 0.8, 4 + rand() * 8, Math.PI * (0.45 + rand() * 0.1),
                   2, rand() < 0.5 ? "#E9FFF6" : "#2F6F63", 0.25)

        // stems and leaves
        const greens = ["#5DAF6B", "#2F7D53", "#86C97A"]
        const heads = [[W * 0.66, H * 0.27, W * 0.13, -0.3], [W * 0.82, H * 0.34, W * 0.12, 0.4],
                       [W * 0.57, H * 0.42, W * 0.11, 1.1], [W * 0.74, H * 0.46, W * 0.115, 2.0], [W * 0.88, H * 0.18, W * 0.085, 0.8]]
        for (const [hx, hy] of heads) {
            ctx.strokeStyle = greens[Math.floor(rand() * 3)]
            ctx.lineWidth = 3
            ctx.beginPath()
            ctx.moveTo(vx + (rand() - 0.5) * vw * 0.2, vy - vh * 0.5)
            ctx.quadraticCurveTo((vx + hx) / 2 + (rand() - 0.5) * 30, (vy + hy) / 2, hx, hy)
            ctx.stroke()
        }
        for (let i = 0; i < 5; i++) {
            const lx = vx + (rand() - 0.5) * vw, ly = vy - vh * (0.5 + rand() * 0.6)
            stroke(ctx, lx, ly, W * 0.05, -Math.PI / 2 + (rand() - 0.5) * 1.6, 6, greens[i % 3], 0.85)
        }

        // the blossoms, and one fallen to the floor
        for (const [hx, hy, r, tilt] of heads) blossom(ctx, hx, hy, r, rand, tilt)
        for (let i = 0; i < 3; i++)
            stroke(ctx, W * 0.12 + i * 8, H * 0.9, W * 0.22, -0.18 + i * 0.05, 4, greens[i], 0.8)
        blossom(ctx, W * 0.3, H * 0.86, W * 0.055, rand, 0.9)

        if (isImageLoaded(grain)) {
            ctx.fillStyle = ctx.createPattern(grain, "repeat")
            ctx.fillRect(0, 0, W, H)
            ctx.fillRect(0, 0, W, H)
        }
        ctx.restore()
    }
}
