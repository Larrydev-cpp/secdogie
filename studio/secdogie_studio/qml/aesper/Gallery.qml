// Aesper's parts together, the way the pages will use them: the misty
// backdrop, a sheet with a torn painted half, the display line, the companion
// as a figure of light, a link underlined by hand and a caption beneath it.
// The parts arrive one after another, with a small bounce. A window of its
// own, for looking at the parts (and for their tests); it needs nothing from
// the bridge.
import QtQuick
import QtQuick.Window
import "."
import ".."

Window {
    id: gallery
    title: "Aesper"
    width: 1240
    height: 820
    visible: true
    color: Soft.mistGround
    property bool night: false
    property string lang: "en"
    Binding { target: Theme; property: "dark"; value: gallery.night }
    Binding { target: Theme; property: "lang"; value: gallery.lang }
    function t(zh, en) { return Theme.t(zh, en) }

    MistBackdrop { anchors.fill: parent }

    SoftSheet {
        id: sheet
        width: Math.min(gallery.width * 0.76, 1020)
        height: Math.min(gallery.height * 0.8, 660)
        anchors.centerIn: parent
        split: 0.52

        DisplayTitle {
            id: title
            x: Soft.s7
            y: Soft.s7
            width: sheet.wordsArea.width - 2 * x
            size: Theme.lang === "en" ? 48 : 44
            text: gallery.t("给夜里\n还在想事情的人", "A companion for\nnight thinkers")
        }
        GlowFigure {
            id: figure
            anchors.horizontalCenter: parent.horizontalCenter
            y: title.y + title.height + Soft.s2
            width: Math.min(sheet.wordsArea.width * 0.7, 320)
            height: Math.max(180, link.y - y - Soft.s2)
        }
        WavyLink {
            id: link
            anchors.horizontalCenter: parent.horizontalCenter
            y: caption.y - height - Soft.s4
            text: gallery.t("跟它说说话", "Start a conversation")
        }
        Caption {
            id: caption
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom
            anchors.bottomMargin: Soft.s7
            width: Math.min(sheet.wordsArea.width - 2 * Soft.s7, 340)
            text: gallery.t("拿不准就先问你；改不回来的事，一定等你签名。",
                            "It asks when it is unsure, and anything that can't be undone waits for your signature.")
        }
    }

    Appear { id: a0; target: sheet }
    Appear { id: a1; target: title; delay: Soft.stagger * 2 }
    Appear { id: a2; target: figure; delay: Soft.stagger * 3 }
    Appear { id: a3; target: link; delay: Soft.stagger * 4 }
    Appear { id: a4; target: caption; delay: Soft.stagger * 5 }
    function arrive() { a0.play(); a1.play(); a2.play(); a3.play(); a4.play() }
    Component.onCompleted: arrive()
}
