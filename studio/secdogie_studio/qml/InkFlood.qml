// The change between 墨 and 宣纸: a thread of ink drops from the switch to the
// foot of the window, splashes, and the ink rises with a wavering edge until
// it covers everything; under it the world changes (`covered`), and the ink
// lifts away. Going to paper, the same with paper. For someone who asked for
// less motion, it is only a short fade.
import QtQuick
import QtQuick.Shapes
import "."

Item {
    id: flood
    anchors.fill: parent
    visible: running
    z: 1000

    property bool running: false
    property color ink: "#12151D"
    property real fromX: width / 2
    property real fromY: 0
    property real thread: 0                    // 0..1: how far the thread has fallen
    property real level: 1                     // 1: nothing risen; 0: covered to the top
    property real phase: 0                     // the edge's waver
    property real splash: 0                    // 0..1: the drops at the foot
    signal covered()

    function play(toColor, x, y) {
        if (running) return
        ink = toColor
        fromX = x
        fromY = y
        thread = 0; level = 1; phase = 0; splash = 0; opacity = 1
        running = true
        if (Theme.still) quick.restart()
        else story.restart()
    }

    MouseArea { anchors.fill: parent; enabled: flood.running; hoverEnabled: true }   // the window holds still meanwhile

    Rectangle {                                // the thread
        x: flood.fromX - 1
        y: flood.fromY
        width: 2
        height: Math.max(0, flood.thread * (flood.height - flood.fromY))
        color: flood.ink
        opacity: flood.level > 0.05 ? 1 : 0
    }
    Repeater {                                 // the splash where it lands
        model: [[-26, 0.9], [-9, 1.4], [14, 1.1], [30, 0.7]]
        Rectangle {
            required property var modelData
            width: 6 * modelData[1]; height: width; radius: width / 2
            color: flood.ink
            x: flood.fromX + modelData[0] * flood.splash - width / 2
            y: flood.height - 6 - Math.sin(flood.splash * Math.PI) * 34 * modelData[1]
            opacity: flood.splash > 0 && flood.splash < 1 ? 1 - flood.splash * 0.6 : 0
        }
    }
    Shape {                                    // the rising ink, its edge wavering
        id: wave
        anchors.fill: parent
        readonly property real base: flood.level * (flood.height + 40) - 20
        readonly property real amp: 16 * Math.min(1, (1 - flood.level) * 6) * Math.min(1, flood.level * 6 + 0.15)
        readonly property real w: flood.width
        ShapePath {
            id: edge
            fillColor: flood.ink
            strokeColor: "transparent"
            readonly property real s: Math.sin(flood.phase)
            readonly property real c: Math.cos(flood.phase)
            startX: 0
            startY: wave.base + wave.amp * edge.s
            PathCubic { x: wave.w * 0.25; y: wave.base - wave.amp * edge.c
                        control1X: wave.w * 0.08; control1Y: wave.base + wave.amp * (edge.s + 1)
                        control2X: wave.w * 0.17; control2Y: wave.base - wave.amp * (edge.c + 1) }
            PathCubic { x: wave.w * 0.5; y: wave.base - wave.amp * edge.s
                        control1X: wave.w * 0.33; control1Y: wave.base - wave.amp * (edge.c - 1)
                        control2X: wave.w * 0.42; control2Y: wave.base + wave.amp * (edge.s + 1) }
            PathCubic { x: wave.w * 0.75; y: wave.base + wave.amp * edge.c
                        control1X: wave.w * 0.58; control1Y: wave.base - wave.amp * (edge.s + 1)
                        control2X: wave.w * 0.67; control2Y: wave.base + wave.amp * (edge.c + 1) }
            PathCubic { x: wave.w; y: wave.base + wave.amp * edge.s
                        control1X: wave.w * 0.83; control1Y: wave.base - wave.amp * (edge.c - 1)
                        control2X: wave.w * 0.92; control2Y: wave.base + wave.amp * (edge.s - 1) }
            PathLine { x: wave.w; y: flood.height + 40 }
            PathLine { x: 0; y: flood.height + 40 }
            PathLine { x: 0; y: wave.base + wave.amp * edge.s }
        }
    }

    SequentialAnimation {
        id: story
        NumberAnimation { target: flood; property: "thread"; from: 0; to: 1; duration: 380; easing.type: Easing.InQuad }
        ParallelAnimation {
            NumberAnimation { target: flood; property: "splash"; from: 0; to: 1; duration: 520; easing.type: Easing.OutQuad }
            SequentialAnimation {
                PauseAnimation { duration: 90 }
                ParallelAnimation {
                    NumberAnimation { target: flood; property: "level"; from: 1; to: 0; duration: 880
                                      easing.type: Easing.InOutCubic }
                    NumberAnimation { target: flood; property: "phase"; from: 0; to: Math.PI * 3; duration: 880 }
                }
            }
        }
        ScriptAction { script: flood.covered() }
        PauseAnimation { duration: 60 }
        NumberAnimation { target: flood; property: "opacity"; to: 0; duration: 340; easing.type: Easing.OutCubic }
        ScriptAction { script: flood.running = false }
    }
    SequentialAnimation {
        id: quick
        ScriptAction { script: { flood.thread = 0; flood.level = 0; flood.opacity = 0 } }
        NumberAnimation { target: flood; property: "opacity"; to: 1; duration: 100 }
        ScriptAction { script: flood.covered() }
        NumberAnimation { target: flood; property: "opacity"; to: 0; duration: 140 }
        ScriptAction { script: flood.running = false }
    }
}
