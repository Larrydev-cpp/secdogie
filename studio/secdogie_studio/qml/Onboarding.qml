// The first meeting, in the home window: who it is, what it is made of (you,
// Studio, the node, your devices), what to call you, the passphrase that signs
// what cannot be undone, the keys being made, and the three keys once they
// are ready. In Chinese or English, as the person chooses at the top.
//
// One orchestrated moment: when the window first opens, the lamp lights up in
// the night, and then the greeting arrives. Everything after that answers the
// person's own clicks. It is still when the person asked for less motion.
//
// The steps before the keys (hello, a name, a passphrase) live here; the
// bridge says when the keys are being made ("making") and when they are
// ready ("ready"). Nothing here moves the keyboard focus.
import QtQuick
import QtQuick.Controls
import "."

Item {
    id: meet
    property int step: 0                         // 0 hello, 1 what it is, 2 a name, 3 the passphrase
    readonly property string phase: P.onboarding || ""
    readonly property int shown: phase === "making" || phase === "ready" ? 4 : step
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
    readonly property var strengthWords: Theme.lang === "en" ? ["", "too short", "fair", "good", "strong"]
                                                             : ["", "太短了", "还可以", "不错", "很好"]
    function submit() { if (ready) P.welcome(pass1.text, pass2.text, name.text) }

    // ---- the one moment: the lamp lights, then it speaks ----
    property real dawn: Theme.still ? 1 : 0      // 0 -> 1 as the lamp lights
    property real voice: Theme.still ? 1 : 0     // 0 -> 1 as the words arrive
    SequentialAnimation {
        running: !Theme.still && meet.visible
        NumberAnimation { target: meet; property: "dawn"; from: 0; to: 1; duration: 1600; easing.type: Easing.OutCubic }
        NumberAnimation { target: meet; property: "voice"; from: 0; to: 1; duration: 700; easing.type: Easing.OutCubic }
    }

    // ---- parts of a step ----
    component Step: Column {
        id: stepItem
        property int index
        width: stage.width
        readonly property bool here: meet.shown === index
        spacing: 18
        opacity: here ? meet.voice : 0
        visible: opacity > 0.01
        enabled: here
        Behavior on opacity {
            enabled: meet.voice >= 1
            NumberAnimation { duration: stepItem.here ? Theme.enter : Theme.exit }
        }
    }
    component Title: Text {
        width: parent.width
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
        color: Theme.text
        font.family: Theme.serif
        font.pixelSize: Theme.display
        font.weight: Font.DemiBold
        lineHeight: 1.2
    }
    component Lead: Text {
        width: parent.width
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
        color: Theme.soft
        font.family: Theme.serif
        font.pixelSize: Theme.voice
        lineHeight: 1.75
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
            Icon { anchors.centerIn: parent; visible: check.met; name: "check"; size: 12; stroke: 2; color: Theme.calm }
        }
        Text { text: check.words; color: check.met ? Theme.text : Theme.soft; font.pixelSize: Theme.meta
               anchors.verticalCenter: parent.verticalCenter }
    }

    // ---- the night around it: one large, faint lamp-glow ----
    Item {
        anchors.horizontalCenter: parent.horizontalCenter
        y: frame.y + lamp.y + lamp.height / 2 - height / 2
        width: 980; height: 980
        opacity: 0.13 * meet.dawn
        Orb { anchors.fill: parent; size: 980; mood: meet.phase === "making" ? "thinking" : "rest"; breathing: false
              sphere: false }
    }

    Column {
        id: frame
        width: Math.min(meet.shown === 1 ? 760 : 440, parent.width - 64)
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.verticalCenter: parent.verticalCenter
        spacing: 30

        Orb {
            id: lamp
            anchors.horizontalCenter: parent.horizontalCenter
            size: 132
            lit: true
            mood: meet.phase === "making" ? "thinking" : "rest"
            scale: 0.4 + 0.6 * meet.dawn
            opacity: meet.dawn
        }

        Item {                                   // the step itself
            id: stage
            width: parent.width
            // as tall as the step on show, so each step sits in the middle of the night
            height: (meet.shown === 0 ? hello : meet.shown === 1 ? intro : meet.shown === 2 ? naming
                     : meet.shown === 3 ? secret : meet.phase === "making" ? making : done).implicitHeight
            Behavior on height { NumberAnimation { duration: Theme.enter; easing.type: Easing.OutCubic } }

            Step {                               // hello: who it is, and what it promises
                id: hello
                index: 0
                Title { text: Theme.t("你好，我是 secdogie。", "Hello, I'm secdogie.") }
                Lead {
                    text: Theme.t("我会住在这台电脑上，替你做事。\n"
                                  + "你的钥匙和记录都留在这里；\n"
                                  + "改不回来的事，一定等你亲手签名；\n"
                                  + "拿不准的时候，我先问你。",
                                  "I'll live on this computer and do things for you.\n"
                                  + "Your keys and records stay here;\n"
                                  + "nothing irreversible happens without your signature;\n"
                                  + "when I'm unsure, I ask you first.")
                }
                Item { width: 1; height: 6 }
                Pill { anchors.horizontalCenter: parent.horizontalCenter; width: 260; primary: true
                       text: Theme.t("认识一下", "Nice to meet you"); onClicked: meet.step = 1 }
            }

            Step {                               // what it is made of
                id: intro
                index: 1
                Title { text: Theme.t("我由四部分组成", "Four parts make me") }
                Row {
                    width: parent.width
                    spacing: 12
                    Repeater {
                        model: [
                            ["壹", Theme.t("你", "You"), Theme.t("签名口令只在你手里，高风险的事都等你。", "The passphrase is yours alone; anything high-risk waits for you.")],
                            ["贰", "Studio", Theme.t("这个应用：和你对话，替你配对设备。", "This app: it talks with you and pairs your devices.")],
                            ["叁", Theme.t("节点", "Node"), Theme.t("在后台做事：签名日志、两道门。", "It does the work: a signed journal, two gates.")],
                            ["肆", Theme.t("网格", "Mesh"), Theme.t("只由你自己的设备组成，经 WebRTC 直连。", "Only your own devices, linked directly over WebRTC.")]
                        ]
                        Rectangle {
                            required property var modelData
                            width: (intro.width - 36) / 4
                            height: 170
                            radius: Theme.round
                            color: Theme.panel
                            border.color: Theme.hairline
                            Column {
                                x: 16; y: 16
                                width: parent.width - 32
                                spacing: 8
                                Text { text: modelData[0]; color: Theme.seal; font.family: Theme.serif; font.pixelSize: 24
                                       font.weight: Font.Bold }
                                Text { text: modelData[1]; color: Theme.text; font.pixelSize: Theme.body; font.weight: Font.DemiBold }
                                Text { width: parent.width; text: modelData[2]; color: Theme.soft; font.pixelSize: Theme.meta
                                       wrapMode: Text.Wrap; lineHeight: 1.4; textFormat: Text.PlainText }
                            }
                        }
                    }
                }
                Lead { text: Theme.t("没有中心服务器，没有陌生人的节点；你的规则从不离开你的设备。",
                                     "No central server, no strangers' nodes; your rules never leave your devices.") }
                Pill { anchors.horizontalCenter: parent.horizontalCenter; width: 260; primary: true
                       text: Theme.t("好，继续", "Go on"); onClicked: meet.step = 2 }
                Link { anchors.horizontalCenter: parent.horizontalCenter; text: Theme.t("上一步", "Back"); onClicked: meet.step = 0 }
            }

            Step {                               // a name
                id: naming
                index: 2
                Title { text: Theme.t("我该怎么称呼你？", "What should I call you?") }
                Lead { text: Theme.t("名字、外号都可以。只用来称呼你，不会发给任何人。",
                                     "A name or a nickname. It's only for me to call you by; it goes to no one.") }
                Item { width: 1; height: 2 }
                Field { id: name; objectName: "name"; width: parent.width; label: Theme.t("称呼", "Name")
                        hint: Theme.t("比如：小王", "e.g. Sam"); size: 16; onAccepted: meet.step = 3 }
                Item { width: 1; height: 2 }
                Pill { anchors.horizontalCenter: parent.horizontalCenter; width: 260; primary: true
                       text: name.text.trim().length ? Theme.t("就叫我", "Call me ") + name.text.trim() : Theme.t("下一步", "Next")
                       onClicked: meet.step = 3 }
                Row {
                    anchors.horizontalCenter: parent.horizontalCenter
                    spacing: 28
                    Link { text: Theme.t("上一步", "Back"); onClicked: meet.step = 1 }
                    Link { text: Theme.t("先不说", "Skip"); onClicked: { name.text = ""; meet.step = 3 } }
                }
            }

            Step {                               // the passphrase
                id: secret
                index: 3
                Title { text: Theme.t("设一个签名口令", "Choose a signing passphrase") }
                Lead { text: Theme.t("改不回来的事，都要用它签名。\n它只存在这台电脑上，连我也不知道；忘了就找不回来。",
                                     "Everything irreversible is signed with it.\nIt exists only on this computer, not even I know it; forget it and it's gone.") }
                Field { id: pass1; objectName: "pass1"; width: parent.width; label: Theme.t("签名口令", "Passphrase")
                        hint: Theme.t("至少 8 位", "8 characters or more")
                        secret: true; revealable: true; size: 16 }
                Row {                            // how strong it is: four marks and a word
                    width: parent.width
                    spacing: 6
                    Accessible.role: Accessible.StaticText
                    Accessible.name: Theme.t("口令强度：", "Strength: ") + (meet.strengthWords[meet.strength] || Theme.t("还没输入", "not typed yet"))
                    Repeater {
                        model: 4
                        Rectangle {
                            required property int index
                            width: (secret.width - 3 * 6 - 56) / 4
                            height: 3
                            radius: 1.5
                            anchors.verticalCenter: parent.verticalCenter
                            color: index < meet.strength ? (meet.strength >= 3 ? Theme.calm : Theme.warm)
                                                         : Theme.tinted(Theme.text, 0.12)
                            Behavior on color { ColorAnimation { duration: Theme.touch } }
                        }
                    }
                    Text { width: 56; horizontalAlignment: Text.AlignRight; text: meet.strengthWords[meet.strength]
                           color: meet.strength >= 3 ? Theme.calm : Theme.warm; font.pixelSize: Theme.meta }
                }
                Field { id: pass2; objectName: "pass2"; width: parent.width; label: Theme.t("再输一次", "Again")
                        hint: Theme.t("和上面一样", "The same as above")
                        secret: true; size: 16; onAccepted: meet.submit() }
                Row {
                    spacing: 22
                    Check { met: meet.longEnough; words: Theme.t("至少 8 位", "8 or more") }
                    Check { met: meet.same; words: Theme.t("两次一致", "Both the same") }
                }
                Text { width: parent.width; visible: text.length > 0; text: P.feedback; color: Theme.warm
                       font.pixelSize: Theme.body; wrapMode: Text.Wrap; textFormat: Text.PlainText }
                Pill { anchors.horizontalCenter: parent.horizontalCenter; width: 260; primary: true; text: Theme.t("生成我的钥匙", "Make my keys")
                       enabled: meet.ready; onClicked: meet.submit() }
                Link { anchors.horizontalCenter: parent.horizontalCenter; text: Theme.t("上一步", "Back"); onClicked: meet.step = 2 }
            }

            Step {                               // the keys being made
                id: making
                index: 4
                visible: opacity > 0.01 && meet.phase === "making"
                Title { text: Theme.t("正在生成你的钥匙……", "Making your keys…") }
                Lead { text: Theme.t("用你的口令把签名钥匙锁起来，要花几秒钟。", "Locking the signing key with your passphrase takes a few seconds.") }
            }
            Step {                               // and ready
                id: done
                index: 4
                visible: opacity > 0.01 && meet.phase === "ready"
                Title { text: P.you ? Theme.t("好了，", "Done, ") + P.you + Theme.t("。", ".") : Theme.t("好了。", "Done.") }
                Lead { text: Theme.t("三把钥匙都在这台电脑上了，点一下就能复制。", "All three keys are on this computer; click one to copy it.") }
                Column {
                    width: parent.width
                    Fact { width: parent.width; labelWidth: 120; label: Theme.t("我自己的钥匙", "My own key"); value: (P.keys || {}).node || "" }
                    Fact { width: parent.width; labelWidth: 120; label: Theme.t("你和我说话用的", "For talking to me"); value: (P.keys || {}).app || "" }
                    Fact { width: parent.width; labelWidth: 120; label: Theme.t("你的签名钥匙", "Your signing key"); value: (P.keys || {}).operator || "" }
                }
                Item { width: 1; height: 2 }
                Pill { anchors.horizontalCenter: parent.horizontalCenter; width: 260; primary: true; text: Theme.t("开始吧", "Let's begin")
                       onClicked: P.finishOnboarding() }
            }
        }

        Row {                                    // where you are in four steps; the current one drawn long
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: 6
            opacity: meet.voice
            Accessible.role: Accessible.ProgressBar
            Accessible.name: Theme.t("第 " + (meet.shown + 1) + " 步，共 5 步", "Step " + (meet.shown + 1) + " of 5")
            Repeater {
                model: 5
                Rectangle {
                    required property int index
                    width: index === meet.shown ? 20 : 6
                    height: 6
                    radius: 3
                    color: index === meet.shown ? Theme.lamp
                           : (index < meet.shown ? Theme.tinted(Theme.lamp, 0.45) : Theme.tinted(Theme.text, 0.16))
                    Behavior on width { NumberAnimation { duration: Theme.enter; easing.type: Easing.OutExpo } }
                }
            }
        }
    }
}
