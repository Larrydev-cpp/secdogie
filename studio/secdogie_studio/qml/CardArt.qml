// The face of a wallet card, painted: its shadow, its art, and the dark panel
// that carries the mark. Two arts:
//   * "glaze": a glaze (Theme.glazes) laid on like a bank card's: a deep
//     diagonal, a low horizon whose rim catches the light, a fold of shadow,
//     and fine grain. `seed` moves the horizon and the light, so no two
//     devices' cards are quite alike.
//   * "dots": the node's card. Black, with the day as a dome of dots: midnight
//     at the left foot, noon at the top, midnight again at the right foot; a
//     quarter-hour you were at this computer is lit in the colour of the sky at
//     that hour, and now is cinnabar. The twelve 时辰 stand around the rim.
// The canvas is larger than the card by `margin` on every side: the shadow
// falls there.
import QtQuick
import "."

Canvas {
    id: art
    property string kind: "glaze"
    property string glaze: "jilan"
    property int seed: 0
    property bool panel: false
    property real margin: 60
    property real radius: Theme.cardRound
    property real lift: 0                      // 0..1: hovered or in front, a deeper shadow
    property var quarters: []                  // 96 values 0..1 for "dots"
    property int now: -1
    // repaint only when what is drawn changes (the bridge sends a fresh list every tick)
    // (nothing here depends on 墨 or 宣纸: a card is the same card in both, so changing the world repaints nothing)
    readonly property string sig: [kind, glaze, seed, panel, width, height, lift.toFixed(2), now,
                                   kind === "dots" ? (quarters || []).join(",") : ""].join("|")
    property string painted: ""
    onSigChanged: if (sig !== painted) requestPaint()
    // the grain, as a tile: one fill, not a thousand specks
    readonly property url grain: Qt.resolvedUrl("grain.png")
    Component.onCompleted: loadImage(grain)
    onImageLoaded: requestPaint()

    function roundRect(ctx, x, y, w, h, r) {
        ctx.beginPath()
        ctx.moveTo(x + r, y)
        ctx.arcTo(x + w, y, x + w, y + h, r)
        ctx.arcTo(x + w, y + h, x, y + h, r)
        ctx.arcTo(x, y + h, x, y, r)
        ctx.arcTo(x, y, x + w, y, r)
        ctx.closePath()
    }
    function rgba(c, a) { return Qt.rgba(c.r, c.g, c.b, a) }
    function mix(a, b, t) { return Qt.rgba(a.r + (b.r - a.r) * t, a.g + (b.g - a.g) * t, a.b + (b.b - a.b) * t, 1) }
    function rng(s) {                          // a small seeded generator: the same card, the same grain
        let x = (s >>> 0) || 1
        return function () { x = (x * 1664525 + 1013904223) >>> 0; return x / 4294967296 }
    }
    // the sky over a day, for the dots: night blue, celadon dawn, gold noon, rouge dusk, lotus night
    readonly property var sky: [Qt.color("#86A6FF"), Qt.color("#8FE3C7"), Qt.color("#F2D173"),
                                Qt.color("#F49C7C"), Qt.color("#B9A1F2")]
    function skyAt(t) {
        const p = Math.max(0, Math.min(0.9999, t)) * (sky.length - 1)
        const i = Math.floor(p)
        return mix(sky[i], sky[i + 1], p - i)
    }

    onPaint: {
        const ctx = getContext("2d")
        ctx.reset()
        painted = sig
        const m = margin, W = width - 2 * m, H = height - 2 * m
        if (W <= 0 || H <= 0) return
        const r = radius
        const g = Theme.glazes[glaze] || Theme.glazes.jilan
        const glow = kind === "dots" ? Qt.color("#000000") : Qt.color(g.glow)

        // the shadow: offset below, soft, in the card's own colour; deeper for the card in front.
        // Laid as a falloff of rounded sheets, not the canvas's blur: that costs ten times as much.
        const depth = 0.4 + 0.6 * Math.min(1, lift * 1.4)
        const blur = 10 + 26 * lift, dy = 4 + 14 * lift
        const alpha = depth * (kind === "dots" ? 0.34 : 0.42)
        const layers = 14
        for (let i = layers; i >= 1; i--) {
            const grow = blur * Math.pow(i / layers, 1.5)     // denser near the edge, thinning outward
            ctx.fillStyle = rgba(glow, alpha / layers * 1.15)
            roundRect(ctx, m - grow * 0.45, m + dy - grow * 0.3, W + grow * 0.9, H + grow * 0.75, r + grow * 0.5)
            ctx.fill()
        }
        roundRect(ctx, m, m, W, H, r)
        ctx.fillStyle = kind === "dots" ? "#0C0D10" : g.deep
        ctx.fill()

        ctx.save()
        roundRect(ctx, m, m, W, H, r)
        ctx.clip()
        const rand = rng(seed * 2654435761)
        if (kind === "dots") paintDots(ctx, m, m, W, H)
        else paintGlaze(ctx, m, m, W, H, g, rand)

        // grain, so it reads as a printed card and not a gradient
        if (isImageLoaded(grain)) {
            ctx.fillStyle = ctx.createPattern(grain, "repeat")
            ctx.fillRect(m, m, W, H)
        }
        if (panel) {                           // the dark panel at the right, for the mark
            const px = m + W * 0.685
            const pg = ctx.createLinearGradient(px, m, px, m + H)
            pg.addColorStop(0, "#131418")
            pg.addColorStop(1, "#0D0E11")
            ctx.fillStyle = pg
            ctx.fillRect(px, m, W - (px - m), H)
            ctx.fillStyle = "rgba(255,255,255,0.06)"
            ctx.fillRect(px, m, 1, H)
        }
        ctx.restore()

        // a hair of light along the edge
        roundRect(ctx, m + 0.5, m + 0.5, W - 1, H - 1, r)
        ctx.lineWidth = 1
        ctx.strokeStyle = "rgba(255,255,255,0.10)"
        ctx.stroke()
    }

    function paintGlaze(ctx, x, y, W, H, g, rand) {
        const base = Qt.color(g.base), deep = Qt.color(g.deep), light = Qt.color(g.light)
        const lg = ctx.createLinearGradient(x, y, x + W, y + H)
        lg.addColorStop(0, mix(base, light, 0.08))
        lg.addColorStop(0.55, base)
        lg.addColorStop(1, deep)
        ctx.fillStyle = lg
        ctx.fillRect(x, y, W, H)

        // a bloom of light high on the left
        const bx = x + W * (0.12 + rand() * 0.2), by = y + H * (0.32 + rand() * 0.12)
        let rg = ctx.createRadialGradient(bx, by, 0, bx, by, W * 0.42)
        rg.addColorStop(0, rgba(light, 0.30))
        rg.addColorStop(1, rgba(light, 0))
        ctx.fillStyle = rg
        ctx.fillRect(x, y, W, H)

        // the fold: a shadow that curls down from the top right
        const fx = x + W * (0.58 + rand() * 0.1)
        rg = ctx.createRadialGradient(fx + W * 0.12, y - H * 0.05, 0, fx + W * 0.12, y - H * 0.05, W * 0.5)
        rg.addColorStop(0, rgba(deep, 0.85))
        rg.addColorStop(0.6, rgba(deep, 0.35))
        rg.addColorStop(1, rgba(deep, 0))
        ctx.fillStyle = rg
        ctx.fillRect(x, y, W, H)

        // the horizon: a great curve low on the card, its rim catching the light
        const cx = x + W * (0.36 + rand() * 0.14), cy = y + H * (1.55 + rand() * 0.12), R = W * 0.8
        const top = cy - R
        const dg = ctx.createLinearGradient(x, top, x, y + H)
        dg.addColorStop(0, mix(base, light, 0.22))
        dg.addColorStop(0.35, base)
        dg.addColorStop(1, mix(base, deep, 0.35))
        ctx.beginPath()
        ctx.arc(cx, cy, R, 0, Math.PI * 2)
        ctx.fillStyle = dg
        ctx.fill()

        ctx.save()                             // the sheen on the rim: a flattened glow
        ctx.translate(cx - W * 0.04, top + H * 0.02)
        ctx.scale(1, 0.26)
        rg = ctx.createRadialGradient(0, 0, 0, 0, 0, W * 0.4)
        rg.addColorStop(0, rgba(light, 0.85))
        rg.addColorStop(0.45, rgba(light, 0.32))
        rg.addColorStop(1, rgba(light, 0))
        ctx.fillStyle = rg
        ctx.beginPath()
        ctx.arc(0, 0, W * 0.4, 0, Math.PI * 2)
        ctx.fill()
        ctx.restore()

        // a quiet floor under the words at the foot
        const fg = ctx.createLinearGradient(x, y + H * 0.62, x, y + H)
        fg.addColorStop(0, rgba(deep, 0))
        fg.addColorStop(1, rgba(deep, 0.38))
        ctx.fillStyle = fg
        ctx.fillRect(x, y, W, H)
    }

    function paintDots(ctx, x, y, W, H) {
        const bg = ctx.createLinearGradient(x, y, x, y + H)
        bg.addColorStop(0, "#1A1B20")
        bg.addColorStop(1, "#08090B")
        ctx.fillStyle = bg
        ctx.fillRect(x, y, W, H)

        const k = W / 420                       // everything scales with the card
        const cx = x + W / 2, cy = y + H * 1.06
        const rings = 9, r0 = H * 0.24, r1 = H * 0.86
        const step = (r1 - r0) / (rings - 1)
        const q = quarters || []
        const lit = []                          // the lit dots, drawn one by one in their own colours
        ctx.beginPath()                         // the unlit ones, all in one path and one fill
        for (let ring = 0; ring < rings; ring++) {
            const rr = r0 + ring * step
            const count = Math.max(8, Math.round(Math.PI * rr / (12.5 * k)))
            const size = 4.4 * k * (0.38 + 0.62 * Math.sin(Math.PI * (ring + 0.6) / (rings + 0.2)))
            for (let j = 0; j <= count; j++) {
                const t = j / count
                const a = Math.PI * (1 - t)
                const px = cx + rr * Math.cos(a), py = cy - rr * Math.sin(a)
                if (py > y + H + size) continue
                const quarter = Math.min(95, Math.floor(t * 96))
                const v = q[quarter] || 0
                if (quarter === now || v > 0) { lit.push([px, py, size, t, v, quarter === now]); continue }
                ctx.moveTo(px + size, py)
                ctx.arc(px, py, size, 0, Math.PI * 2)
            }
        }
        ctx.fillStyle = "rgba(255,255,255,0.11)"
        ctx.fill()
        for (const d of lit) {
            ctx.fillStyle = d[5] ? "#F0603F" : rgba(skyAt(d[3]), 0.42 + 0.58 * d[4])
            ctx.beginPath()
            ctx.arc(d[0], d[1], d[5] ? d[2] * 1.12 : d[2], 0, Math.PI * 2)
            ctx.fill()
        }
        // the twelve 时辰 around the rim: 子 at midnight, 午 at the top
        const names = "子丑寅卯辰巳午未申酉戌亥子"
        ctx.fillStyle = "rgba(255,255,255,0.42)"
        ctx.font = Math.round(10 * k) + "px \"" + Theme.serif + "\""
        ctx.textAlign = "center"
        ctx.textBaseline = "middle"
        const rl = r1 + 15 * k
        for (let i = 1; i < 12; i++) {
            const a = Math.PI * (1 - i / 12)
            ctx.fillText(names[i], cx + rl * Math.cos(a), cy - rl * Math.sin(a))
        }
        // a floor under the words at the foot
        const fg = ctx.createLinearGradient(x, y + H * 0.55, x, y + H)
        fg.addColorStop(0, "rgba(8,9,11,0)")
        fg.addColorStop(1, "rgba(8,9,11,0.82)")
        ctx.fillStyle = fg
        ctx.fillRect(x, y, W, H)
    }
}
