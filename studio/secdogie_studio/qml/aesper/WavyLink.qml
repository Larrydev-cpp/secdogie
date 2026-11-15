// A link the way a person would underline it by hand: a pen nib, the words in
// the display serif, and a thick wavy stroke beneath. Under the pointer the
// wave starts to flow; a press gives way and springs back with a small bounce
// (Q弹). Enter or Space presses it; the keyboard gets a ring.
import QtQuick
import QtQuick.Controls
import QtQuick.Shapes
import ".."

AbstractButton {
    id: link
    property bool pen: true
    property real size: 22
    property color tint: Soft.ink
    hoverEnabled: true
    focusPolicy: Qt.TabFocus
    implicitWidth: Math.max(row.implicitWidth, 120) + 24
    implicitHeight: row.implicitHeight + 26
    Accessible.role: Accessible.Link
    Accessible.name: link.text
    HoverHandler { cursorShape: Qt.PointingHandCursor }
    Keys.onReturnPressed: link.animateClick()
    Keys.onSpacePressed: link.animateClick()
    background: null

    // the bounce: a press gives way, a release springs back past rest and settles
    scale: pressed ? 0.94 : (hovered ? 1.03 : 1)
    Behavior on scale { SpringAnimation { spring: Soft.spring; damping: Soft.damping; epsilon: 0.0005 } }

    contentItem: Item {
        Row {
            id: row
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: 10
            Shape {                                    // a fountain-pen nib, drawn
                id: nib
                readonly property real u: link.size * 0.9
                visible: link.pen
                width: link.size * 0.9; height: width
                anchors.verticalCenter: parent.verticalCenter
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    strokeColor: "transparent"
                    fillColor: link.tint
                    startX: nib.u * 0.08; startY: nib.u * 0.92
                    PathLine { x: nib.u * 0.22; y: nib.u * 0.5 }
                    PathLine { x: nib.u * 0.62; y: nib.u * 0.1 }
                    PathLine { x: nib.u * 0.9; y: nib.u * 0.38 }
                    PathLine { x: nib.u * 0.5; y: nib.u * 0.78 }
                    PathLine { x: nib.u * 0.08; y: nib.u * 0.92 }
                }
                ShapePath {                            // the slit and the breather hole
                    strokeColor: Soft.sheet
                    strokeWidth: 1.6
                    fillColor: "transparent"
                    capStyle: ShapePath.RoundCap
                    startX: nib.u * 0.1; startY: nib.u * 0.9
                    PathLine { x: nib.u * 0.44; y: nib.u * 0.56 }
                    PathMove { x: nib.u * 0.5; y: nib.u * 0.5 }
                    PathArc { x: nib.u * 0.5; y: nib.u * 0.5001; radiusX: nib.u * 0.05; radiusY: nib.u * 0.05
                              useLargeArc: true }
                }
            }
            Text {
                text: link.text
                color: link.tint
                font.family: Soft.display
                font.pixelSize: link.size
                font.letterSpacing: Theme.lang === "en" ? -0.3 : 0
                textFormat: Text.PlainText
            }
        }
        Canvas {                                       // the stroke beneath, by hand
            id: wave
            x: (parent.width - width) / 2
            y: row.y + row.height + 8
            width: row.width + 24
            height: 12
            property real phase: 0
            onPhaseChanged: requestPaint()
            onWidthChanged: requestPaint()
            Connections { target: link; function onTintChanged() { wave.requestPaint() } }
            onPaint: {
                const ctx = getContext("2d")
                ctx.reset()
                const w = width, mid = height / 2
                ctx.strokeStyle = link.tint
                ctx.lineWidth = 3.4
                ctx.lineCap = "round"
                ctx.lineJoin = "round"
                ctx.beginPath()
                for (let x = 3; x <= w - 3; x += 2) {
                    // a hand's wave: a gentle swell, a quicker ripple, and a little wobble that never repeats exactly
                    const y = mid + 2.1 * Math.sin(x * 0.42 + phase) + 0.7 * Math.sin(x * 0.11 + 1.7) + 0.35 * Math.sin(x * 1.3)
                    if (x === 3) ctx.moveTo(x, y)
                    else ctx.lineTo(x, y)
                }
                ctx.stroke()
            }
            NumberAnimation on phase {
                running: link.hovered && !Theme.still
                from: 0; to: Math.PI * 2; duration: 900; loops: Animation.Infinite
            }
        }
        Rectangle {                                    // the keyboard's ring
            anchors.fill: parent
            anchors.margins: -4
            radius: 12
            color: "transparent"
            border.color: Theme.focus
            border.width: 2
            visible: link.visualFocus
        }
    }
}
