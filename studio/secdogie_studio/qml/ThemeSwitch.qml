// 墨 / 宣纸: a pill with the sun and the moon; the cinnabar knob sits on the
// world you are in. `chosen(dark, x, y)` says which was picked and where the
// knob is in the window, so the ink can fall from there (InkFlood.qml).
import QtQuick
import QtQuick.Controls
import "."

AbstractButton {
    id: sw
    property bool dark: Theme.dark
    signal chosen(bool dark, real x, real y)

    implicitWidth: 76
    implicitHeight: 36
    hoverEnabled: true
    focusPolicy: Qt.TabFocus
    Accessible.role: Accessible.CheckBox
    Accessible.checkable: true
    Accessible.checked: sw.dark
    Accessible.name: Theme.t("夜间（墨）", "Night (ink)")
    HoverHandler { cursorShape: Qt.PointingHandCursor }
    Keys.onSpacePressed: sw.animateClick()
    Keys.onReturnPressed: sw.animateClick()

    onClicked: {
        const p = knob.mapToItem(null, knob.width / 2, knob.height)
        chosen(!sw.dark, p.x, p.y)
    }

    component Glyph: Canvas {
        property bool moon: false
        property color ink: Theme.soft
        width: 16; height: 16
        onInkChanged: requestPaint()
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            ctx.fillStyle = ink
            ctx.strokeStyle = ink
            if (moon) {                                        // a crescent: a disc, minus a disc
                ctx.beginPath(); ctx.arc(8, 8, 6.5, 0, Math.PI * 2); ctx.fill()
                ctx.globalCompositeOperation = "destination-out"
                ctx.beginPath(); ctx.arc(11.2, 5.4, 5.6, 0, Math.PI * 2); ctx.fill()
            } else {                                           // a sun: a disc and eight rays
                ctx.beginPath(); ctx.arc(8, 8, 3.2, 0, Math.PI * 2); ctx.fill()
                ctx.lineWidth = 1.5; ctx.lineCap = "round"
                for (let i = 0; i < 8; i++) {
                    const a = i * Math.PI / 4
                    ctx.beginPath()
                    ctx.moveTo(8 + Math.cos(a) * 5.4, 8 + Math.sin(a) * 5.4)
                    ctx.lineTo(8 + Math.cos(a) * 7.2, 8 + Math.sin(a) * 7.2)
                    ctx.stroke()
                }
            }
        }
    }

    background: Rectangle {
        radius: height / 2
        color: Theme.inverse
        Rectangle { anchors.fill: parent; radius: parent.radius; color: "transparent"
                    border.color: sw.visualFocus ? Theme.focus : "transparent"; border.width: 2 }
    }
    contentItem: Item {
        Glyph { x: 10; anchors.verticalCenter: parent.verticalCenter; moon: false
                ink: sw.dark ? Theme.inverseSoft : Theme.sealText; z: 2 }
        Glyph { x: parent.width - 26; anchors.verticalCenter: parent.verticalCenter; moon: true
                ink: sw.dark ? Theme.sealText : Theme.inverseSoft; z: 2 }
        Rectangle {
            id: knob
            width: 30; height: 30; radius: 15
            anchors.verticalCenter: parent.verticalCenter
            x: sw.dark ? parent.width - width - 3 : 3
            color: Theme.seal
            Behavior on x { NumberAnimation { duration: Theme.still ? 0 : 260; easing.type: Easing.OutCubic } }
        }
    }
}
