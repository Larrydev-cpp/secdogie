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
            color: item.current ? Theme.press : (item.down ? Theme.press : (item.hovered ? Theme.wash : "transparent"))
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

    component Section: Column {
        id: section
        property string heading
        width: parent.width
        spacing: 10
        Text { text: section.heading; color: Theme.text; font.pixelSize: 15; font.weight: Font.DemiBold
               textFormat: Text.PlainText; bottomPadding: 4 }
    }

    component Page: Item {
        id: pageItem
        property int index
        readonly property bool here: home.page === index
        opacity: here ? 1 : 0
        visible: opacity > 0.01
        enabled: here
        Behavior on opacity { NumberAnimation { duration: pageItem.here ? Theme.enter : Theme.exit } }
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
        Orb { anchors.fill: parent; size: 820; mood: P.mood; breathing: false; sphere: false; opacity: 0.06 }
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
                Text { text: "secdogie"; color: Theme.text; font.family: Theme.serif; font.pixelSize: Theme.lead
                       font.weight: Font.DemiBold }
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

        // ---- the conversation: it writes, you answer ----------------------------------------------------
        Page {
            id: talk
            index: 0
            anchors.fill: parent
            readonly property real measure: Math.min(width - 88, 700)     // about 40 characters of its serif
            readonly property real inset: (width - measure) / 2

            Text {                                         // the page's heading is its greeting
                id: talkTitle
                x: talk.inset; y: 30
                width: talk.measure
                text: (P.greeting || "你好") + (home.you ? "，" + home.you : "") + "。"
                textFormat: Text.PlainText
                color: Theme.text
                font.family: Theme.serif
                font.pixelSize: Theme.title
                font.weight: Font.DemiBold
            }
            ListView {
                id: chat
                x: talk.inset
                width: talk.measure
                anchors.top: talkTitle.bottom
                anchors.topMargin: 20
                anchors.bottom: dock.top
                anchors.bottomMargin: 20
                clip: true
                model: convo
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: Bar {}
                // A short conversation sits just above the input: the room above it is set after layout,
                // not bound, so it never feeds back into the layout it measures.
                function fit() {
                    const room = Math.max(0, height - contentHeight)
                    if (Math.abs(room - topMargin) > 1) topMargin = room
                    if (stick) positionViewAtEnd()
                }
                property bool stick: true                  // keep the newest line in view, unless you scrolled up
                onMovementEnded: stick = atYEnd
                onCountChanged: Qt.callLater(chat.fit)
                onHeightChanged: Qt.callLater(chat.fit)
                onContentHeightChanged: Qt.callLater(chat.fit)
                delegate: Item {
                    id: msg
                    required property int index
                    required property string who
                    required property string words
                    required property string tone
                    readonly property string before: index > 0 ? convo.get(index - 1).who : ""
                    readonly property bool opens: who !== before          // the first line of a run
                    readonly property real gap: index === 0 ? 0 : (opens ? 22 : 8)
                    readonly property bool mine: who === "you"
                    readonly property bool its: who === "it"
                    // a letter (a refusal at length) or a question with its reasons: a heading, then paragraphs
                    readonly property var parts: words.split("\n\n")
                    readonly property bool letter: its && parts.length > 1
                    width: chat.width - 12                 // the scrollbar's lane
                    height: gap + (who === "note" ? note.implicitHeight : (mine ? bubble.height
                                                                           : (letter ? paper.height : said.implicitHeight)))

                    Text {                                 // a quiet note, centred
                        id: note
                        visible: msg.who === "note"
                        y: msg.gap
                        width: parent.width
                        horizontalAlignment: Text.AlignHCenter
                        text: msg.words
                        textFormat: Text.PlainText
                        wrapMode: Text.Wrap
                        color: Theme.faint
                        font.pixelSize: Theme.meta
                    }
                    Orb {                                  // its mark, in the margin, where it starts to speak
                        visible: msg.its && msg.opens
                        y: msg.gap + (msg.letter ? 14 : -1)
                        size: 24
                        breathing: false
                        lit: true
                    }
                    Text {                                 // what it says: its serif, no bubble
                        id: said
                        visible: msg.its && !msg.letter
                        x: 36
                        y: msg.gap
                        width: Math.min(implicitWidth, parent.width - 36)
                        text: msg.words
                        textFormat: Text.PlainText
                        wrapMode: Text.Wrap
                        color: msg.tone === "warm" ? Theme.warm : Theme.text
                        font.family: Theme.serif
                        font.pixelSize: Theme.voice
                        lineHeight: 1.7
                        Accessible.role: Accessible.StaticText
                        Accessible.name: "它：" + msg.words
                    }
                    Rectangle {                            // a letter: on paper
                        id: paper
                        visible: msg.letter
                        x: 36
                        y: msg.gap
                        width: parent.width - 36
                        height: letterBody.implicitHeight + 48
                        radius: 12
                        color: Theme.panel
                        border.color: Theme.hairline
                        Accessible.role: Accessible.StaticText
                        Accessible.name: "它：" + msg.words
                        Column {
                            id: letterBody
                            x: 26; y: 24
                            width: parent.width - 52
                            spacing: 12
                            Text {
                                width: parent.width
                                text: msg.parts[0]
                                textFormat: Text.PlainText
                                wrapMode: Text.Wrap
                                color: msg.tone === "letter" ? Theme.warm : Theme.text
                                font.family: Theme.serif
                                font.pixelSize: Theme.lead
                                font.weight: Font.DemiBold
                                lineHeight: 1.45
                            }
                            Repeater {
                                model: msg.letter ? msg.parts.slice(1) : []
                                Text {
                                    required property string modelData
                                    width: letterBody.width
                                    text: modelData
                                    textFormat: Text.PlainText
                                    wrapMode: Text.Wrap
                                    color: Theme.soft
                                    font.family: Theme.serif
                                    font.pixelSize: Theme.voice - 1
                                    lineHeight: 1.75
                                }
                            }
                        }
                    }
                    Rectangle {                            // what you said: sans, in frost
                        id: bubble
                        visible: msg.mine
                        y: msg.gap
                        x: msg.width - width
                        width: Math.min(mineText.implicitWidth + 30, msg.width * 0.75)
                        height: mineText.implicitHeight + 18
                        radius: 14
                        topRightRadius: msg.opens ? 4 : 14
                        color: Theme.tinted(Theme.calm, 0.15)
                        border.color: Theme.tinted(Theme.calm, 0.26)
                        Accessible.role: Accessible.StaticText
                        Accessible.name: "你：" + msg.words
                        Text {
                            id: mineText
                            x: 15; y: 9
                            width: bubble.width - 30
                            text: msg.words
                            textFormat: Text.PlainText
                            wrapMode: Text.Wrap
                            color: Theme.text
                            font.pixelSize: Theme.body
                            lineHeight: 1.45
                        }
                    }
                }
            }
            Column {                                       // before the first word
                visible: convo.count === 0
                anchors.centerIn: chat
                width: Math.min(chat.width, 440)
                spacing: 16
                Orb { anchors.horizontalCenter: parent.horizontalCenter; size: 96; mood: P.mood }
                Text { width: parent.width; horizontalAlignment: Text.AlignHCenter; text: "想让我做点什么？"
                       color: Theme.text; font.family: Theme.serif; font.pixelSize: Theme.title
                       font.weight: Font.DemiBold }
                Text { width: parent.width; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
                       text: "拿不准的地方我会先问你；改不回来的事，一定等你签名。"
                       color: Theme.soft; font.family: Theme.serif; font.pixelSize: Theme.voice - 1; lineHeight: 1.6 }
                Column {                                   // things you might say, in your own (sans) words
                    anchors.horizontalCenter: parent.horizontalCenter
                    spacing: 2
                    topPadding: 8
                    Repeater {
                        model: ["整理一下下载文件夹", "写这周的周报", "看看磁盘还剩多少空间"]
                        Link { required property string modelData; anchors.horizontalCenter: parent.horizontalCenter
                               text: "「" + modelData + "」"; focusPolicy: Qt.TabFocus; onClicked: say.text = modelData }
                    }
                }
            }
            Column {
                id: dock
                x: talk.inset
                width: talk.measure
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 22
                spacing: 12
                Rectangle {                                // what waits on you, one at a time
                    id: waiting
                    visible: !!P.homeCard && !home.first
                    width: parent.width
                    height: waitingCard.implicitHeight + 52 + (more.visible ? more.implicitHeight + 16 : 0)
                    radius: 12
                    color: Theme.panel
                    border.color: Theme.hairline
                    readonly property string key: P.homeCardKey
                    readonly property color tint: waitingCard.kind === "sign" ? Theme.sign
                                                  : (waitingCard.kind === "memory" ? Theme.warm : Theme.lamp)
                    onKeyChanged: { waitingCard.reset(); if (key !== "") appear.restart() }
                    NumberAnimation { id: appear; target: waiting; property: "opacity"; from: 0; to: 1
                                      duration: Theme.enter; easing.type: Easing.OutExpo }
                    Rectangle {                            // its kind, as a line of light along the top edge
                        x: 26; width: parent.width - 52; height: 1
                        gradient: Gradient {
                            orientation: Gradient.Horizontal
                            GradientStop { position: 0.0; color: Theme.tinted(waiting.tint, 0) }
                            GradientStop { position: 0.5; color: Theme.tinted(waiting.tint, 0.85) }
                            GradientStop { position: 1.0; color: Theme.tinted(waiting.tint, 0) }
                        }
                    }
                    MomentCard { id: waitingCard; x: 26; y: 26; width: parent.width - 52
                                 c: home.first ? null : P.homeCard }
                    Text { id: more; visible: P.waiting > 0; x: 26; anchors.bottom: parent.bottom
                           anchors.bottomMargin: 20; text: "答完这件，还有 " + P.waiting + " 件在等你。"
                           color: Theme.faint; font.pixelSize: Theme.meta }
                }
                Rectangle {                                // the composer
                    width: parent.width
                    height: 56
                    radius: 22
                    color: Theme.panel
                    border.color: say.focused ? Theme.tinted(Theme.calm, 0.55) : Theme.hairline
                    Behavior on border.color { ColorAnimation { duration: Theme.touch } }
                    Field {
                        id: say
                        focus: true
                        bare: true
                        x: 22
                        width: parent.width - 22 - hand.width - 20
                        anchors.verticalCenter: parent.verticalCenter
                        hint: "告诉我你想做什么，回车交给我"
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
            }
        }

        // ---- this node, and its mesh: the constellation first ------------------------------------------
        Page {
            index: 1
            anchors.fill: parent
            Flickable {
                id: nodePage
                objectName: "nodePage"
                anchors.fill: parent
                contentWidth: width
                contentHeight: nodeBody.implicitHeight + 64
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: Bar {}
                Column {
                    id: nodeBody
                    readonly property real measure: Math.min(nodePage.width - 88, 720)
                    x: (nodePage.width - measure) / 2
                    y: 30
                    width: measure
                    spacing: 40

                    Row {                                  // the mesh as it is: this light, and the places others will take
                        width: parent.width
                        spacing: 28
                        Item {
                            id: sky
                            width: 300
                            height: 230
                            readonly property var places: [[44, 40], [250, 28], [276, 150], [150, 206], [22, 168]]
                            Shape {
                                anchors.fill: parent
                                antialiasing: true
                                ShapePath {
                                    strokeColor: Theme.tinted(Theme.text, 0.18)
                                    strokeWidth: 1
                                    strokeStyle: ShapePath.DashLine
                                    dashPattern: [2, 5]
                                    fillColor: "transparent"
                                    PathMove { x: 150; y: 110 } PathLine { x: 44; y: 40 }
                                    PathMove { x: 150; y: 110 } PathLine { x: 250; y: 28 }
                                    PathMove { x: 150; y: 110 } PathLine { x: 276; y: 150 }
                                    PathMove { x: 150; y: 110 } PathLine { x: 150; y: 206 }
                                    PathMove { x: 150; y: 110 } PathLine { x: 22; y: 168 }
                                }
                            }
                            Repeater {                     // the places other nodes will take: faint, empty
                                model: sky.places
                                Rectangle {
                                    required property var modelData
                                    x: modelData[0] - 9; y: modelData[1] - 9
                                    width: 18; height: 18; radius: 9
                                    color: Theme.ground
                                    border.color: Theme.tinted(Theme.text, 0.22)
                                }
                            }
                            Orb { x: 150 - 40; y: 110 - 40; size: 80
                                  mood: home.node.state === "exited" ? "waiting"
                                        : (home.node.state === "starting" ? "thinking" : "rest")
                                  lit: home.node.state === "running" }
                        }
                        Column {
                            width: parent.width - sky.width - 28
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 12
                            Text { width: parent.width; text: "网格「" + (home.node.mesh || "home") + "」里，现在只有我。"
                                   wrapMode: Text.Wrap; color: Theme.text; font.family: Theme.serif
                                   font.pixelSize: Theme.title; font.weight: Font.DemiBold; lineHeight: 1.3 }
                            Row {
                                spacing: 8
                                Rectangle { width: 7; height: 7; radius: 3.5; anchors.verticalCenter: parent.verticalCenter
                                            color: home.stateColor(home.node.state || "") }
                                Text { text: (home.node.stateWords || "") + (home.node.since ? "，" + home.node.since : "")
                                       color: Theme.soft; font.pixelSize: Theme.body; textFormat: Text.PlainText }
                            }
                            Text { width: parent.width; wrapMode: Text.Wrap; lineHeight: 1.7; color: Theme.soft
                                   font.family: Theme.serif; font.pixelSize: Theme.voice - 1
                                   text: "别的电脑上的节点，要用你签发的邀请码加进来。邀请码、局域网发现和 WebRTC 连接还在做；做好以后，它们会出现在这些空位上。" }
                        }
                    }

                    Section {                              // this node
                        heading: "这台节点"
                        Fact { width: parent.width; label: "它的钥匙"; value: home.node.did || "" }
                        Fact { width: parent.width; label: "角色"; value: home.node.roles || "" }
                        Fact { width: parent.width; label: "监听"; value: home.node.listen || "" }
                        Fact { width: parent.width; label: "连接方式"; value: home.node.carriers || "" }
                        Fact { width: parent.width; label: "Studio"
                               value: home.node.appConnected ? "连着它" : (home.node.answered ? "没连上" : "正在问它……")
                               tone: home.node.appConnected ? Theme.text : Theme.warm }
                        Text { visible: !!home.node.halted; width: parent.width; wrapMode: Text.Wrap; color: Theme.warm
                               font.pixelSize: Theme.body; text: "它被叫停了：不会再开始新的事，直到你重启它。" }
                    }

                    Section {                              // its work
                        heading: "在做的事"
                        Text { width: parent.width; text: home.node.work || ""; textFormat: Text.PlainText
                               color: Theme.soft; font.pixelSize: Theme.body; wrapMode: Text.Wrap }
                        Repeater {
                            model: home.node.recent || []
                            Item {
                                id: goal
                                required property var modelData
                                width: parent.width
                                height: 34
                                Text { anchors.left: parent.left; anchors.right: goalWords.left
                                       anchors.rightMargin: 16; anchors.verticalCenter: parent.verticalCenter
                                       text: goal.modelData.title; textFormat: Text.PlainText
                                       elide: Text.ElideRight; color: Theme.text; font.pixelSize: Theme.body }
                                Row {
                                    id: goalWords
                                    anchors.right: parent.right
                                    anchors.verticalCenter: parent.verticalCenter
                                    spacing: 7
                                    Rectangle { width: 6; height: 6; radius: 3; anchors.verticalCenter: parent.verticalCenter
                                                color: home.goalTint(goal.modelData.status) }
                                    Text { text: goal.modelData.words; color: Theme.soft; font.pixelSize: Theme.meta }
                                }
                            }
                        }
                    }

                    Section {                              // the keys that speak for you
                        heading: "你的钥匙"
                        Fact { width: parent.width; label: "签名钥匙"; value: home.node.operator || "" }
                        Fact { width: parent.width; label: "这台电脑的 App"; value: home.node.app || "" }
                    }

                    Section {                              // the rules it keeps
                        heading: "门的规则"
                        Text { visible: (home.node.rules || []).length === 0; width: parent.width; wrapMode: Text.Wrap
                               text: "还没有注入过规则，用的是内置的门。"; color: Theme.soft; font.pixelSize: Theme.body }
                        Repeater {
                            model: home.node.rules || []
                            Item {
                                id: rule
                                required property var modelData
                                width: parent.width
                                height: 34
                                Text { anchors.verticalCenter: parent.verticalCenter; text: rule.modelData.name
                                       color: Theme.text; font.pixelSize: Theme.body }
                                Row {
                                    anchors.right: parent.right
                                    anchors.verticalCenter: parent.verticalCenter
                                    spacing: 7
                                    Rectangle { width: 6; height: 6; radius: 3; anchors.verticalCenter: parent.verticalCenter
                                                color: rule.modelData.applied ? Theme.calm : Theme.faint }
                                    Text { text: rule.modelData.words; color: Theme.soft; font.pixelSize: Theme.meta
                                           font.features: ({ "tnum": 1 }) }
                                }
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
}
