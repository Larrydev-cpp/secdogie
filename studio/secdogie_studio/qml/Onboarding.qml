// The first meeting, in the home window: who it is, what to call you, the
// passphrase that signs what cannot be undone, the keys being made, and the
// three keys once they are ready.
//
// The steps before the keys (hello, a name, a passphrase) live here; the
// bridge says when the keys are being made ("making") and when they are
// ready ("ready"). Nothing here moves the keyboard focus.
import QtQuick
import QtQuick.Controls
import "."

Item {
    id: meet
    property int step: 0                         // 0 hello, 1 a name, 2 the passphrase
    readonly property string phase: P.onboarding || ""
    readonly property int shown: phase === "making" || phase === "ready" ? 3 : step
    readonly property int strength: score(pass1.text)
    readonly property bool longEnough: pass1.text.length >= 8
    readonly property bool same: pass2.text.length > 0 && pass1.text === pass2.text
    readonly property bool ready: longEnough && same && !P.working

    function score(p) {
        if (p.length === 0) return 0
        if (p.length < 8) return 1
        let s = 1
        if (p.length >= 12) s++
        const kinds = [/[a-z]/, /[A-Z]/, /[0-9]/, /[^A-Za-z0-9]/].filter(r => r.test(p)).length
        if (kinds >= 2) s++
        if (kinds >= 3 || p.length >= 16) s++
        return Math.min(4, s)
    }
    readonly property var strengthWords: ["", "太短了", "还可以", "不错", "很好"]
    function submit() { if (ready) P.welcome(pass1.text, pass2.text, name.text) }

    // ---- parts of a step ----
    component Step: Column {
        id: stepItem
        property int index
        width: stage.width
        readonly property bool here: meet.shown === index
        spacing: 18
        opacity: here ? 1 : 0
        visible: opacity > 0.01
        enabled: here
        Behavior on opacity {
            NumberAnimation { duration: stepItem.here ? Theme.enter : Theme.exit
                              easing.type: stepItem.here ? Easing.OutExpo : Easing.InQuad }
        }
        transform: Translate { x: (1 - stepItem.opacity) * (Theme.still ? 0 : (meet.shown > stepItem.index ? -24 : 24)) }
    }
    component Title: Text {
        width: parent.width
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
        color: Theme.text
        font.pixelSize: Theme.display
        font.weight: Font.Medium
        lineHeight: 1.2
    }
    component Lead: Text {
        width: parent.width
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
        color: Theme.soft
        font.pixelSize: 15
        lineHeight: 1.6
    }
    component Promise: Row {
        id: promise
        property string icon
        property string head
        property string body
        width: parent.width
        spacing: 14
        Rectangle {
            width: 36; height: 36; radius: 12
            color: Theme.tinted(Theme.calm, 0.10)
            border.color: Theme.tinted(Theme.calm, 0.18)
            Icon { anchors.centerIn: parent; name: promise.icon; size: 18; color: Theme.calm }
        }
        Column {
            width: parent.width - 50
            spacing: 2
            Text { width: parent.width; text: promise.head; color: Theme.text; font.pixelSize: Theme.body
                   font.weight: Font.Medium; textFormat: Text.PlainText; wrapMode: Text.Wrap }
            Text { width: parent.width; text: promise.body; color: Theme.soft; font.pixelSize: 13
                   lineHeight: 1.45; textFormat: Text.PlainText; wrapMode: Text.Wrap }
        }
    }
    component Check: Row {
        id: check
        property bool met
        property string words
        spacing: 8
        Rectangle {
            width: 16; height: 16; radius: 8
            anchors.verticalCenter: parent.verticalCenter
            color: check.met ? Theme.tinted(Theme.calm, 0.18) : "transparent"
            border.color: check.met ? Theme.tinted(Theme.calm, 0.5) : Theme.edge
            Icon { anchors.centerIn: parent; visible: check.met; name: "check"; size: 12; stroke: 2
                   color: Theme.calm }
        }
        Text { text: check.words; color: check.met ? Theme.text : Theme.soft; font.pixelSize: Theme.meta
               anchors.verticalCenter: parent.verticalCenter }
    }

    // ---- the night around it ----
    Item {
        anchors.horizontalCenter: parent.horizontalCenter
        y: -height * 0.35
        width: 900; height: 900
        Orb { anchors.fill: parent; size: 900; mood: meet.phase === "making" ? "thinking" : "rest"; breathing: false
              opacity: 0.16 }
    }

    Column {
        id: frame
        width: Math.min(460, parent.width - 64)
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.verticalCenter: parent.verticalCenter
        spacing: 28

        Row {                                    // where you are: four marks, the current one drawn long
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: 6
            Accessible.role: Accessible.ProgressBar
            Accessible.name: "第 " + (meet.shown + 1) + " 步，共 4 步"
            Repeater {
                model: 4
                Rectangle {
                    required property int index
                    width: index === meet.shown ? 22 : 6
                    height: 6
                    radius: 3
                    color: index === meet.shown ? Theme.calm
                           : (index < meet.shown ? Theme.tinted(Theme.calm, 0.45) : Theme.tinted(Theme.text, 0.16))
                    Behavior on width { NumberAnimation { duration: Theme.enter; easing.type: Easing.OutExpo } }
                }
            }
        }

        Orb {
            anchors.horizontalCenter: parent.horizontalCenter
            size: 120
            lit: true
            mood: meet.phase === "making" ? "thinking" : "rest"
        }

        Item {                                   // the step itself
            id: stage
            width: parent.width
            height: Math.max(hello.implicitHeight, naming.implicitHeight, secret.implicitHeight, making.implicitHeight,
                             done.implicitHeight)

            Step {                               // hello
                id: hello
                index: 0
                Title { text: "你好，我是 secdogie。" }
                Lead { text: "我会住在这台电脑上，替你做事。\n开始之前，先说说我会怎么做。" }
                Item { width: 1; height: 4 }
                Promise { icon: "shield"; head: "只在这台电脑上"; body: "钥匙、记录和你告诉我的事，都留在这里，不交给别人。" }
                Promise { icon: "key"; head: "你的签名才算数"; body: "删除、发送这类改不回来的事，都要你亲手签名。" }
                Promise { icon: "chat"; head: "拿不准就先问你"; body: "我会用一段话把顾虑说清楚，而不是丢给你一排按钮。" }
                Item { width: 1; height: 4 }
                Pill { width: parent.width; primary: true; text: "开始认识"; trailing: "arrow"; onClicked: meet.step = 1 }
            }

            Step {                               // a name
                id: naming
                index: 1
                Title { text: "我该怎么称呼你？" }
                Lead { text: "名字、外号都可以。只用来称呼你，不会发给任何人。" }
                Item { width: 1; height: 4 }
                Field { id: name; objectName: "name"; width: parent.width; label: "称呼"; hint: "比如：小王"; size: 16
                        onAccepted: meet.step = 2 }
                Item { width: 1; height: 4 }
                Pill { width: parent.width; primary: true; text: name.text.trim().length ? "就叫我" + name.text.trim()
                                                                                         : "下一步"
                       trailing: "arrow"; onClicked: meet.step = 2 }
                Row {
                    anchors.horizontalCenter: parent.horizontalCenter
                    spacing: 24
                    Link { text: "上一步"; onClicked: meet.step = 0 }
                    Link { text: "先不说"; onClicked: { name.text = ""; meet.step = 2 } }
                }
            }

            Step {                               // the passphrase
                id: secret
                index: 2
                Title { text: "设一个签名口令" }
                Lead { text: "改不回来的事，都要用它签名。\n它只存在这台电脑上，连我也不知道；忘了就找不回来。" }
                Field { id: pass1; objectName: "pass1"; width: parent.width; label: "签名口令"; hint: "至少 8 位"; secret: true; revealable: true
                        size: 16 }
                Row {                            // how strong it is: four marks and a word
                    width: parent.width
                    spacing: 6
                    Accessible.role: Accessible.StaticText
                    Accessible.name: "口令强度：" + (meet.strengthWords[meet.strength] || "还没输入")
                    Repeater {
                        model: 4
                        Rectangle {
                            required property int index
                            width: (secret.width - 3 * 6 - 64) / 4
                            height: 4
                            radius: 2
                            anchors.verticalCenter: parent.verticalCenter
                            color: index < meet.strength ? (meet.strength >= 3 ? Theme.calm : Theme.warm)
                                                         : Theme.tinted(Theme.text, 0.12)
                            Behavior on color { ColorAnimation { duration: Theme.touch } }
                        }
                    }
                    Text { width: 64; horizontalAlignment: Text.AlignRight; text: meet.strengthWords[meet.strength]
                           color: meet.strength >= 3 ? Theme.calm : Theme.warm; font.pixelSize: Theme.meta }
                }
                Field { id: pass2; objectName: "pass2"; width: parent.width; label: "再输一次"; hint: "和上面一样"; secret: true; size: 16
                        onAccepted: meet.submit() }
                Row {
                    spacing: 20
                    Check { met: meet.longEnough; words: "至少 8 位" }
                    Check { met: meet.same; words: "两次一致" }
                }
                Text { width: parent.width; visible: text.length > 0; text: P.feedback; color: Theme.warm
                       font.pixelSize: Theme.body; wrapMode: Text.Wrap; textFormat: Text.PlainText }
                Pill { width: parent.width; primary: true; text: "生成我的钥匙"; enabled: meet.ready
                       onClicked: meet.submit() }
                Link { anchors.horizontalCenter: parent.horizontalCenter; text: "上一步"; onClicked: meet.step = 1 }
            }

            Step {                               // the keys being made, then ready
                id: making
                index: 3
                visible: opacity > 0.01 && meet.phase === "making"
                Title { text: "正在生成你的钥匙……" }
                Lead { text: "用你的口令把签名钥匙锁起来，要花几秒钟。" }
            }
            Step {
                id: done
                index: 3
                visible: opacity > 0.01 && meet.phase === "ready"
                Title { text: P.you ? "好了，" + P.you + "。" : "好了。" }
                Lead { text: "三把钥匙都在这台电脑上了。点一下就能复制。" }
                Panel {
                    width: parent.width
                    spacing: 2
                    Fact { width: parent.width; labelWidth: 120; label: "我自己的钥匙"; value: (P.keys || {}).node || "" }
                    Fact { width: parent.width; labelWidth: 120; label: "你和我说话用的"; value: (P.keys || {}).app || "" }
                    Fact { width: parent.width; labelWidth: 120; label: "你的签名钥匙"; value: (P.keys || {}).operator || "" }
                }
                Pill { width: parent.width; primary: true; text: "开始吧"; trailing: "arrow"
                       onClicked: P.finishOnboarding() }
            }
        }
    }
}
