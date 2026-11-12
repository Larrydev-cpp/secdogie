// Numbers in dots, like a lamp-lit board: each character a 5 x 7 grid, the
// lit dots in ink (or paper), the unlit ones faint, so the shape of every
// place stays visible. Digits and a few signs: . : , - + % / and K M h m d s.
import QtQuick
import "."

Canvas {
    id: board
    property string text: ""
    property real pitch: 6                   // dot centre to dot centre
    property real dot: pitch * 0.72          // a dot's diameter
    property color on: Theme.dotOn
    property color off: Theme.dotOff
    property bool ghosts: true               // draw the unlit dots too

    readonly property var glyphs: ({
        "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
        "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
        "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
        "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
        "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
        "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
        "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
        "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
        "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
        "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
        ".": ["00000", "00000", "00000", "00000", "00000", "01100", "01100"],
        ",": ["00000", "00000", "00000", "00000", "01100", "00100", "01000"],
        ":": ["00000", "01100", "01100", "00000", "01100", "01100", "00000"],
        "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
        "+": ["00000", "00100", "00100", "11111", "00100", "00100", "00000"],
        "%": ["11001", "11010", "00010", "00100", "01000", "01011", "10011"],
        "/": ["00001", "00010", "00010", "00100", "01000", "01000", "10000"],
        "K": ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
        "M": ["10001", "11011", "10101", "10101", "10001", "10001", "10001"],
        "h": ["10000", "10000", "10110", "11001", "10001", "10001", "10001"],
        "m": ["00000", "00000", "11010", "10101", "10101", "10101", "10101"],
        "d": ["00001", "00001", "01101", "10011", "10001", "10011", "01101"],
        "s": ["00000", "00000", "01111", "10000", "01110", "00001", "11110"],
        " ": ["00000", "00000", "00000", "00000", "00000", "00000", "00000"]
    })

    implicitWidth: Math.max(1, text.length) * 6 * pitch - pitch
    implicitHeight: 7 * pitch
    width: implicitWidth
    height: implicitHeight
    Accessible.role: Accessible.StaticText
    Accessible.name: text

    onTextChanged: requestPaint()
    onOnChanged: requestPaint()
    onOffChanged: requestPaint()
    onPitchChanged: requestPaint()

    onPaint: {
        const ctx = getContext("2d")
        ctx.reset()
        const r = dot / 2
        for (let i = 0; i < text.length; i++) {
            const g = glyphs[text[i]] || glyphs[" "]
            for (let row = 0; row < 7; row++) {
                for (let col = 0; col < 5; col++) {
                    const lit = g[row][col] === "1"
                    if (!lit && !ghosts) continue
                    ctx.fillStyle = lit ? on : off
                    ctx.beginPath()
                    ctx.arc(i * 6 * pitch + col * pitch + pitch / 2, row * pitch + pitch / 2, r, 0, Math.PI * 2)
                    ctx.fill()
                }
            }
        }
    }
}
