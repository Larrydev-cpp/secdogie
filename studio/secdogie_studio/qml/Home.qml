// Studio's home window: where you and the companion talk, and where you see
// this node and its mesh.
//
// Opening Studio opens it. Closing it leaves the companion as a light in the
// corner of the screen (Presence.qml); clicking the light brings this window
// back. While it is open, what waits on you (one question, one signature, one
// memory) shows here, above the line you type into, one at a time. Before
// there is a node at all, the whole window is the first hello.
//
// The design system is in studio/DESIGN.md; all state comes from the bridge
// `P` (secdogie_studio/app.py).
import QtQuick
import QtQuick.Window
import QtQuick.Controls
import "."

Window {
    id: home
    title: "secdogie"
    width: 980
    height: 680
    minimumWidth: 760
    minimumHeight: 540
    color: Theme.ground
    visible: P.homeOpen
    onClosing: (close) => { close.accepted = false; P.closeHome() }   // closing leaves the light

    property int page: 0                                   // 0: the conversation, 1: the node and its mesh
    readonly property bool first: !!P.homeCard && P.homeCard.kind === "welcome"
    readonly property int waitingCount: P.homeCard ? 1 + P.waiting : 0
    readonly property var node: P.node || ({})
    property bool editingRules: false

    function moodWords(mood) {
        switch (mood) {
        case "thinking": return "正在做事"
        case "waiting": return "有事等你"
        case "signing": return "等你签名"
        case "speaking": return "想问你一件事"
        default: return node.stateWords || "醒着"
        }
    }
    function stateColor(state) {
        switch (state) {
        case "running": return Theme.calm
        case "starting": return Theme.think
        case "exited": return Theme.warm
        default: return Theme.faint
        }
    }
    function send() {
        if (say.text.trim().length === 0) return
        P.say(say.text)
        say.text = ""
    }

    // ---- the conversation, appended as it grows (the list never jumps) -------------------------------
    // Each line has a stable key. What is new is appended after the last line
    // already shown; only if what is shown no longer matches (a question that
    // was waiting found its place) is the list rebuilt.
    ListModel { id: convo }
    function heldMatches(lines, at) {
        for (let k = 0; k < convo.count && at - k >= 0; k++)
            if (convo.get(convo.count - 1 - k).key !== lines[at - k].key) return false
        return true
    }
    function syncConversation() {
        const lines = P.conversation || []
        let at = -1
        if (convo.count > 0) {
            const last = convo.get(convo.count - 1).key
            at = lines.findIndex(l => l.key === last)
            if (at < 0 || !heldMatches(lines, at)) { convo.clear(); at = -1 }
        }
        for (let i = at + 1; i < lines.length; i++)
            convo.append({ key: lines[i].key, who: lines[i].who, words: lines[i].text, tone: lines[i].tone })
    }
    Connections { target: P; function onChanged() { home.syncConversation() } }
    Component.onCompleted: { Theme.still = P.still; syncConversation() }

    Shortcut { sequence: "Ctrl+1"; onActivated: home.page = 0 }
    Shortcut { sequence: "Ctrl+2"; onActivated: home.page = 1 }
    Shortcut {
        sequences: [StandardKey.Cancel]
        enabled: home.editingRules
        onActivated: home.editingRules = false
    }

    // ---- parts only this window uses ------------------------------------------------------------------
    component NavItem: AbstractButton {
        id: item
        property bool current: false
        property int badge: 0
        hoverEnabled: true
        implicitHeight: 40
        leftPadding: 12
        rightPadding: 12
        font.pixelSize: Theme.body
        Accessible.role: Accessible.PageTab
        Accessible.selected: item.current
        Accessible.description: item.badge > 0 ? item.badge + " 件事在等你" : ""
        Keys.onReturnPressed: item.animateClick()
        Keys.onEnterPressed: item.animateClick()
        HoverHandler { cursorShape: Qt.PointingHandCursor }
        contentItem: Item {
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: item.text
                textFormat: Text.PlainText
                font.pixelSize: item.font.pixelSize
                font.weight: item.current ? Font.Medium : Font.Normal
                color: item.current || item.hovered ? Theme.text : Theme.soft
            }
            Rectangle {                                   // how many things wait on this page
                visible: item.badge > 0
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                height: 20
                width: Math.max(20, count.implicitWidth + 12)
                radius: 10
                color: Theme.tinted(Theme.warm, 0.18)
                Text {
                    id: count
                    anchors.centerIn: parent
                    text: item.badge
                    color: Theme.warm
                    font.pixelSize: Theme.meta
                    font.features: ({ "tnum": 1 })
                }
            }
        }
        background: Rectangle {
            radius: 10
            color: item.current || item.down ? Theme.press : (item.hovered ? Theme.wash : "transparent")
            Behavior on color { ColorAnimation { duration: Theme.touch } }
            Ring { visible: item.visualFocus; round: 10 }
        }
    }

    component Heading: Text {
        textFormat: Text.PlainText
        color: Theme.text
        font.pixelSize: Theme.lead
        font.weight: Font.Medium
    }

    // ---- the first hello: before there is a node, the window is one card -------------------------------
    Item {
        anchors.fill: parent
        visible: home.first
        Column {
            anchors.centerIn: parent
            width: 460
            spacing: 32
            Orb { anchors.horizontalCenter: parent.horizontalCenter; size: 96; mood: P.mood; lit: true }
            Sheet {
                width: parent.width
                height: hello.implicitHeight + 48
                color: Theme.panel
                MomentCard { id: hello; x: 24; y: 24; width: parent.width - 48; c: home.first ? P.homeCard : null }
            }
        }
    }

    // ---- the rail: who it is, and where you are --------------------------------------------------------
    Item {
        id: rail
        visible: !home.first
        width: 216
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Theme.hairline }
        Row {
            id: who
            x: 20; y: 24
            spacing: 12
            Orb { size: 48; mood: P.mood; anchors.verticalCenter: parent.verticalCenter }
            Column {
                anchors.verticalCenter: parent.verticalCenter
                spacing: 2
                Text { text: "secdogie"; color: Theme.text; font.pixelSize: Theme.body; font.weight: Font.Medium }
                Text { text: home.moodWords(P.mood); textFormat: Text.PlainText; color: Theme.soft
                       font.pixelSize: Theme.meta }
            }
        }
        Column {
            anchors.top: who.bottom
            anchors.topMargin: 28
            x: 12
            width: parent.width - 24
            spacing: 4
            NavItem { width: parent.width; text: "对话"; current: home.page === 0
                      badge: home.page !== 0 ? home.waitingCount : 0; onClicked: home.page = 0 }
            NavItem { width: parent.width; text: "节点与网格"; current: home.page === 1; onClicked: home.page = 1 }
        }
        Column {
            x: 20
            width: parent.width - 40
            anchors.bottom: parent.bottom
            anchors.bottomMargin: 16
            spacing: 4
            Text { width: parent.width; text: "关掉窗口，我就在屏幕角落。"; wrapMode: Text.Wrap
                   color: Theme.faint; font.pixelSize: Theme.meta; lineHeight: 1.4 }
            Link { text: "让我休息"; size: Theme.meta; onClicked: P.rest() }
        }
    }

    Item {
        id: content
        visible: !home.first
        anchors.left: rail.right
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: parent.bottom

        // ---- the conversation ------------------------------------------------------------------------
        Item {
            id: talk
            anchors.fill: parent
            visible: home.page === 0
            readonly property real measure: Math.min(width - 64, 720)     // a readable column
            readonly property real inset: (width - measure) / 2

            Text { id: talkTitle; x: talk.inset; y: 24; text: "对话"; color: Theme.text
                   font.pixelSize: Theme.title; font.weight: Font.Medium }
            ListView {
                id: chat
                x: talk.inset
                width: talk.measure
                anchors.top: talkTitle.bottom
                anchors.topMargin: 16
                anchors.bottom: dock.top
                anchors.bottomMargin: 16
                clip: true
                spacing: 12
                model: convo
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: Bar {}
                topMargin: Math.max(0, height - contentHeight)   // a short conversation sits just above the input
                property bool stick: true                  // keep the newest line in view, unless you scrolled up
                onMovementEnded: stick = atYEnd
                onCountChanged: if (stick) Qt.callLater(() => chat.positionViewAtEnd())
                onHeightChanged: if (stick) Qt.callLater(() => chat.positionViewAtEnd())
                delegate: Item {
                    id: msg
                    required property string who
                    required property string words
                    required property string tone
                    width: chat.width - 12                 // the scrollbar's lane
                    height: who === "note" ? note.implicitHeight + 4 : bubble.height
                    Text {
                        id: note
                        visible: msg.who === "note"
                        width: parent.width
                        horizontalAlignment: Text.AlignHCenter
                        text: msg.words
                        textFormat: Text.PlainText
                        wrapMode: Text.Wrap
                        color: Theme.faint
                        font.pixelSize: Theme.meta
                    }
                    Rectangle {
                        id: bubble
                        visible: msg.who !== "note"
                        readonly property bool mine: msg.who === "you"
                        // a letter (a refusal at length) or a question with its reasons: a heading, then paragraphs
                        readonly property var parts: msg.words.split("\n\n")
                        readonly property bool essay: !mine && parts.length > 1
                        x: mine ? msg.width - width : 0
                        width: essay ? msg.width * 0.9
                                    : Math.min(head.implicitWidth + 32, msg.width * 0.78)
                        height: column.implicitHeight + (essay ? 32 : 20)
                        radius: 16
                        color: mine ? Theme.tinted(Theme.calm, 0.16) : Theme.panel
                        border.color: mine ? Theme.tinted(Theme.calm, 0.30) : Theme.hairline
                        Accessible.role: Accessible.StaticText
                        Accessible.name: (mine ? "你：" : "它：") + msg.words
                        Column {
                            id: column
                            x: bubble.essay ? 20 : 16
                            y: bubble.essay ? 16 : 10
                            width: bubble.width - 2 * x
                            spacing: 10
                            Text {
                                id: head
                                width: parent.width
                                text: bubble.essay ? bubble.parts[0] : msg.words
                                textFormat: Text.PlainText
                                wrapMode: Text.Wrap
                                color: msg.tone === "warm" || (bubble.essay && msg.tone === "letter") ? Theme.warm
                                                                                                     : Theme.text
                                font.pixelSize: bubble.essay ? Theme.lead : Theme.body
                                font.weight: bubble.essay ? Font.Medium : Font.Normal
                                lineHeight: 1.45
                            }
                            Repeater {
                                model: bubble.essay ? bubble.parts.slice(1) : []
                                Text {
                                    required property string modelData
                                    width: column.width
                                    text: modelData
                                    textFormat: Text.PlainText
                                    wrapMode: Text.Wrap
                                    color: Theme.soft
                                    font.pixelSize: Theme.body
                                    lineHeight: 1.6
                                }
                            }
                        }
                    }
                }
            }
            Column {                                       // before the first word
                visible: convo.count === 0
                anchors.centerIn: chat
                width: Math.min(chat.width, 380)
                spacing: 8
                Text { width: parent.width; horizontalAlignment: Text.AlignHCenter; text: "我们还没说过话。"
                       color: Theme.text; font.pixelSize: Theme.lead }
                Text { width: parent.width; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
                       text: "告诉我你想做什么。拿不准的地方我会先问你；\n改不回来的事，一定等你签名。"
                       color: Theme.soft; font.pixelSize: Theme.body; lineHeight: 1.5 }
            }
            Column {
                id: dock
                x: talk.inset
                width: talk.measure
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 24
                spacing: 16
                Sheet {                                    // what waits on you, one at a time
                    id: waiting
                    visible: !!P.homeCard && !home.first
                    width: parent.width
                    height: waitingCard.implicitHeight + 48 + (more.visible ? more.implicitHeight + 16 : 0)
                    color: Theme.panel
                    readonly property string key: P.homeCardKey
                    onKeyChanged: { waitingCard.reset(); if (key !== "") appear.restart() }
                    NumberAnimation { id: appear; target: waiting; property: "opacity"; from: 0; to: 1
                                      duration: Theme.enter; easing.type: Easing.OutExpo }
                    MomentCard { id: waitingCard; x: 24; y: 24; width: parent.width - 48
                                 c: home.first ? null : P.homeCard }
                    Text { id: more; visible: P.waiting > 0; x: 24; anchors.bottom: parent.bottom
                           anchors.bottomMargin: 20; text: "答完这件，还有 " + P.waiting + " 件在等你。"
                           color: Theme.faint; font.pixelSize: Theme.meta }
                }
                Row {
                    width: parent.width
                    spacing: 12
                    Field { id: say; focus: true; width: parent.width - hand.width - 12
                            hint: "比如：帮我整理这周的周报"; onAccepted: home.send() }
                    Pill { id: hand; text: "交给我"; primary: true; enabled: say.text.trim().length > 0
                           onClicked: home.send() }
                }
            }
        }

        // ---- this node, and its mesh -----------------------------------------------------------------
        Flickable {
            id: nodePage
            objectName: "nodePage"
            anchors.fill: parent
            visible: home.page === 1
            contentWidth: width
            contentHeight: nodeBody.implicitHeight + 56
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: Bar {}
            Column {
                id: nodeBody
                readonly property real measure: Math.min(nodePage.width - 64, 720)
                x: (nodePage.width - measure) / 2
                y: 24
                width: measure
                spacing: 36

                Text { text: "节点与网格"; color: Theme.text; font.pixelSize: Theme.title; font.weight: Font.Medium }

                Column {                                   // this node
                    width: parent.width
                    spacing: 12
                    Row {
                        spacing: 10
                        Rectangle { width: 10; height: 10; radius: 5; anchors.verticalCenter: parent.verticalCenter
                                    color: home.stateColor(home.node.state || "") }
                        Heading { id: stateHead; text: home.node.stateWords || "" }
                        Text { anchors.baseline: stateHead.baseline; text: home.node.since || ""
                               textFormat: Text.PlainText; color: Theme.soft; font.pixelSize: Theme.body }
                    }
                    Column {
                        width: parent.width
                        Fact { width: parent.width; label: "这台节点"; value: home.node.did || "" }
                        Fact { width: parent.width; label: "角色"; value: home.node.roles || "" }
                        Fact { width: parent.width; label: "监听"; value: home.node.listen || "" }
                        Fact { width: parent.width; label: "连接方式"; value: home.node.carriers || "" }
                        Fact { width: parent.width; label: "和 Studio"
                               value: home.node.appConnected ? "已连上" : (home.node.answered ? "没连上" : "正在问它……")
                               tone: home.node.appConnected ? Theme.text : Theme.soft }
                    }
                    Text { visible: !!home.node.halted; width: parent.width; wrapMode: Text.Wrap; color: Theme.warm
                           font.pixelSize: Theme.body; text: "它被叫停了：不会再开始新的事，直到你重启它。" }
                }

                Column {                                   // its work
                    width: parent.width
                    spacing: 12
                    Heading { text: "在做的事" }
                    Text { width: parent.width; text: home.node.work || ""; textFormat: Text.PlainText
                           color: Theme.soft; font.pixelSize: Theme.body; wrapMode: Text.Wrap }
                    Column {
                        width: parent.width
                        Repeater {
                            model: home.node.recent || []
                            Row {
                                id: goal
                                required property var modelData
                                width: parent.width
                                height: 32
                                spacing: 10
                                Rectangle {
                                    width: 6; height: 6; radius: 3; anchors.verticalCenter: parent.verticalCenter
                                    color: goal.modelData.status === "failed" ? Theme.warm
                                           : (goal.modelData.status === "active" ? Theme.think
                                           : (goal.modelData.status === "done" ? Theme.calm : Theme.faint))
                                }
                                Text { width: parent.width - 16 - goalWords.width - 10; height: parent.height
                                       verticalAlignment: Text.AlignVCenter; text: goal.modelData.title
                                       textFormat: Text.PlainText; elide: Text.ElideRight; color: Theme.text
                                       font.pixelSize: Theme.body }
                                Text { id: goalWords; height: parent.height; verticalAlignment: Text.AlignVCenter
                                       text: goal.modelData.words; color: Theme.soft; font.pixelSize: Theme.meta }
                            }
                        }
                    }
                }

                Column {                                   // the mesh
                    width: parent.width
                    spacing: 12
                    Heading { text: "网格「" + (home.node.mesh || "home") + "」" }
                    Column {
                        width: parent.width
                        Fact { width: parent.width; label: "你的签名钥匙"; value: home.node.operator || "" }
                        Fact { width: parent.width; label: "你的 App"; value: home.node.app || "" }
                    }
                    Rectangle {                            // the other nodes: none yet, and why
                        width: parent.width
                        height: others.implicitHeight + 32
                        radius: 12
                        color: Theme.well
                        border.color: Theme.hairline
                        Column {
                            id: others
                            x: 16; y: 16
                            width: parent.width - 32
                            spacing: 6
                            Text { text: "还没有别的节点。"; color: Theme.text; font.pixelSize: Theme.body }
                            Text { width: parent.width; wrapMode: Text.Wrap; lineHeight: 1.45
                                   color: Theme.soft; font.pixelSize: Theme.meta
                                   text: "另一台电脑上的节点要用你签发的邀请码加进来。邀请码、局域网发现和 WebRTC 连接还在做，做好后，它们会出现在这里。" }
                        }
                    }
                }

                Column {                                   // the rules it keeps
                    width: parent.width
                    spacing: 12
                    Heading { text: "门的规则" }
                    Text { visible: (home.node.rules || []).length === 0; width: parent.width; wrapMode: Text.Wrap
                           text: "还没有注入过规则，用的是内置的门。"; color: Theme.soft; font.pixelSize: Theme.body }
                    Repeater {
                        model: home.node.rules || []
                        Row {
                            id: rule
                            required property var modelData
                            height: 28
                            spacing: 10
                            Rectangle { width: 6; height: 6; radius: 3; anchors.verticalCenter: parent.verticalCenter
                                        color: rule.modelData.applied ? Theme.calm : Theme.faint }
                            Text { height: parent.height; verticalAlignment: Text.AlignVCenter
                                   text: rule.modelData.name; color: Theme.text; font.pixelSize: Theme.body }
                            Text { height: parent.height; verticalAlignment: Text.AlignVCenter
                                   text: rule.modelData.words; color: Theme.soft; font.pixelSize: Theme.meta
                                   font.features: ({ "tnum": 1 }) }
                        }
                    }
                    Link {
                        text: home.editingRules ? "收起" : "编辑规则"
                        onClicked: {
                            if (!home.editingRules) { P.openRules(); rulesEditor.load() }
                            home.editingRules = !home.editingRules
                        }
                    }
                    RulesEditor { id: rulesEditor; visible: home.editingRules; width: parent.width; showInForce: false
                                  editorHeight: 240 }
                }
            }
        }
    }
}
