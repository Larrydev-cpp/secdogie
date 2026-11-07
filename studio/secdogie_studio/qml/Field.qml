// One line to type into: a visible label when its meaning would vanish once
// you type, an example while it is empty, an underline that lights with focus.
import QtQuick
import QtQuick.Controls
import "."

FocusScope {
    id: field
    property alias text: input.text
    property string label
    property string hint
    property bool secret: false
    signal accepted
    implicitHeight: column.implicitHeight
    Column {
        id: column
        width: parent.width
        spacing: 6
        Text {
            visible: field.label !== ""
            text: field.label
            textFormat: Text.PlainText
            color: Theme.soft
            font.pixelSize: Theme.meta
        }
        TextField {
            id: input
            focus: true
            width: parent.width
            implicitHeight: 40
            leftPadding: 0
            rightPadding: 0
            topPadding: 0
            bottomPadding: 0
            verticalAlignment: TextInput.AlignVCenter
            color: Theme.text
            placeholderText: field.hint
            placeholderTextColor: Theme.faint
            selectionColor: Theme.tinted(Theme.focus, 0.38)
            selectedTextColor: Theme.text
            font.pixelSize: Theme.body
            echoMode: field.secret ? TextInput.Password : TextInput.Normal
            passwordCharacter: "•"
            inputMethodHints: field.secret ? (Qt.ImhSensitiveData | Qt.ImhNoPredictiveText | Qt.ImhNoAutoUppercase)
                                           : Qt.ImhNone
            selectByMouse: true
            Accessible.name: field.label !== "" ? field.label : field.hint
            Accessible.passwordEdit: field.secret
            onAccepted: field.accepted()
            cursorDelegate: Caret { host: input }
            background: Item {
                Rectangle {
                    anchors.bottom: parent.bottom
                    width: parent.width
                    height: input.activeFocus ? 2 : 1
                    color: input.activeFocus ? Theme.focus : (input.hovered ? Theme.soft : Theme.edge)
                    Behavior on color { ColorAnimation { duration: Theme.touch } }
                }
            }
        }
    }
}
