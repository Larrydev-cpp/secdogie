// A drawn icon: one stroke weight (1.6 px at 20 px), round caps and joins,
// from a small set authored for Studio on a 24 px grid. Decorative: the
// control that carries it has the accessible name.
import QtQuick
import QtQuick.Shapes
import "."

Item {
    id: icon
    property string name
    property real size: 20
    property color color: Theme.soft
    property real stroke: 1.6
    implicitWidth: size
    implicitHeight: size
    Accessible.ignored: true

    readonly property var paths: ({
        "chat": "M6 4.5h12a2.5 2.5 0 0 1 2.5 2.5v8a2.5 2.5 0 0 1-2.5 2.5h-7l-4.5 3.5v-3.5H6A2.5 2.5 0 0 1 3.5 15V7A2.5 2.5 0 0 1 6 4.5z",
        "mesh": "M12 3.5a2.5 2.5 0 1 1 0 5a2.5 2.5 0 1 1 0-5zM5 15.5a2.5 2.5 0 1 1 0 5a2.5 2.5 0 1 1 0-5zM19 15.5a2.5 2.5 0 1 1 0 5a2.5 2.5 0 1 1 0-5zM10.9 8.3L6.1 15.7M13.1 8.3l4.8 7.4M7.5 18h9",
        "power": "M12 3.5v8M7.4 6.4a7 7 0 1 0 9.2 0",
        "send": "M12 19V5.5M6 11.5l6-6 6 6",
        "eye": "M2.5 12s3.5-6.5 9.5-6.5S21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12zM12 9a3 3 0 1 1 0 6a3 3 0 1 1 0-6z",
        "eyeOff": "M3.5 3.5l17 17M10.5 5.6c.5-.1 1-.1 1.5-.1 6 0 9.5 6.5 9.5 6.5a16 16 0 0 1-2.9 3.7M6.7 6.7A16 16 0 0 0 2.5 12s3.5 6.5 9.5 6.5c1.6 0 3-.4 4.3-1M9.9 9.9a3 3 0 0 0 4.2 4.2",
        "check": "M5 12.5l4.5 4.5L19 7.5",
        "shield": "M12 3.5l7 2.8v5.4c0 4.3-3 7.9-7 9.3-4-1.4-7-5-7-9.3V6.3z",
        "key": "M8 6a4 4 0 1 1 0 8a4 4 0 1 1 0-8zM10.9 12.9l8.6 8.1M15.6 17.3l2.4-2.4M18 19.6l2-2",
        "arrow": "M5 12h14M13 6l6 6-6 6",
        "back": "M19 12H5M11 6l-6 6 6 6",
        "copy": "M9 9h9.5v11H9zM15 9V4.5H5.5V15H9"
    })

    Shape {
        width: 24
        height: 24
        scale: icon.size / 24
        transformOrigin: Item.TopLeft
        antialiasing: true
        ShapePath {
            strokeColor: icon.color
            strokeWidth: icon.stroke * 24 / Math.max(1, icon.size)
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            joinStyle: ShapePath.RoundJoin
            PathSvg { path: icon.paths[icon.name] || "" }
        }
    }
}
