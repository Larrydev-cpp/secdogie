// The gate rules, as YAML: write them, try a sentence against them, then sign
// with the operator passphrase and inject them into the node.
import QtQuick
import QtQuick.Controls
import "."

Column {
    id: editor
    property bool showInForce: true
    property real editorHeight: 200
    function load() { if (rules.text === "") rules.text = P.rulesText }
    onVisibleChanged: if (visible) load()
    spacing: 16

    Rectangle {
        width: parent.width; height: editor.editorHeight; radius: 12
        color: Theme.well
        border.color: rules.activeFocus ? Theme.focus : Theme.hairline
        Behavior on border.color { ColorAnimation { duration: Theme.touch } }
        ScrollView {
            id: rulesView
            anchors.fill: parent
            anchors.margins: 1
            ScrollBar.vertical: Bar {
                parent: rulesView
                x: rulesView.width - width
                y: rulesView.topPadding
                height: rulesView.availableHeight
            }
            ScrollBar.horizontal: Bar {
                parent: rulesView
                x: rulesView.leftPadding
                y: rulesView.height - height
                width: rulesView.availableWidth
            }
            TextArea {
                id: rules
                padding: 12
                color: Theme.text
                font.pixelSize: Theme.meta
                font.family: Qt.platform.os === "osx" ? "Menlo"
                             : (Qt.platform.os === "windows" ? "Consolas" : "monospace")
                selectionColor: Theme.tinted(Theme.focus, 0.38)
                selectedTextColor: Theme.text
                selectByMouse: true
                textFormat: TextEdit.PlainText
                wrapMode: TextEdit.NoWrap
                background: null
                cursorDelegate: Caret { host: rules }
                Accessible.name: "门的规则（YAML）"
            }
        }
    }
    Row {
        width: parent.width
        spacing: 12
        Field { id: sample; width: parent.width - tryButton.width - 12; label: "试一句话"
                hint: "比如：帮我批量注册十个账号"; onAccepted: P.tryRules(rules.text, sample.text) }
        Pill { id: tryButton; y: sample.height - height; text: "试一试"
               onClicked: P.tryRules(rules.text, sample.text) }
    }
    Row {
        width: parent.width
        spacing: 12
        Field { id: rulesPass; width: parent.width - pushButton.width - 12; secret: true; label: "签名口令"
                hint: "签好就交给节点"; onAccepted: if (pushButton.enabled) pushButton.clicked() }
        Pill { id: pushButton; y: rulesPass.height - height; text: "签名并注入"; primary: true
               enabled: rulesPass.text.length > 0 && !P.working
               onClicked: { P.pushRules(rules.text, rulesPass.text); rulesPass.text = "" } }
    }
    Text { width: parent.width; text: P.working ? P.working + "……" : P.rulesNote; textFormat: Text.PlainText
           wrapMode: Text.Wrap; color: Theme.soft; font.pixelSize: Theme.body; lineHeight: 1.3
           visible: text.length > 0 }
    Text { width: parent.width; text: P.inForce; textFormat: Text.PlainText; wrapMode: Text.Wrap
           color: Theme.faint; font.pixelSize: Theme.meta; visible: editor.showInForce && text.length > 0 }
}
