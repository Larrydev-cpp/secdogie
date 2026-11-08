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
    property bool bare: false                   // no underline: the field sits in a box of its own (the composer)
    property bool revealable: false             // a secret that can be shown while typing
    property bool revealed: false
    readonly property bool focused: input.activeFocus
    property int size: Theme.body
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
            rightPadding: field.revealable ? 40 : 0
            topPadding: 0
            bottomPadding: 0
            verticalAlignment: TextInput.AlignVCenter
            color: Theme.text
            placeholderText: field.hint
            placeholderTextColor: Theme.faint
            selectionColor: Theme.tinted(Theme.focus, 0.38)
            selectedTextColor: Theme.text
            font.pixelSize: field.size
            echoMode: field.secret && !field.revealed ? TextInput.Password : TextInput.Normal
            passwordCharacter: "•"
            inputMethodHints: field.secret ? (Qt.ImhSensitiveData | Qt.ImhNoPredictiveText | Qt.ImhNoAutoUppercase)
                                           : Qt.ImhNone
            selectByMouse: true
            Accessible.name: field.label !== "" ? field.label : field.hint
            Accessible.passwordEdit: field.secret
            onAccepted: field.accepted()
            cursorDelegate: Caret { host: input }
            IconButton {
                visible: field.revealable
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                diameter: 32
                iconSize: 17
                focusPolicy: Qt.TabFocus
                symbol: field.revealed ? "eyeOff" : "eye"
                text: field.revealed ? "隐藏口令" : "显示口令"
                onClicked: field.revealed = !field.revealed
            }
            background: Item {
                Rectangle {
                    visible: !field.bare
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
