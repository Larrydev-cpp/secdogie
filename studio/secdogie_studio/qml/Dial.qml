// A day on a dial, told in 时辰 (the twelve two-hour periods, 子 at the top):
// a tick for every quarter hour, lit in gold for the 时辰 you were at this
// computer today, a cinnabar hand at now, and the time in the middle.
import QtQuick
import "."

Item {
    id: dial
    property var present: []                 // 时辰 indices (0 = 子 ... 11 = 亥) you were here in
    property int now: 0                      // the current 时辰
    property string time: ""                 // "14:05"
    readonly property var names: ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]

    implicitWidth: 180
    implicitHeight: 180
    Accessible.role: Accessible.Graphic
    Accessible.name: Theme.t("今天你在场的时辰：", "Today you were here in: ")
                     + (present.length ? present.map(i => names[i]).join("、") : Theme.t("还没有", "none yet"))

    Canvas {
        id: face
        anchors.fill: parent
        property color tick: Theme.tinted(Theme.text, 0.30)
        property color lit: Theme.gold
        property color ring: Theme.text
        property color hand: Theme.seal
        onTickChanged: requestPaint()
        onLitChanged: requestPaint()
        Connections { target: dial; function onPresentChanged() { face.requestPaint() }
                      function onTimeChanged() { face.requestPaint() } }
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            const cx = width / 2, cy = height / 2
            const R = Math.min(width, height) / 2
            ctx.lineWidth = Math.max(3, R * 0.05)
            ctx.strokeStyle = ring
            ctx.beginPath(); ctx.arc(cx, cy, R - ctx.lineWidth / 2, 0, Math.PI * 2); ctx.stroke()
            const set = {}
            for (const i of dial.present) set[i] = true
            for (let q = 0; q < 96; q++) {                       // 96 quarters of a day, 子 (00:00) at the top
                const hour = q / 4
                const sc = Math.floor(((hour + 1) % 24) / 2)
                const a = (hour / 24) * Math.PI * 2 - Math.PI / 2
                const long = q % 8 === 4                          // the middle of each 时辰
                const r1 = R * (long ? 0.50 : 0.58), r2 = R * 0.84
                ctx.strokeStyle = set[sc] ? lit : tick
                ctx.lineWidth = set[sc] ? Math.max(2, R * 0.022) : Math.max(1.2, R * 0.012)
                ctx.beginPath()
                ctx.moveTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1)
                ctx.lineTo(cx + Math.cos(a) * r2, cy + Math.sin(a) * r2)
                ctx.stroke()
            }
            const parts = dial.time.split(":")
            if (parts.length === 2) {                            // now
                const h = Number(parts[0]) + Number(parts[1]) / 60
                const a = (h / 24) * Math.PI * 2 - Math.PI / 2
                ctx.strokeStyle = hand
                ctx.lineWidth = Math.max(2, R * 0.025)
                ctx.beginPath()
                ctx.moveTo(cx + Math.cos(a) * R * 0.46, cy + Math.sin(a) * R * 0.46)
                ctx.lineTo(cx + Math.cos(a) * R * 0.9, cy + Math.sin(a) * R * 0.9)
                ctx.stroke()
            }
        }
    }
    Rectangle {                                                    // the middle: now, and its 时辰
        anchors.centerIn: parent
        width: dial.width * 0.42
        height: width
        radius: width / 2
        color: Theme.inverse
        Column {
            anchors.centerIn: parent
            spacing: 2
            Text { anchors.horizontalCenter: parent.horizontalCenter; text: dial.time; color: Theme.inverseText
                   font.pixelSize: Math.round(dial.width * 0.105); font.weight: Font.DemiBold
                   font.features: ({ "tnum": 1 }) }
            Text { anchors.horizontalCenter: parent.horizontalCenter; text: dial.names[dial.now] + Theme.t("时", "")
                   color: Theme.inverseSoft; font.family: Theme.serif; font.pixelSize: Math.round(dial.width * 0.07) }
        }
    }
}
