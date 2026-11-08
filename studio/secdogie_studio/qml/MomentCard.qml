// What waits on the person, as a card's content: one question, one signature,
// one memory, or the first hello. What the companion says is the heading;
// then what a signature commits to (or why), the fields, the actions, and the
// answer to what the person did. Used by the light's card and the home window.
import QtQuick
import QtQuick.Controls
import "."

Column {
    id: card
    property var c                              // the card (app.card_dict), or null
    readonly property string kind: c ? c.kind : ""
    readonly property color tint: kind === "sign" ? Theme.sign : (kind === "memory" ? Theme.warm : Theme.calm)
    spacing: 16

    property bool guard: true                   // a signature cannot be approved in its first 0.8 s
    Timer { id: guardTimer; interval: 800; onTriggered: card.guard = false }
    function reset() { guard = true; guardTimer.restart(); free.text = ""; pass1.text = ""; pass2.text = "" }
    function approve() {
        if (!approveButton.enabled) return
        P.approve(c.id, pass1.text)
        pass1.text = ""
    }
    Component.onCompleted: reset()
    Accessible.role: Accessible.Grouping
    Accessible.name: c ? c.label : ""

    // What it says: the first paragraph is the heading; a question with its
    // reasons (the automatic Socratic gate's) goes on below it, in paragraphs.
    readonly property var parts: (c ? c.says : "").split("\n\n")
    property int restParagraphs: 0              // 0: all of them (the home window); the light keeps it short
    readonly property var rest: parts.slice(1, restParagraphs > 0 ? 1 + restParagraphs : parts.length)
    readonly property bool cut: rest.length < parts.length - 1
    signal readAll                              // the rest did not fit: read it in the home window
    Text {
        width: parent.width
        text: card.parts[0]
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        maximumLineCount: 6
        elide: Text.ElideRight
        color: Theme.text
        font.pixelSize: Theme.lead
        font.weight: Font.Medium
        lineHeight: 1.35
    }
    Column {
        visible: card.rest.length > 0
        width: parent.width
        spacing: 10
        Repeater {
            model: card.rest
            Text {
                required property string modelData
                width: parent.width
                text: modelData
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                maximumLineCount: card.restParagraphs > 0 ? 8 : 1000
                elide: Text.ElideRight
                color: Theme.soft
                font.pixelSize: Theme.body
                lineHeight: 1.6
            }
        }
    }
    Link { visible: card.cut; text: "在主界面里看完整的"; onClicked: card.readAll() }
    Column {
        width: parent.width
        spacing: 8
        visible: !!card.c && card.c.details.length > 0
        Repeater {
            model: card.c ? card.c.details : []
            Row {
                id: detail
                required property var modelData
                readonly property bool plain: modelData[0] === ""
                readonly property bool refusal: modelData[0] === "不能签" || modelData[0] === "不能记"
                width: card.width
                spacing: 12
                Text {
                    visible: !detail.plain
                    width: 56
                    topPadding: 2
                    text: detail.modelData[0]
                    textFormat: Text.PlainText
                    color: Theme.faint
                    font.pixelSize: Theme.meta
                }
                Text {
                    width: detail.plain ? parent.width : parent.width - 68
                    text: detail.modelData[1]
                    textFormat: Text.PlainText
                    wrapMode: detail.plain ? Text.Wrap : Text.WrapAnywhere
                    color: detail.refusal ? Theme.warm : (detail.plain ? Theme.soft : Theme.text)
                    font.pixelSize: Theme.body
                    font.features: ({ "tnum": 1 })
                    lineHeight: detail.plain ? 1.5 : 1.3
                }
            }
        }
    }
    Field {
        id: pass1
        width: parent.width
        secret: true
        visible: card.kind === "sign" || card.kind === "welcome"
        label: card.kind === "welcome" ? "你的口令" : "签名口令"
        hint: card.kind === "welcome" ? "至少 8 位，只有你知道" : "输入后按回车签名"
        onAccepted: if (card.kind === "sign") card.approve()
    }
    Field {
        id: pass2
        width: parent.width
        secret: true
        visible: card.kind === "welcome"
        label: "再输一次"
        hint: "和上面一样"
        onAccepted: if (!P.working) P.welcome(pass1.text, pass2.text, "")
    }
    Flow {
        width: parent.width
        spacing: 8
        Repeater {
            model: card.kind === "ask" && card.c ? card.c.options : []
            Pill { required property string modelData; required property int index
                   text: modelData; primary: index === 0; tint: card.tint
                   onClicked: P.answer(card.c.id, modelData) }
        }
        Pill { id: approveButton; visible: card.kind === "sign"; text: "签名同意"; primary: true; tint: Theme.sign
               enabled: !!card.c && !card.guard && card.c.canApprove && pass1.text.length > 0 && !P.working
               onClicked: card.approve() }
        Pill { visible: card.kind === "sign"; text: "这次不签"; onClicked: P.deny(card.c.id) }
        Pill { visible: card.kind === "memory"; text: "记住吧"; primary: true; tint: Theme.warm
               enabled: !!card.c && card.c.canApprove; onClicked: P.remember(card.c.id, true) }
        Pill { visible: card.kind === "memory"; text: "别记"; onClicked: P.remember(card.c.id, false) }
        Pill { visible: card.kind === "welcome"; text: "生成钥匙"; primary: true; enabled: !P.working
               onClicked: P.welcome(pass1.text, pass2.text, "") }
    }
    Row {                                       // or say it in your own words
        width: parent.width
        spacing: 12
        visible: card.kind === "ask" && !!card.c && card.c.freeText
        Field {
            id: free
            width: parent.width - reply.width - 12
            hint: card.c && card.c.options.length ? "或者直接告诉我……" : "直接告诉我……"
            onAccepted: if (text.trim().length) P.answer(card.c.id, text)
        }
        Pill { id: reply; text: "回答"; enabled: free.text.trim().length > 0
               onClicked: P.answer(card.c.id, free.text) }
    }
    Text {
        width: parent.width
        visible: text.length > 0
        text: P.working ? P.working + "……" : P.feedback
        textFormat: Text.PlainText
        color: P.working ? Theme.soft : Theme.warm
        font.pixelSize: Theme.body
        lineHeight: 1.3
        wrapMode: Text.Wrap
    }
}
