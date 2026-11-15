// How a part arrives: it fades in while it grows from a little small, past its
// size and back (an overshoot: Q弹). `delay` staggers a group. Scale and
// opacity only, so it never fights a layout. For someone who asked for less
// motion, it is a short fade.
//   Appear { id: a; target: title; delay: 70 }   ...   a.play()
import QtQuick
import ".."

QtObject {
    id: appear
    property Item target
    property int delay: 0
    function play() { if (target) motion.restart() }

    property SequentialAnimation motion: SequentialAnimation {
        PropertyAction { target: appear.target; property: "opacity"; value: 0 }
        PropertyAction { target: appear.target; property: "scale"; value: Theme.still ? 1 : 0.92 }
        PauseAnimation { duration: appear.delay }
        ParallelAnimation {
            NumberAnimation { target: appear.target; property: "opacity"; to: 1; duration: Soft.arrive * 0.55
                              easing.type: Easing.OutCubic }
            NumberAnimation { target: appear.target; property: "scale"; to: 1; duration: Soft.arrive
                              easing.type: Easing.OutBack; easing.overshoot: Soft.overshoot }
        }
    }
}
