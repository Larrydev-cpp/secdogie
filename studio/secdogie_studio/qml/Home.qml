// Studio's home window: where you and the companion talk, and where you see
// this node and its mesh.
//
// Opening Studio opens it. Closing it leaves the companion as a light in the
// corner of the screen (Presence.qml); clicking the light brings this window
// back. While it is open, what waits on you (one question, one signature, one
// memory) shows here, above the line you type into, one at a time. Before
// there is a profile, the whole window is the first meeting (Onboarding.qml).
//
// The design system is in studio/DESIGN.md; all state comes from the bridge
// `P` (secdogie_studio/app.py).
import QtQuick
import QtQuick.Window
import QtQuick.Controls
import QtQuick.Shapes
import "."

Window {
    id: home
    title: "secdogie"
    width: 1040
    height: 720
    minimumWidth: 820
    minimumHeight: 580
    color: Theme.ground
    visible: P.homeOpen
    onClosing: (close) => { close.accepted = false; P.closeHome() }   // closing leaves the light

    property int page: 0                                   // 0: the conversation, 1: the node and its mesh
    readonly property bool first: (P.onboarding || "") !== ""
    readonly property int waitingCount: P.homeCard ? 1 + P.waiting : 0
    readonly property var node: P.node || ({})
    readonly property string you: P.you || ""
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
    function goalTint(status) {
        switch (status) {
        case "active": return Theme.think
        case "done": return Theme.calm
        case "failed": return Theme.warm
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
        property string symbol
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
            Row {
                anchors.verticalCenter: parent.verticalCenter
                spacing: 12
                Icon { name: item.symbol; size: 18; anchors.verticalCenter: parent.verticalCenter
                       color: item.current ? Theme.calm : (item.hovered ? Theme.text : Theme.soft) }
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: item.text
                    textFormat: Text.PlainText
                    font.pixelSize: item.font.pixelSize
                    font.weight: item.current ? Font.Medium : Font.Normal
                    color: item.current || item.hovered ? Theme.text : Theme.soft
                }
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
            color: item.current ? Theme.panelHi : (item.down ? Theme.press : (item.hovered ? Theme.wash : "transparent"))
            border.color: item.current ? Theme.hairline : "transparent"
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

    component PageTitle: Column {
        id: titled
        property string title
        property string sub
        spacing: 4
        Text { text: titled.title; color: Theme.text; font.pixelSize: Theme.title; font.weight: Font.Medium
               textFormat: Text.PlainText }
        Text { visible: titled.sub !== ""; text: titled.sub; color: Theme.soft; font.pixelSize: Theme.body
               textFormat: Text.PlainText }
    }

    component Page: Item {
        id: pageItem
        property int index
        readonly property bool here: home.page === index
        opacity: here ? 1 : 0
        visible: opacity > 0.01
        enabled: here
        Behavior on opacity { NumberAnimation { duration: pageItem.here ? Theme.enter : Theme.exit } }
        transform: Translate { y: (1 - pageItem.opacity) * (Theme.still ? 0 : 8) }
    }

    // ---- the first meeting: before there is a profile, the window is the meeting ----------------------
    Onboarding { objectName: "onboarding"; anchors.fill: parent; visible: home.first }

    // ---- the night around the work: a faint light that follows the companion's mood -------------------
    Item {
        visible: !home.first
        x: home.width - 460
        y: -420
        width: 820
        height: 820
        Orb { anchors.fill: parent; size: 820; mood: P.mood; breathing: false; opacity: 0.07 }
    }

    // ---- the rail: who it is, where you are, and who you are --------------------------------------------
    Rectangle {
        id: rail
        visible: !home.first
        width: 236
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        color: Theme.sunk
        Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Theme.hairline }

        Row {
            id: brand
            x: 20; y: 22
            spacing: 12
            Orb { size: 40; mood: P.mood; anchors.verticalCenter: parent.verticalCenter }
            Column {
                anchors.verticalCenter: parent.verticalCenter
                spacing: 3
                Text { text: "secdogie"; color: Theme.text; font.pixelSize: 15; font.weight: Font.DemiBold
                       font.letterSpacing: 0.3 }
                Row {
                    spacing: 6
                    Rectangle { width: 6; height: 6; radius: 3; anchors.verticalCenter: parent.verticalCenter
                                color: home.stateColor(home.node.state || "") }
                    Text { text: home.moodWords(P.mood); textFormat: Text.PlainText; color: Theme.soft
                           font.pixelSize: Theme.meta }
                }
            }
        }
        Column {
            anchors.top: brand.bottom
            anchors.topMargin: 30
            x: 12
            width: parent.width - 24
            spacing: 4
            NavItem { width: parent.width; text: "对话"; symbol: "chat"; current: home.page === 0
                      badge: home.page !== 0 ? home.waitingCount : 0; onClicked: home.page = 0 }
            NavItem { width: parent.width; text: "节点与网格"; symbol: "mesh"; current: home.page === 1
                      onClicked: home.page = 1 }
        }

        Column {                                           // you, at the foot of the rail
            x: 16
            width: parent.width - 32
            anchors.bottom: parent.bottom
            anchors.bottomMargin: 16
            spacing: 12
            Rectangle { width: parent.width; height: 1; color: Theme.hairline }
            Row {
                spacing: 12
                Rectangle {
                    width: 34; height: 34; radius: 17
                    color: Theme.tinted(Theme.calm, 0.14)
                    border.color: Theme.tinted(Theme.calm, 0.3)
                    Text { anchors.centerIn: parent; text: home.you ? home.you.charAt(0) : "你"
                           color: Qt.lighter(Theme.calm, 1.15); font.pixelSize: Theme.body; font.weight: Font.Medium }
                }
                Column {
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 2
                    Text { text: home.you || "你"; color: Theme.text; font.pixelSize: Theme.body
                           textFormat: Text.PlainText; elide: Text.ElideRight; width: 150 }
                    Text { text: "签名钥匙在这台电脑上"; color: Theme.faint; font.pixelSize: Theme.meta }
                }
            }
            Column {
                spacing: 0
                Row {
                    spacing: 6
                    Icon { name: "power"; size: 14; color: Theme.soft; anchors.verticalCenter: parent.verticalCenter }
                    Link { text: "让我休息"; size: Theme.meta; onClicked: P.rest() }
                }
                Text { text: "关掉窗口，我就在屏幕角落。"; color: Theme.faint; font.pixelSize: Theme.meta }
            }
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
        Page {
            id: talk
            index: 0
            anchors.fill: parent
            readonly property real measure: Math.min(width - 72, 740)     // a readable column
            readonly property real inset: (width - measure) / 2

            PageTitle {
                id: talkTitle
                x: talk.inset; y: 26
                title: "对话"
                sub: P.greeting ? P.greeting + (home.you ? "，" + home.you : "") + "。" : ""
            }
            ListView {
                id: chat
                x: talk.inset
                width: talk.measure
                anchors.top: talkTitle.bottom
                anchors.topMargin: 18
                anchors.bottom: dock.top
                anchors.bottomMargin: 18
                clip: true
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
                    required property int index
                    required property string who
                    required property string words
                    required property string tone
                    readonly property string before: index > 0 ? convo.get(index - 1).who : ""
                    readonly property bool opens: who !== before          // the first line of a run
                    readonly property real gap: index === 0 ? 0 : (opens ? 18 : 6)
                    readonly property bool mine: who === "you"
                    width: chat.width - 12                 // the scrollbar's lane
                    height: gap + (who === "note" ? noteChip.height : bubble.height)

                    Rectangle {                            // a quiet note, centred
                        id: noteChip
                        visible: msg.who === "note"
                        y: msg.gap
                        anchors.horizontalCenter: parent.horizontalCenter
                        width: Math.min(noteText.implicitWidth + 24, msg.width * 0.8)
                        height: noteText.implicitHeight + 10
                        radius: height / 2
                        color: Theme.wash
                        Text {
                            id: noteText
                            anchors.centerIn: parent
                            width: Math.min(implicitWidth, msg.width * 0.8 - 24)
                            horizontalAlignment: Text.AlignHCenter
                            text: msg.words
                            textFormat: Text.PlainText
                            wrapMode: Text.Wrap
                            color: Theme.faint
                            font.pixelSize: Theme.meta
                        }
                    }
                    Orb {                                  // the companion's mark, at the start of what it says
                        visible: msg.who === "it" && msg.opens
                        y: msg.gap - 2
                        size: 26
                        breathing: false
                        lit: true
                    }
                    Rectangle {
                        id: bubble
                        visible: msg.who !== "note"
                        y: msg.gap
                        // a letter (a refusal at length) or a question with its reasons: a heading, then paragraphs
                        readonly property var parts: msg.words.split("\n\n")
                        readonly property bool essay: !msg.mine && parts.length > 1
                        readonly property real room: msg.width - 36
                        x: msg.mine ? msg.width - width : 36
                        width: essay ? room * 0.94 : Math.min(head.implicitWidth + 32, room * 0.8)
                        height: column.implicitHeight + (essay ? 34 : 22)
                        radius: 16
                        topLeftRadius: !msg.mine && msg.opens ? 6 : 16
                        topRightRadius: msg.mine && msg.opens ? 6 : 16
                        color: msg.mine ? Theme.tinted(Theme.calm, 0.15) : Theme.panel
                        border.color: msg.mine ? Theme.tinted(Theme.calm, 0.28) : Theme.hairline
                        Accessible.role: Accessible.StaticText
                        Accessible.name: (msg.mine ? "你：" : "它：") + msg.words
                        Column {
                            id: column
                            x: bubble.essay ? 20 : 16
                            y: bubble.essay ? 17 : 11
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
                width: Math.min(chat.width, 460)
                spacing: 14
                Orb { anchors.horizontalCenter: parent.horizontalCenter; size: 84; mood: P.mood }
                Text { width: parent.width; horizontalAlignment: Text.AlignHCenter
                       text: home.you ? home.you + "，想让我做点什么？" : "想让我做点什么？"
                       color: Theme.text; font.pixelSize: Theme.title; font.weight: Font.Medium }
                Text { width: parent.width; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
                       text: "拿不准的地方我会先问你；改不回来的事，一定等你签名。"
                       color: Theme.soft; font.pixelSize: Theme.body; lineHeight: 1.5 }
                Item { width: 1; height: 4 }
                Flow {
                    width: parent.width
                    spacing: 8
                    layoutDirection: Qt.LeftToRight
                    Repeater {
                        model: ["整理一下下载文件夹", "写这周的周报", "看看磁盘还剩多少空间"]
                        Pill { required property string modelData; text: modelData; focusPolicy: Qt.TabFocus
                               onClicked: say.text = modelData }
                    }
                }
            }
            Column {
                id: dock
                x: talk.inset
                width: talk.measure
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 20
                spacing: 12
                Rectangle {                                // what waits on you, one at a time
                    id: waiting
                    visible: !!P.homeCard && !home.first
                    width: parent.width
                    height: waitingCard.implicitHeight + 48 + (more.visible ? more.implicitHeight + 16 : 0)
                    radius: 18
                    color: Theme.panel
                    border.color: Theme.hairline
                    readonly property string key: P.homeCardKey
                    readonly property color tint: waitingCard.tint
                    onKeyChanged: { waitingCard.reset(); if (key !== "") appear.restart() }
                    NumberAnimation { id: appear; target: waiting; property: "opacity"; from: 0; to: 1
                                      duration: Theme.enter; easing.type: Easing.OutExpo }
                    Rectangle {                            // its kind, as a line of light along the top edge
                        x: 24; width: parent.width - 48; height: 1
                        gradient: Gradient {
                            orientation: Gradient.Horizontal
                            GradientStop { position: 0.0; color: Theme.tinted(waiting.tint, 0) }
                            GradientStop { position: 0.5; color: Theme.tinted(waiting.tint, 0.8) }
                            GradientStop { position: 1.0; color: Theme.tinted(waiting.tint, 0) }
                        }
                    }
                    MomentCard { id: waitingCard; x: 24; y: 24; width: parent.width - 48
                                 c: home.first ? null : P.homeCard }
                    Text { id: more; visible: P.waiting > 0; x: 24; anchors.bottom: parent.bottom
                           anchors.bottomMargin: 20; text: "答完这件，还有 " + P.waiting + " 件在等你。"
                           color: Theme.faint; font.pixelSize: Theme.meta }
                }
                Rectangle {                                // the composer
                    width: parent.width
                    height: 56
                    radius: 18
                    color: Theme.panel
                    border.color: say.focused ? Theme.tinted(Theme.calm, 0.5) : Theme.hairline
                    Behavior on border.color { ColorAnimation { duration: Theme.touch } }
                    Field {
                        id: say
                        focus: true
                        bare: true
                        x: 20
                        width: parent.width - 20 - hand.width - 20
                        anchors.verticalCenter: parent.verticalCenter
                        hint: "告诉我你想做什么……"
                        size: 15
                        onAccepted: home.send()
                    }
                    IconButton {
                        id: hand
                        anchors.right: parent.right
                        anchors.rightMargin: 9
                        anchors.verticalCenter: parent.verticalCenter
                        diameter: 38
                        symbol: "send"
                        primary: true
                        text: "交给我"
                        enabled: say.text.trim().length > 0
                        onClicked: home.send()
                    }
                }
                Text { anchors.horizontalCenter: parent.horizontalCenter; text: "回车交给我"
                       color: Theme.faint; font.pixelSize: Theme.meta }
            }
        }

        // ---- this node, and its mesh -----------------------------------------------------------------
        Page {
            index: 1
            anchors.fill: parent
            Flickable {
                id: nodePage
                objectName: "nodePage"
                anchors.fill: parent
                contentWidth: width
                contentHeight: nodeBody.implicitHeight + 56
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: Bar {}
                Column {
                    id: nodeBody
                    readonly property real measure: Math.min(nodePage.width - 72, 760)
                    x: (nodePage.width - measure) / 2
                    y: 26
                    width: measure
                    spacing: 16

                    PageTitle { title: "节点与网格"; sub: "这台电脑上的我，和我们的网格。" }
                    Item { width: 1; height: 4 }

                    Panel {                                // this node
                        width: parent.width
                        Row {
                            width: parent.width
                            spacing: 16
                            Orb { size: 56; anchors.verticalCenter: parent.verticalCenter
                                  mood: home.node.state === "exited" ? "waiting"
                                        : (home.node.state === "starting" ? "thinking" : "rest")
                                  lit: home.node.state === "running" }
                            Column {
                                anchors.verticalCenter: parent.verticalCenter
                                width: parent.width - 72 - linkPill.width - 16
                                spacing: 3
                                Text { text: home.node.stateWords || ""; color: Theme.text; font.pixelSize: Theme.title
                                       font.weight: Font.Medium; textFormat: Text.PlainText }
                                Text { text: home.node.since || "正在问它……"; color: Theme.soft
                                       font.pixelSize: Theme.body; textFormat: Text.PlainText }
                            }
                            StatusPill {
                                id: linkPill
                                anchors.verticalCenter: parent.verticalCenter
                                text: home.node.appConnected ? "和 Studio 连着" : (home.node.answered ? "没连上" : "正在连")
                                tint: home.node.appConnected ? Theme.calm : Theme.warm
                            }
                        }
                        Rectangle { width: parent.width; height: 1; color: Theme.hairline }
                        Column {
                            width: parent.width
                            Fact { width: parent.width; label: "这台节点"; value: home.node.did || "" }
                            Fact { width: parent.width; label: "角色"; value: home.node.roles || "" }
                            Fact { width: parent.width; label: "监听"; value: home.node.listen || "" }
                            Fact { width: parent.width; label: "连接方式"; value: home.node.carriers || "" }
                        }
                        Text { visible: !!home.node.halted; width: parent.width; wrapMode: Text.Wrap; color: Theme.warm
                               font.pixelSize: Theme.body; text: "它被叫停了：不会再开始新的事，直到你重启它。" }
                    }

                    Panel {                                // its work
                        width: parent.width
                        Row {
                            width: parent.width
                            Heading { id: workHead; text: "在做的事" }
                            Text { width: parent.width - workHead.width; horizontalAlignment: Text.AlignRight
                                   anchors.baseline: workHead.baseline; text: home.node.work || ""
                                   textFormat: Text.PlainText; color: Theme.soft; font.pixelSize: Theme.body
                                   elide: Text.ElideLeft }
                        }
                        Column {
                            width: parent.width
                            visible: (home.node.recent || []).length > 0
                            Repeater {
                                model: home.node.recent || []
                                Item {
                                    id: goal
                                    required property var modelData
                                    required property int index
                                    width: parent.width
                                    height: 44
                                    Rectangle { visible: goal.index > 0; width: parent.width; height: 1
                                                color: Theme.hairline }
                                    Text { anchors.left: parent.left; anchors.right: goalState.left
                                           anchors.rightMargin: 12; anchors.verticalCenter: parent.verticalCenter
                                           text: goal.modelData.title; textFormat: Text.PlainText
                                           elide: Text.ElideRight; color: Theme.text; font.pixelSize: Theme.body }
                                    StatusPill { id: goalState; anchors.right: parent.right
                                                 anchors.verticalCenter: parent.verticalCenter
                                                 text: goal.modelData.words; tint: home.goalTint(goal.modelData.status) }
                                }
                            }
                        }
                    }

                    Panel {                                // the mesh
                        width: parent.width
                        Heading { text: "网格「" + (home.node.mesh || "home") + "」" }
                        Row {
                            width: parent.width
                            spacing: 24
                            Item {                         // this node, and the places other nodes will take
                                width: 200
                                height: 150
                                Shape {
                                    anchors.fill: parent
                                    antialiasing: true
                                    ShapePath {
                                        strokeColor: Theme.tinted(Theme.text, 0.22)
                                        strokeWidth: 1
                                        strokeStyle: ShapePath.DashLine
                                        dashPattern: [3, 4]
                                        fillColor: "transparent"
                                        PathMove { x: 100; y: 64 } PathLine { x: 40; y: 30 }
                                        PathMove { x: 100; y: 64 } PathLine { x: 160; y: 30 }
                                        PathMove { x: 100; y: 64 } PathLine { x: 100; y: 124 }
                                        PathAngleArc { centerX: 28; centerY: 22; radiusX: 13; radiusY: 13
                                                       startAngle: 0; sweepAngle: 360 }
                                        PathAngleArc { centerX: 172; centerY: 22; radiusX: 13; radiusY: 13
                                                       startAngle: 0; sweepAngle: 360 }
                                        PathAngleArc { centerX: 100; centerY: 134; radiusX: 13; radiusY: 13
                                                       startAngle: 0; sweepAngle: 360 }
                                    }
                                }
                                Orb { x: 100 - 26; y: 64 - 26; size: 52; mood: "rest"; lit: true }
                                Text { x: 112; y: 56; text: "这台"; color: Theme.soft; font.pixelSize: Theme.meta }
                            }
                            Column {
                                width: parent.width - 224
                                spacing: 8
                                anchors.verticalCenter: parent.verticalCenter
                                Text { text: "现在只有这台电脑上的我。"; color: Theme.text; font.pixelSize: Theme.body
                                       font.weight: Font.Medium }
                                Text { width: parent.width; wrapMode: Text.Wrap; lineHeight: 1.5
                                       color: Theme.soft; font.pixelSize: 13
                                       text: "另一台电脑上的节点要用你签发的邀请码加进来。邀请码、局域网发现和 WebRTC 连接还在做，做好后，它们会出现在这些空位上。" }
                            }
                        }
                        Rectangle { width: parent.width; height: 1; color: Theme.hairline }
                        Column {
                            width: parent.width
                            Fact { width: parent.width; label: "你的签名钥匙"; value: home.node.operator || "" }
                            Fact { width: parent.width; label: "你的 App"; value: home.node.app || "" }
                        }
                    }

                    Panel {                                // the rules it keeps
                        width: parent.width
                        Row {
                            width: parent.width
                            Heading { id: rulesHead; text: "门的规则" }
                            Item { width: parent.width - rulesHead.width - editLink.width; height: 1 }
                            Link {
                                id: editLink
                                anchors.verticalCenter: rulesHead.verticalCenter
                                text: home.editingRules ? "收起" : "编辑规则"
                                onClicked: {
                                    if (!home.editingRules) { P.openRules(); rulesEditor.load() }
                                    home.editingRules = !home.editingRules
                                }
                            }
                        }
                        Text { visible: (home.node.rules || []).length === 0; width: parent.width; wrapMode: Text.Wrap
                               text: "还没有注入过规则，用的是内置的门。"; color: Theme.soft; font.pixelSize: Theme.body }
                        Column {
                            width: parent.width
                            Repeater {
                                model: home.node.rules || []
                                Item {
                                    id: rule
                                    required property var modelData
                                    required property int index
                                    width: parent.width
                                    height: 44
                                    Rectangle { visible: rule.index > 0; width: parent.width; height: 1
                                                color: Theme.hairline }
                                    Text { anchors.verticalCenter: parent.verticalCenter; text: rule.modelData.name
                                           color: Theme.text; font.pixelSize: Theme.body }
                                    StatusPill { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                                 text: rule.modelData.words
                                                 tint: rule.modelData.applied ? Theme.calm : Theme.faint }
                                }
                            }
                        }
                        RulesEditor { id: rulesEditor; visible: home.editingRules; width: parent.width; showInForce: false
                                      editorHeight: 240 }
                    }
                }
            }
        }
    }
}
