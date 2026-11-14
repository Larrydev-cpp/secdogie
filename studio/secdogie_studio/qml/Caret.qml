// The text cursor, in the focus colour, blinking at the platform's pace.
import QtQuick
import "."

Rectangle {
    id: caret
    property Item host
    property bool lit: true
    width: 2
    color: Theme.focus
    visible: !!host && host.cursorVisible
    opacity: lit ? 1 : 0
    Timer {
        id: blink
        interval: Math.max(250, (Qt.styleHints.cursorFlashTime || 1000) / 2)
        repeat: true
        running: caret.visible && Qt.styleHints.cursorFlashTime !== 0
        onRunningChanged: caret.lit = true
        onTriggered: caret.lit = !caret.lit
    }
    Connections {
        target: caret.host
        function onCursorPositionChanged() { caret.lit = true; blink.restart() }
    }
}
