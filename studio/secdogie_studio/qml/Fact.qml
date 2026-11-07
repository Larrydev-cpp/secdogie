// One fact about the node: a label and its value on one line. A DID can be
// copied: click it (or press Enter on it) and it says so.
import QtQuick
import QtQuick.Controls
import "."

Row {
    id: fact
    property string label
    property string value
    property bool copyable: value.indexOf("did:") === 0
    property int labelWidth: 112
    property color tone: Theme.text
    spacing: 12
    Text {
        width: fact.labelWidth; height: 28
        verticalAlignment: Text.AlignVCenter
        text: fact.label
        textFormat: Text.PlainText
        color: Theme.faint
        font.pixelSize: Theme.meta
        elide: Text.ElideRight
    }
    AbstractButton {
        id: button
        property bool copied: false
        width: fact.width - fact.labelWidth - fact.spacing; height: 28
        padding: 0
        enabled: fact.copyable
        hoverEnabled: true
        text: fact.value
        Accessible.name: fact.copyable ? "复制" + fact.label : text
        Keys.onReturnPressed: button.animateClick()
        HoverHandler { cursorShape: Qt.PointingHandCursor }
        Timer { id: copiedFor; interval: 1600; onTriggered: button.copied = false }
        onClicked: { clipboard.text = text; clipboard.selectAll(); clipboard.copy(); clipboard.text = ""
                     copied = true; copiedFor.restart() }
        TextEdit { id: clipboard; visible: false; textFormat: TextEdit.PlainText }
        contentItem: Item {
            Text {
                anchors.left: parent.left
                anchors.right: note.visible ? note.left : parent.right
                anchors.rightMargin: note.visible ? 8 : 0
                height: parent.height
                verticalAlignment: Text.AlignVCenter
                text: button.text
                textFormat: Text.PlainText
                elide: Text.ElideMiddle
                color: fact.tone
                font.pixelSize: Theme.body
                font.features: ({ "tnum": 1 })
            }
            Text {
                id: note
                anchors.right: parent.right
                height: parent.height
                verticalAlignment: Text.AlignVCenter
                visible: button.enabled && (button.hovered || button.visualFocus || button.copied)
                text: button.copied ? "已复制" : "复制"
                color: button.copied ? Theme.calm : Theme.soft
                font.pixelSize: Theme.meta
            }
        }
        background: Item { Ring { visible: button.visualFocus; round: 6 } }
    }
}
