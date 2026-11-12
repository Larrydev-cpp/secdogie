// Studio's home window: an overview of you, the companion and your devices on
// cards; the conversation; pairing your devices; the gates' rules; and what
// the project is. Two worlds: 墨 (ink) and 宣纸 (rice paper), changed by the
// switch at the top right, with ink (or paper) flooding up the window.
//
// Opening Studio opens it. Closing it leaves the companion as a light in the
// corner of the screen (Presence.qml); clicking the light brings this window
// back. Before there is a profile, the whole window is the first meeting
// (Onboarding.qml). All state comes from the bridge `P` (secdogie_studio/app.py);
// the design system is in studio/DESIGN.md.
import QtQuick
import QtQuick.Window
import QtQuick.Controls
import QtQuick.Shapes
import "."

Window {
    id: home
    title: "secdogie"
    width: 1240
    height: 820
    minimumWidth: 1000
    minimumHeight: 680
    color: Theme.ground
    visible: P.homeOpen
    onClosing: (close) => { close.accepted = false; P.closeHome() }   // closing leaves the light

    property int page: 0                       // 0 overview, 1 conversation, 2 devices, 3 rules, 4 about
    readonly property bool first: (P.onboarding || "") !== ""
    readonly property int waitingCount: P.homeCard ? 1 + P.waiting : 0
    readonly property var node: P.node || ({})
    readonly property var dev: P.devices || ({})
    readonly property var pair: dev.pair || ({})
    readonly property var join: dev.join || ({})
    readonly property string you: P.you || ""
    readonly property real margin: 28

    Binding { target: Theme; property: "dark"; value: P.theme !== "light" }
    Binding { target: Theme; property: "lang"; value: P.lang || "zh" }

    function t(zh, en) { return Theme.t(zh, en) }
    function shortDid(did) { return did && did.length > 24 ? did.slice(0, 14) + "…" + did.slice(-6) : (did || "") }
    function two(n) { return n < 10 ? "0" + n : "" + n }
    function uptimeText(s) {
        if (!s) return "0:00"
        const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60)
        return h > 99 ? Math.floor(h / 24) + "d" : h + ":" + two(m)
    }
    function hereText(s) {
        const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60)
        return h > 0 ? h + "h" + two(m) : m + "m"
    }
    function stateColor(state) {
        switch (state) {
        case "online": case "running": return Theme.calm
        case "connecting": case "starting": return Theme.warm
        case "refused": case "exited": return Theme.sign
        default: return Theme.faint
        }
    }
    function stateWords(d) {
        switch (d.state) {
        case "online": return t("在线 · 直连", "Online · direct") + (d.rtt !== null && d.rtt !== undefined ? " · " + d.rtt + t(" 毫秒", " ms") : "")
        case "connecting": return t("连接中…", "Connecting…")
        case "refused": return t("被拒绝", "Refused")
        default: return t("不在线", "Offline")
        }
    }
    function send() {
        if (say.text.trim().length === 0) return
        P.say(say.text)
        say.text = ""
    }

    // ---- the conversation, appended as it grows (the list never jumps) --------------------
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
    Shortcut { sequence: "Ctrl+3"; onActivated: home.page = 2 }
    Shortcut { sequence: "Ctrl+4"; onActivated: home.page = 3 }
    Shortcut { sequence: "Ctrl+5"; onActivated: home.page = 4 }

    // ---- parts only this window uses ----------------------------------------------------------
    component Page: Item {
        id: pageItem
        property int index
        readonly property bool here: home.page === index
        opacity: here ? 1 : 0
        visible: opacity > 0.01
        enabled: here
        Behavior on opacity { NumberAnimation { duration: pageItem.here ? Theme.enter : Theme.exit } }
    }
    component Words: Text {                    // a line of the interface
        textFormat: Text.PlainText
        color: Theme.soft
        font.pixelSize: Theme.body
        wrapMode: Text.Wrap
        lineHeight: 1.35
    }
    component Lcd: Rectangle {                 // dot numerals on their own dark (or light) plate
        property alias text: digits.text
        property real pitch: 5.5
        property color plate: Theme.inverse
        property color lit: Theme.inverseText
        width: digits.width + 22
        height: digits.height + 18
        radius: 10
        color: plate
        DotText { id: digits; anchors.centerIn: parent; pitch: parent.pitch; on: parent.lit
                  off: Theme.tinted(parent.lit, 0.12) }
    }
    component Scene: Item {                    // an ink-wash landscape: hills, a moon, mist
        id: scene
        clip: true
        property real drop: 0                  // push the hills down (0..0.3): room for words above them
        Rectangle { anchors.fill: parent; radius: 14; color: Theme.dark ? "#262C3A" : "#E3DAC6" }
        Rectangle { x: parent.width * 0.68; y: parent.height * 0.16; width: parent.height * 0.26; height: width
                    radius: width / 2; color: Theme.dark ? "#E9D9B0" : "#C9573E"; opacity: 0.9 }
        Shape {
            anchors.fill: parent
            ShapePath {
                fillColor: Theme.dark ? "#3A4256" : "#B9AE97"; strokeColor: "transparent"
                startX: 0; startY: scene.height * (0.62 + scene.drop)
                PathCubic { x: scene.width * 0.45; y: scene.height * (0.38 + scene.drop)
                            control1X: scene.width * 0.15; control1Y: scene.height * (0.34 + scene.drop)
                            control2X: scene.width * 0.3; control2Y: scene.height * (0.46 + scene.drop) }
                PathCubic { x: scene.width; y: scene.height * (0.5 + scene.drop)
                            control1X: scene.width * 0.62; control1Y: scene.height * (0.28 + scene.drop)
                            control2X: scene.width * 0.82; control2Y: scene.height * (0.56 + scene.drop) }
                PathLine { x: scene.width; y: scene.height } PathLine { x: 0; y: scene.height }
            }
            ShapePath {
                fillColor: Theme.dark ? "#141824" : "#3B3A3A"; strokeColor: "transparent"
                startX: 0; startY: scene.height * (0.8 + scene.drop)
                PathCubic { x: scene.width * 0.55; y: scene.height * (0.66 + scene.drop)
                            control1X: scene.width * 0.2; control1Y: scene.height * (0.62 + scene.drop)
                            control2X: scene.width * 0.38; control2Y: scene.height * (0.78 + scene.drop) }
                PathCubic { x: scene.width; y: scene.height * (0.74 + scene.drop)
                            control1X: scene.width * 0.72; control1Y: scene.height * (0.56 + scene.drop)
                            control2X: scene.width * 0.86; control2Y: scene.height * (0.8 + scene.drop) }
                PathLine { x: scene.width; y: scene.height } PathLine { x: 0; y: scene.height }
            }
        }
    }
    component Act: AbstractButton {           // a text button: primary (filled) or quiet (outlined)
        id: act
        property bool primary: true
        property bool onSeal: false            // on the cinnabar card
        hoverEnabled: true
        focusPolicy: Qt.TabFocus
        implicitHeight: 38
        implicitWidth: actLabel.implicitWidth + 32
        HoverHandler { cursorShape: Qt.PointingHandCursor }
        Keys.onReturnPressed: act.animateClick()
        Keys.onSpacePressed: act.animateClick()
        Accessible.role: Accessible.Button
        Accessible.name: act.text
        opacity: enabled ? 1 : 0.45
        background: Rectangle {
            radius: height / 2
            color: act.onSeal ? (act.primary ? Theme.sealText : "transparent")
                              : (act.primary ? Theme.inverse : (act.hovered ? Theme.wash : "transparent"))
            border.color: act.visualFocus ? Theme.focus
                          : (act.primary ? "transparent" : (act.onSeal ? Theme.sealSoft : Theme.edge))
            border.width: act.visualFocus ? 2 : 1
        }
        contentItem: Text {
            id: actLabel
            text: act.text
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            color: act.onSeal ? (act.primary ? Theme.seal : Theme.sealText)
                              : (act.primary ? Theme.inverseText : Theme.text)
            font.pixelSize: 13
            font.weight: Font.DemiBold
            textFormat: Text.PlainText
        }
    }

    // ---- the first meeting -------------------------------------------------------------------
    Onboarding { objectName: "onboarding"; anchors.fill: parent; visible: home.first }

    // ---- the top bar: the mark, the pages, the language, the world, you ----------------------
    Item {
        id: bar
        x: home.margin
        width: home.width - 2 * home.margin
        height: 76

        Row {
            anchors.verticalCenter: parent.verticalCenter
            spacing: 10
            Grid {                                                   // the mark: nine dots, one lit
                anchors.verticalCenter: parent.verticalCenter
                columns: 3
                spacing: 3
                Repeater {
                    model: 9
                    Rectangle { required property int index; width: 4; height: 4; radius: 2
                                color: index === 4 ? Theme.seal : Theme.text }
                }
            }
            Text { anchors.verticalCenter: parent.verticalCenter; text: "secdogie"; color: Theme.text
                   font.family: Theme.serif; font.pixelSize: Theme.lead; font.weight: Font.DemiBold }
        }

        NavPill {
            visible: !home.first
            anchors.centerIn: parent
            current: home.page
            items: [{ text: home.t("总览", "Overview") },
                    { text: home.t("对话", "Talk"), badge: home.page !== 1 ? home.waitingCount : 0 },
                    { text: home.t("设备", "Devices") },
                    { text: home.t("规则", "Rules") },
                    { text: home.t("关于", "About") }]
            onPicked: (i) => home.page = i
        }

        Row {
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            spacing: 10
            Rectangle {                                              // 中 / EN
                anchors.verticalCenter: parent.verticalCenter
                width: langRow.implicitWidth + 8; height: 36; radius: 18
                color: Theme.panel; border.color: Theme.hairline
                Row {
                    id: langRow
                    anchors.centerIn: parent
                    spacing: 2
                    Repeater {
                        model: [["zh", "中"], ["en", "EN"]]
                        AbstractButton {
                            id: lb
                            required property var modelData
                            readonly property bool on: (P.lang || "zh") === modelData[0]
                            width: 34; height: 28
                            focusPolicy: Qt.TabFocus
                            Accessible.name: modelData[0] === "zh" ? "中文" : "English"
                            Accessible.role: Accessible.RadioButton
                            Accessible.checked: lb.on
                            onClicked: P.setLanguage(modelData[0])
                            HoverHandler { cursorShape: Qt.PointingHandCursor }
                            background: Rectangle { radius: 14; color: lb.on ? Theme.inverse : "transparent"
                                                    border.color: lb.visualFocus ? Theme.focus : "transparent"; border.width: 2 }
                            contentItem: Text { text: lb.modelData[1]; horizontalAlignment: Text.AlignHCenter
                                                verticalAlignment: Text.AlignVCenter; font.pixelSize: 12
                                                color: lb.on ? Theme.inverseText : Theme.soft }
                        }
                    }
                }
            }
            ThemeSwitch {
                id: themeSwitch
                objectName: "themeSwitch"
                anchors.verticalCenter: parent.verticalCenter
                onChosen: (dark, x, y) => {
                    home.nextTheme = dark ? "dark" : "light"
                    flood.play(dark ? "#12151D" : "#E8E0CD", x, y)
                }
            }
            Rectangle {                                              // you
                anchors.verticalCenter: parent.verticalCenter
                width: 36; height: 36; radius: 18
                color: Theme.panel; border.color: Theme.hairline
                Text { anchors.centerIn: parent; text: home.you ? home.you.charAt(0) : home.t("你", "Y")
                       color: Theme.text; font.pixelSize: 13; font.weight: Font.DemiBold }
                Accessible.role: Accessible.StaticText
                Accessible.name: home.you || home.t("你", "You")
            }
        }
    }
    property string nextTheme: ""

    Item {
        id: pages
        visible: !home.first
        anchors.top: bar.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom

        // ==== 总览: the cards ========================================================================
        Page {
            index: 0
            anchors.fill: parent
            Flickable {
                id: overview
                objectName: "overview"
                anchors.fill: parent
                contentHeight: grid.height + home.margin
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: Bar {}
                Item {
                    id: grid
                    x: home.margin
                    y: 4
                    width: overview.width - 2 * home.margin
                    readonly property real gap: Theme.gap
                    readonly property real col: (width - 3 * gap) / 4
                    readonly property real rowA: Math.max(300, (overview.height - home.margin - gap - 4) * 0.56)
                    readonly property real rowB: Math.max(240, overview.height - home.margin - gap - 4 - rowA)
                    height: rowA + gap + rowB

                    // -- you, here: time at this computer today, the last two hours in dots --
                    Card {
                        x: 0; width: grid.col; height: grid.rowA
                        title: home.t("你在场", "You, here")
                        chip: home.t("今天", "TODAY")
                        Lcd { text: home.hereText(P.hereToday || 0) }
                        Words { y: 66; width: parent.width; font.pixelSize: Theme.meta; color: Theme.faint
                                text: home.t("在电脑前的时间，只看系统的空闲时长", "At this computer, from the system's idle time only") }
                        DotBars { anchors.bottom: parent.bottom; anchors.bottomMargin: 30
                                  anchors.horizontalCenter: parent.horizontalCenter
                                  values: P.hereBars || []; pitch: Math.min(9, parent.width / 24); rows: 8 }
                        Words { anchors.bottom: parent.bottom; text: home.t("过去两小时 · 每格五分钟", "Last two hours · five minutes a column")
                                font.pixelSize: Theme.meta; color: Theme.faint }
                    }

                    // -- what it is doing now --
                    Card {
                        x: grid.col + grid.gap; width: grid.col; height: grid.rowA
                        title: home.t("正在做", "Now")
                        chip: (home.node.work || "").length > 0 ? home.t("进行中", "LIVE") : ""
                        chipLive: true
                        Scene { width: parent.width; height: parent.height * 0.46 }
                        Column {
                            y: parent.height * 0.46 + 14
                            width: parent.width
                            spacing: 6
                            Text { width: parent.width; text: home.node.work || home.t("没有事在做", "Nothing under way")
                                   color: Theme.text; font.family: Theme.serif; font.pixelSize: Theme.lead
                                   font.weight: Font.DemiBold; elide: Text.ElideRight; textFormat: Text.PlainText }
                            Repeater {
                                model: (home.node.recent || []).slice(0, 3)
                                Row {
                                    required property var modelData
                                    spacing: 8
                                    Rectangle { width: 6; height: 6; radius: 3; anchors.verticalCenter: parent.verticalCenter
                                                color: modelData.status === "done" ? Theme.calm
                                                       : (modelData.status === "failed" ? Theme.sign : Theme.warm) }
                                    Text { text: modelData.title; color: Theme.soft; font.pixelSize: Theme.meta
                                           elide: Text.ElideRight; width: grid.col - 60; textFormat: Text.PlainText }
                                }
                            }
                        }
                    }

                    // -- the day in 时辰 --
                    Card {
                        x: 2 * (grid.col + grid.gap); width: grid.col; height: grid.rowA
                        title: home.t("时辰", "The day")
                        chip: home.t("十二时辰", "12 SHICHEN")
                        Dial {
                            anchors.horizontalCenter: parent.horizontalCenter
                            width: Math.min(parent.width, parent.height - 34); height: width
                            present: P.present || []
                            now: P.shichen || 0
                            time: P.clock || ""
                        }
                        Words { anchors.bottom: parent.bottom; width: parent.width; font.pixelSize: Theme.meta
                                color: Theme.faint
                                text: home.t("金色的时辰，你在电脑前", "Gold: the 时辰 you were at this computer") }
                    }

                    // -- what waits on you: the seal card --
                    Card {
                        id: waitCard
                        x: 3 * (grid.col + grid.gap); width: grid.col; height: grid.rowA
                        kind: "seal"
                        title: home.t("等你", "Waiting on you")
                        Lcd { text: home.two(home.waitingCount); plate: Qt.darker(Theme.seal, 1.25); lit: Theme.sealText
                              pitch: 7 }
                        Words { y: 86; color: Theme.sealSoft; font.pixelSize: Theme.meta
                                text: home.waitingCount > 0 ? home.t("件事等你回答或签名", "to answer or sign")
                                                            : home.t("现在没有事等你", "Nothing waits on you now") }
                        Column {
                            y: 120
                            spacing: 8
                            Repeater {
                                model: [[home.t("提问", "Questions"), "ask"], [home.t("签名", "Signatures"), "sign"],
                                        [home.t("记忆", "Memories"), "memory"]]
                                Row {
                                    required property var modelData
                                    spacing: 8
                                    Rectangle { width: 6; height: 6; radius: 3; anchors.verticalCenter: parent.verticalCenter
                                                color: P.homeCard && P.homeCard.kind === modelData[1] ? Theme.sealText
                                                                                                      : Theme.sealSoft
                                                opacity: P.homeCard && P.homeCard.kind === modelData[1] ? 1 : 0.5 }
                                    Text { text: modelData[0]; color: Theme.sealText; font.pixelSize: Theme.meta }
                                }
                            }
                        }
                        Act { anchors.bottom: parent.bottom; onSeal: true; primary: home.waitingCount > 0
                              text: home.t("去对话", "Open the conversation"); onClicked: home.page = 1 }
                    }

                    // -- your devices --
                    Card {
                        y: grid.rowA + grid.gap; width: 2 * grid.col + grid.gap; height: grid.rowB
                        title: home.t("我的设备", "My devices")
                        chip: home.t("网格 ", "MESH ") + (home.dev.mesh || "home")
                        Column {
                            width: parent.width
                            spacing: 0
                            Repeater {
                                model: (home.dev.devices || []).slice(0, 4)
                                Item {
                                    required property var modelData
                                    required property int index
                                    width: parent.width; height: 38
                                    Rectangle { width: 22; height: 22; radius: 5; color: Theme.seal
                                                anchors.verticalCenter: parent.verticalCenter
                                                Text { anchors.centerIn: parent; text: home.two(index + 1); color: Theme.sealText
                                                       font.pixelSize: 11; font.weight: Font.DemiBold; font.features: ({ "tnum": 1 }) } }
                                    Text { x: 34; anchors.verticalCenter: parent.verticalCenter; width: parent.width * 0.3
                                           text: modelData.name || home.shortDid(modelData.did); color: Theme.text
                                           font.pixelSize: Theme.body; elide: Text.ElideRight; textFormat: Text.PlainText }
                                    Rectangle {
                                        x: parent.width * 0.42; width: parent.width * 0.3; height: 3; radius: 1.5
                                        anchors.verticalCenter: parent.verticalCenter
                                        color: Theme.hairline
                                        Rectangle { height: parent.height; radius: 1.5; color: home.stateColor(modelData.state)
                                                    width: modelData.state === "online"
                                                           ? parent.width * Math.max(0.15, 1 - Math.min(1, (modelData.rtt || 0) / 300))
                                                           : (modelData.state === "connecting" ? parent.width * 0.3 : 0) }
                                    }
                                    Text { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                           text: home.stateWords(modelData); color: home.stateColor(modelData.state)
                                           font.pixelSize: Theme.meta; font.features: ({ "tnum": 1 }) }
                                }
                            }
                        }
                        Words {
                            visible: (home.dev.devices || []).length === 0
                            width: parent.width
                            text: home.t("还没有别的设备。到「设备」页写一封请帖，用手机或另一台电脑打开就能加进来。",
                                         "No other devices yet. Write an invitation on the Devices page and open it on a phone or another computer.")
                        }
                        Act { anchors.bottom: parent.bottom; primary: false; text: home.t("管理设备", "Manage devices")
                              onClicked: home.page = 2 }
                    }

                    // -- this node: the inverse card --
                    Card {
                        x: 2 * (grid.col + grid.gap); y: grid.rowA + grid.gap; width: grid.col; height: grid.rowB
                        kind: "inverse"
                        title: home.t("节点", "This node")
                        chip: home.node.stateWords || ""
                        DotText { text: home.uptimeText(home.node.uptime || 0); pitch: 6; on: Theme.inverseText
                                  off: Theme.tinted(Theme.inverseText, 0.12) }
                        Text { y: 52; text: home.node.since || home.t("还没醒", "Not awake yet"); color: Theme.inverseSoft
                               font.pixelSize: Theme.meta; textFormat: Text.PlainText }
                        Row {                                                // roles, carriers, rules: three segments
                            y: 84; width: parent.width; spacing: 4
                            Rectangle { width: parent.width * 0.5; height: 6; radius: 3; color: Theme.inverseText }
                            Rectangle { width: parent.width * 0.3; height: 6; radius: 3; color: Theme.seal }
                            Rectangle { width: parent.width * 0.2 - 8; height: 6; radius: 3; color: Theme.gold }
                        }
                        Column {
                            anchors.bottom: parent.bottom
                            width: parent.width
                            spacing: 6
                            Repeater {
                                model: [[home.t("角色", "Roles"), home.node.roles || "—"],
                                        [home.t("连接", "Carriers"), home.node.carriers || "—"],
                                        [home.t("钥匙", "Key"), home.shortDid(home.node.did)]]
                                Item {
                                    required property var modelData
                                    width: parent.width; height: 18
                                    Text { text: modelData[0]; color: Theme.inverseSoft; font.pixelSize: Theme.meta }
                                    Text { anchors.right: parent.right; width: parent.width - 60; horizontalAlignment: Text.AlignRight
                                           text: modelData[1]; color: Theme.inverseText; font.pixelSize: Theme.meta
                                           elide: Text.ElideMiddle; textFormat: Text.PlainText }
                                }
                            }
                        }
                    }

                    // -- the gates' rules --
                    Card {
                        x: 3 * (grid.col + grid.gap); y: grid.rowA + grid.gap; width: grid.col; height: grid.rowB
                        title: home.t("门的规则", "The gates")
                        chip: (home.node.rules || []).length + home.t(" 份", " IN FORCE")
                        Column {
                            width: parent.width
                            spacing: 10
                            Repeater {
                                model: (home.node.rules || []).slice(0, 4)
                                Item {
                                    required property var modelData
                                    width: parent.width; height: 26
                                    Text { text: modelData.name; color: Theme.text; font.pixelSize: Theme.body
                                           textFormat: Text.PlainText }
                                    Text { anchors.right: parent.right; text: "v" + modelData.version; color: Theme.soft
                                           font.pixelSize: Theme.meta; font.features: ({ "tnum": 1 }) }
                                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 2; color: Theme.hairline
                                                Rectangle { height: 2; width: parent.width * (modelData.applied ? 1 : 0.4)
                                                            color: modelData.applied ? Theme.lamp : Theme.faint } }
                                }
                            }
                        }
                        Words { visible: (home.node.rules || []).length === 0; width: parent.width
                                text: home.t("还没有注入过规则，用的是内置的门：更严可以，放宽不行。",
                                             "No rules injected yet; the built-in gates apply. Rules can only make them stricter.") }
                        Act { anchors.bottom: parent.bottom; primary: false; text: home.t("编辑规则", "Edit the rules")
                              onClicked: home.page = 3 }
                    }
                }
            }
        }

        // ==== 对话: it writes, you answer ============================================================
        Page {
            id: talk
            index: 1
            anchors.fill: parent
            readonly property real measure: Math.min(width - 88, 720)
            readonly property real inset: (width - measure) / 2

            Text {
                id: talkTitle
                x: talk.inset; y: 8
                width: talk.measure
                text: (P.greeting || home.t("你好", "Hello")) + (home.you ? home.t("，", ", ") + home.you : "") + home.t("。", ".")
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
                anchors.topMargin: 18
                anchors.bottom: dock.top
                anchors.bottomMargin: 18
                clip: true
                model: convo
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: Bar {}
                function fit() {
                    const room = Math.max(0, height - contentHeight)
                    if (Math.abs(room - topMargin) > 1) topMargin = room
                    if (stick) positionViewAtEnd()
                }
                property bool stick: true
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
                    readonly property bool opens: who !== before
                    readonly property real gap: index === 0 ? 0 : (opens ? 22 : 8)
                    readonly property bool mine: who === "you"
                    readonly property bool its: who === "it"
                    readonly property var parts: words.split("\n\n")
                    readonly property bool letter: its && parts.length > 1
                    width: chat.width - 12
                    height: gap + (who === "note" ? note.implicitHeight : (mine ? bubble.height
                                                                           : (letter ? paper.height : said.implicitHeight)))
                    Text {
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
                    Rectangle {                                       // its mark: a small lamp
                        visible: msg.its && msg.opens
                        y: msg.gap + (msg.letter ? 16 : 4)
                        width: 14; height: 14; radius: 7
                        color: Theme.lamp
                    }
                    Text {
                        id: said
                        visible: msg.its && !msg.letter
                        x: 30
                        y: msg.gap
                        width: Math.min(implicitWidth, parent.width - 30)
                        text: msg.words
                        textFormat: Text.PlainText
                        wrapMode: Text.Wrap
                        color: msg.tone === "warm" ? Theme.warm : Theme.text
                        font.family: Theme.serif
                        font.pixelSize: Theme.voice
                        lineHeight: 1.7
                        Accessible.role: Accessible.StaticText
                        Accessible.name: home.t("它：", "It: ") + msg.words
                    }
                    Rectangle {
                        id: paper
                        visible: msg.letter
                        x: 30
                        y: msg.gap
                        width: parent.width - 30
                        height: letterBody.implicitHeight + 48
                        radius: 16
                        color: Theme.panel
                        border.color: Theme.hairline
                        Accessible.role: Accessible.StaticText
                        Accessible.name: home.t("它：", "It: ") + msg.words
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
                    Rectangle {
                        id: bubble
                        visible: msg.mine
                        y: msg.gap
                        x: msg.width - width
                        width: Math.min(mineText.implicitWidth + 30, msg.width * 0.75)
                        height: mineText.implicitHeight + 18
                        radius: 16
                        topRightRadius: msg.opens ? 4 : 16
                        color: Theme.inverse
                        Accessible.role: Accessible.StaticText
                        Accessible.name: home.t("你：", "You: ") + msg.words
                        Text {
                            id: mineText
                            x: 15; y: 9
                            width: bubble.width - 30
                            text: msg.words
                            textFormat: Text.PlainText
                            wrapMode: Text.Wrap
                            color: Theme.inverseText
                            font.pixelSize: Theme.body
                            lineHeight: 1.45
                        }
                    }
                }
            }
            Column {
                visible: convo.count === 0
                anchors.centerIn: chat
                width: Math.min(chat.width, 460)
                spacing: 16
                Rectangle { anchors.horizontalCenter: parent.horizontalCenter; width: 56; height: 56; radius: 28
                            color: Theme.lamp; opacity: 0.9 }
                Text { width: parent.width; horizontalAlignment: Text.AlignHCenter
                       text: home.t("想让我做点什么？", "What would you like me to do?")
                       color: Theme.text; font.family: Theme.serif; font.pixelSize: Theme.title; font.weight: Font.DemiBold }
                Text { width: parent.width; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
                       text: home.t("拿不准的地方我会先问你；改不回来的事，一定等你签名。",
                                    "Where I'm unsure I'll ask first; anything that can't be undone waits for your signature.")
                       color: Theme.soft; font.family: Theme.serif; font.pixelSize: Theme.voice - 1; lineHeight: 1.6 }
                Column {
                    anchors.horizontalCenter: parent.horizontalCenter
                    spacing: 2
                    topPadding: 8
                    Repeater {
                        model: Theme.lang === "en" ? ["Tidy up the Downloads folder", "Write this week's report", "How much disk space is left?"]
                                                   : ["整理一下下载文件夹", "写这周的周报", "看看磁盘还剩多少空间"]
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
                anchors.bottomMargin: 24
                spacing: 12
                Rectangle {
                    id: waiting
                    visible: !!P.homeCard && !home.first
                    width: parent.width
                    height: waitingCard.implicitHeight + 52 + (more.visible ? more.implicitHeight + 16 : 0)
                    radius: 18
                    color: Theme.panel
                    border.color: Theme.hairline
                    readonly property string key: P.homeCardKey
                    onKeyChanged: { waitingCard.reset(); if (key !== "") appear.restart() }
                    NumberAnimation { id: appear; target: waiting; property: "opacity"; from: 0; to: 1
                                      duration: Theme.enter; easing.type: Easing.OutExpo }
                    Rectangle { x: 26; width: 40; height: 3; radius: 1.5
                                color: waitingCard.kind === "sign" ? Theme.seal : (waitingCard.kind === "memory" ? Theme.warm : Theme.lamp) }
                    MomentCard { id: waitingCard; x: 26; y: 26; width: parent.width - 52
                                 c: home.first ? null : P.homeCard }
                    Text { id: more; visible: P.waiting > 0; x: 26; anchors.bottom: parent.bottom
                           anchors.bottomMargin: 20
                           text: home.t("答完这件，还有 " + P.waiting + " 件在等你。", P.waiting + " more after this one.")
                           color: Theme.faint; font.pixelSize: Theme.meta }
                }
                Rectangle {
                    width: parent.width
                    height: 56
                    radius: 28
                    color: Theme.panel
                    border.color: say.focused ? Theme.tinted(Theme.calm, 0.6) : Theme.hairline
                    Behavior on border.color { ColorAnimation { duration: Theme.touch } }
                    Field {
                        id: say
                        focus: true
                        bare: true
                        x: 24
                        width: parent.width - 24 - hand.width - 20
                        anchors.verticalCenter: parent.verticalCenter
                        hint: home.t("告诉我你想做什么，回车交给我", "Tell me what you want done; Enter hands it to me")
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
                        text: home.t("交给我", "Hand it over")
                        enabled: say.text.trim().length > 0
                        onClicked: home.send()
                    }
                }
            }
        }

        // ==== 设备: pairing, and your devices online =================================================
        Page {
            index: 2
            anchors.fill: parent
            Flickable {
                id: devPage
                objectName: "devicesPage"
                anchors.fill: parent
                contentHeight: devBody.height + home.margin
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: Bar {}
                Item {
                    id: devBody
                    x: home.margin
                    y: 4
                    width: devPage.width - 2 * home.margin
                    readonly property real colW: (width - Theme.gap) / 2
                    height: Math.max(leftCol.height, rightCol.height)

                    Column {
                        id: leftCol
                        width: devBody.colW
                        spacing: Theme.gap

                        // -- invite a device --
                        Card {
                            id: inviteCard
                            width: parent.width
                            visible: (home.dev.role || "owner") === "owner"
                            height: 108 + inviteBody.height
                            title: home.t("请一台设备进来", "Bring in a device")
                            chip: home.pair.phase === "waiting" ? home.t("还剩 ", "") + Math.floor((home.pair.left || 0) / 60) + ":"
                                                                  + home.two((home.pair.left || 0) % 60) + home.t("", " LEFT") : ""
                            Column {
                                id: inviteBody
                                width: parent.width
                                spacing: 12
                                // writing it
                                Column {
                                    visible: ["idle", "ended", undefined].indexOf(home.pair.phase) >= 0
                                    width: parent.width
                                    spacing: 10
                                    Words { width: parent.width
                                            text: home.t("写一封请帖，在另一台设备上打开（手机扫二维码，或另一台 Studio 粘贴）。两边显示同样的六位数，你在这里接纳它。写请帖要你的签名口令。",
                                                         "Write an invitation and open it on the other device (a phone scans the QR code; another Studio pastes it). Both show the same six digits, and you admit it here. Writing it takes your signing passphrase.") }
                                    Field { id: devName; width: parent.width; label: home.t("这台电脑叫", "This computer is called")
                                            text: home.dev.deviceName || ""; onAccepted: P.setDeviceName(text) }
                                    Field { id: sigUrl; width: parent.width; label: home.t("信令地址", "Signaling address")
                                            hint: "wss://…/ws"; text: home.dev.signal || "" }
                                    Field { id: pageUrl; width: parent.width; label: home.t("配对页地址（给手机用，可不填）", "Pairing page (for phones; optional)")
                                            hint: "https://…/pair.html"; text: home.dev.page || "" }
                                    Field { id: invitePass; width: parent.width; secret: true; label: home.t("签名口令", "Signing passphrase")
                                            onAccepted: if (writeButton.enabled) writeButton.clicked() }
                                    Words { visible: home.pair.phase === "ended" && !!home.pair.reason; width: parent.width
                                            color: Theme.warm; text: home.pair.reason || "" }
                                    Act { id: writeButton; text: home.t("写请帖", "Write the invitation")
                                          enabled: invitePass.text.length > 0 && sigUrl.text.length > 0
                                          onClicked: { P.setDeviceName(devName.text)
                                                       P.writeInvite(invitePass.text, sigUrl.text, pageUrl.text); invitePass.text = "" } }
                                }
                                // waiting for it
                                Row {
                                    visible: home.pair.phase === "waiting"
                                    width: parent.width
                                    spacing: 18
                                    QrCode { visible: (home.pair.qr || []).length > 0; rows: home.pair.qr || []; width: 180; height: 180 }
                                    Column {
                                        width: parent.width - ((home.pair.qr || []).length > 0 ? 198 : 0)
                                        spacing: 10
                                        Words { width: parent.width
                                                text: (home.pair.qr || []).length > 0
                                                      ? home.t("用另一台设备扫这个二维码，或打开下面的链接。", "Scan this with the other device, or open the link below.")
                                                      : home.t("在另一台 Studio 的「用请帖加入」里粘贴这段请帖。", "Paste this invitation into \"Join with an invitation\" on the other Studio.") }
                                        Rectangle {
                                            width: parent.width; height: 64; radius: 10; color: Theme.well
                                            Text { anchors.fill: parent; anchors.margins: 10; wrapMode: Text.WrapAnywhere
                                                   text: home.pair.link || home.pair.code_text || ""; color: Theme.soft
                                                   font.pixelSize: 11; elide: Text.ElideRight; maximumLineCount: 3
                                                   font.family: Qt.platform.os === "windows" ? "Consolas" : "monospace" }
                                        }
                                        Row {
                                            spacing: 10
                                            Act { text: home.t("复制", "Copy"); onClicked: P.copy(home.pair.link || home.pair.code_text || "") }
                                            Act { primary: false; text: home.t("收回", "Withdraw"); onClicked: P.withdrawInvite() }
                                        }
                                        Row {
                                            spacing: 8
                                            Rectangle { width: 7; height: 7; radius: 3.5; color: Theme.warm; anchors.verticalCenter: parent.verticalCenter }
                                            Words { text: home.t("等对方打开并点同意…", "Waiting for the other device to open it and agree…") }
                                        }
                                    }
                                }
                                // comparing the codes
                                Column {
                                    visible: home.pair.phase === "compare" || home.pair.phase === "admitting"
                                    width: parent.width
                                    spacing: 12
                                    Words { width: parent.width; color: Theme.text
                                            text: home.t("两块屏幕上的数字一样吗？一样就接纳；不一样说明有人在两台设备之间，取消即可。",
                                                         "Do both screens show the same number? If they match, admit it; if not, someone is between the two devices: cancel.") }
                                    Lcd { text: (home.pair.code || "").slice(0, 3) + " " + (home.pair.code || "").slice(3); pitch: 7 }
                                    Words { width: parent.width
                                            text: home.t("请求加入的是「", "Asking to join: \"") + (home.pair.name || home.t("未命名", "unnamed"))
                                                  + home.t("」 · ", "\" · ") + home.shortDid(home.pair.did) }
                                    Row {
                                        spacing: 10
                                        Act { text: home.t("一样，接纳", "They match, admit"); enabled: home.pair.phase === "compare"
                                              onClicked: P.decidePairing(true) }
                                        Act { primary: false; text: home.t("不一样，取消", "They differ, cancel")
                                              enabled: home.pair.phase === "compare"; onClicked: P.decidePairing(false) }
                                    }
                                }
                                // done
                                Row {
                                    visible: home.pair.phase === "done"
                                    spacing: 16
                                    Rectangle { width: 52; height: 52; radius: 6; color: Theme.seal; rotation: -4
                                                Text { anchors.centerIn: parent; text: "纳"; color: Theme.sealText; font.pixelSize: 26
                                                       font.family: Theme.serif; font.weight: Font.Bold } }
                                    Column {
                                        spacing: 8
                                        Text { text: home.t("已接纳「", "Admitted \"") + (home.pair.name || "") + home.t("」", "\"")
                                               color: Theme.text; font.pixelSize: Theme.lead; font.weight: Font.DemiBold }
                                        Act { primary: false; text: home.t("再请一台", "Invite another"); onClicked: P.withdrawInvite() }
                                    }
                                }
                            }
                        }

                        // -- join with an invitation --
                        Card {
                            width: parent.width
                            height: 108 + joinBody.height
                            title: home.t("用请帖加入", "Join with an invitation")
                            Column {
                                id: joinBody
                                width: parent.width
                                spacing: 12
                                Column {
                                    visible: ["idle", "ended", undefined].indexOf(home.join.phase) >= 0
                                    width: parent.width
                                    spacing: 10
                                    Words { width: parent.width
                                            text: home.t("把另一台设备给你的请帖（链接或那段字）粘贴在这里。看过之后点同意才会联网。",
                                                         "Paste the invitation another device gave you (the link or the code). Nothing connects until you agree.") }
                                    Field { id: inviteText; width: parent.width; label: home.t("请帖", "Invitation")
                                            onAccepted: P.readInvite(text) }
                                    Words { visible: home.join.phase === "ended" && !!home.join.reason; width: parent.width
                                            color: Theme.warm; text: home.join.reason || "" }
                                    Act { text: home.t("看看请帖", "Read it"); enabled: inviteText.text.trim().length > 0
                                          onClicked: P.readInvite(inviteText.text) }
                                }
                                Column {
                                    visible: home.join.phase === "card" || home.join.phase === "joining"
                                    width: parent.width
                                    spacing: 8
                                    Words { width: parent.width; color: Theme.text
                                            text: home.t("邀请你加入网格「", "You are invited into the mesh \"") + (home.join.mesh || "")
                                                  + home.t("」", "\"") }
                                    Words { width: parent.width; text: home.t("邀请者 ", "From ") + home.shortDid(home.join.inviter) }
                                    Row {
                                        spacing: 10
                                        Act { text: home.join.phase === "joining" ? home.t("正在连接…", "Connecting…") : home.t("同意加入", "Agree and join")
                                              enabled: home.join.phase === "card"; onClicked: P.agreeInvite() }
                                        Act { primary: false; text: home.t("不了", "No"); onClicked: P.resetJoin() }
                                    }
                                }
                                Column {
                                    visible: home.join.phase === "compare"
                                    width: parent.width
                                    spacing: 10
                                    Words { width: parent.width; color: Theme.text
                                            text: home.t("对照另一块屏幕上的六位数，在那边确认。", "Compare with the six digits on the other screen, and confirm there.") }
                                    Lcd { text: (home.join.code || "").slice(0, 3) + " " + (home.join.code || "").slice(3); pitch: 7 }
                                }
                                Words {
                                    visible: home.join.phase === "done"
                                    width: parent.width; color: Theme.calm
                                    text: home.t("已加入「", "Joined \"") + (home.join.mesh || "") + home.t("」。在右边上线，就能见到你的其他设备。", "\". Go online on the right to meet your other devices.")
                                }
                            }
                        }
                    }

                    Column {
                        id: rightCol
                        x: devBody.colW + Theme.gap
                        width: devBody.colW
                        spacing: Theme.gap

                        Card {
                            width: parent.width
                            height: devCol.implicitHeight + 76
                            title: home.t("我的设备", "My devices")
                            chip: home.dev.online ? home.t("在线", "ONLINE") : home.t("不在线", "OFFLINE")
                            chipLive: !!home.dev.online
                            Column {
                                id: devCol
                                width: parent.width
                                spacing: 12
                                Words { width: parent.width
                                        text: !home.dev.ready ? home.t("这台电脑还不在网格里：先写一封请帖，或用请帖加入。",
                                                                      "This computer is not in a mesh yet: write an invitation, or join with one.")
                                                             : home.t("上线后，这台电脑和你其他在线的设备经 WebRTC 直连；每条消息都由发送的设备签名。关掉 Studio 就下线。",
                                                                      "Online, this computer links directly to your other online devices over WebRTC; every message is signed by the device that sent it. Close Studio and it goes offline.") }
                                Row {
                                    spacing: 10
                                    Act { text: home.dev.online ? home.t("下线", "Go offline") : home.t("上线", "Go online")
                                          primary: !home.dev.online
                                          enabled: !!home.dev.ready && (home.dev.online || (home.dev.signal || sigUrl.text).length > 0)
                                          onClicked: home.dev.online ? P.goOffline() : P.goOnline(home.dev.signal || sigUrl.text) }
                                }
                                Column {
                                    id: devList
                                    width: parent.width
                                    Repeater {
                                        model: home.dev.devices || []
                                        Item {
                                            required property var modelData
                                            width: devList.width; height: 54
                                            Rectangle { width: 10; height: 10; radius: 5; y: 10
                                                        color: modelData.state === "online" ? Theme.calm : "transparent"
                                                        border.color: home.stateColor(modelData.state); border.width: 1.5 }
                                            Column {
                                                x: 22; y: 4
                                                spacing: 2
                                                Text { text: (modelData.name || home.t("未命名的设备", "Unnamed device"))
                                                             + (modelData.owner ? home.t("  · 主人", "  · owner") : "")
                                                       color: Theme.text; font.pixelSize: Theme.body; textFormat: Text.PlainText }
                                                Text { text: home.shortDid(modelData.did); color: Theme.faint; font.pixelSize: 11
                                                       font.family: Qt.platform.os === "windows" ? "Consolas" : "monospace" }
                                            }
                                            Text { anchors.right: parent.right; y: 6; text: home.stateWords(modelData)
                                                   color: home.stateColor(modelData.state); font.pixelSize: Theme.meta }
                                            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.hairline }
                                        }
                                    }
                                }
                                Column {
                                    id: talkBox
                                    visible: !!home.dev.online
                                    width: parent.width
                                    spacing: 8
                                    Field { id: noteField; width: parent.width; label: home.t("说一句，发给所有在线的设备", "Say something to every device online")
                                            onAccepted: { P.sayToDevices(text); text = "" } }
                                    Repeater {
                                        model: (home.dev.notes || []).slice(0, 6)
                                        Text { required property var modelData; width: talkBox.width; wrapMode: Text.Wrap
                                               text: (modelData.mine ? home.t("这台电脑", "This computer") : (modelData.name || home.shortDid(modelData.from)))
                                                     + "：" + modelData.text
                                               color: modelData.mine ? Theme.lamp : Theme.soft; font.pixelSize: Theme.body
                                               textFormat: Text.PlainText }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        // ==== 规则 ==================================================================================
        Page {
            index: 3
            anchors.fill: parent
            Flickable {
                anchors.fill: parent
                contentHeight: rulesCard.height + home.margin + 4
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: Bar {}
                Card {
                    id: rulesCard
                    x: (parent.width - width) / 2
                    y: 4
                    width: Math.min(parent.width - 2 * home.margin, 860)
                    height: rulesEditor.implicitHeight + 150
                    title: home.t("门的规则", "The gates' rules")
                    chip: home.t("只能更严", "STRICTER ONLY")
                    Column {
                        width: parent.width
                        spacing: 14
                        Words { width: parent.width
                                text: home.t("规则写成 YAML，先试一句话看门怎么回应，再用签名口令签名注入。注入的规则只在你自己的节点上运行，只能让门更严。",
                                             "Rules are YAML. Try a sentence to see how the gate answers, then sign and inject them with your passphrase. Injected rules run only on your own nodes, and can only make the gates stricter.") }
                        RulesEditor { id: rulesEditor; width: parent.width; editorHeight: 260 }
                    }
                    Component.onCompleted: { P.openRules(); rulesEditor.load() }
                }
            }
        }

        // ==== 关于: what this is, and what it is good at ================================================
        Page {
            index: 4
            anchors.fill: parent
            Flickable {
                id: about
                objectName: "aboutPage"
                anchors.fill: parent
                contentHeight: aboutBody.height + home.margin
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: Bar {}
                Column {
                    id: aboutBody
                    x: home.margin
                    y: 4
                    width: about.width - 2 * home.margin
                    spacing: Theme.gap
                    Item {                                                    // the hero: ink-wash hills, an inscription
                        width: parent.width
                        height: 320
                        Scene { anchors.fill: parent; drop: 0.17 }
                        Text {
                            x: parent.width - 70; y: 26
                            text: home.t("吾之器物\n自成一网", "")
                            visible: Theme.lang !== "en"
                            color: Theme.dark ? "#F0E7D3" : "#1E2230"; opacity: 0.85
                            font.family: Theme.serif; font.pixelSize: 18; lineHeight: 1.0
                            width: 24; wrapMode: Text.WrapAnywhere
                        }
                        Column {
                            x: 36; y: 40
                            width: parent.width * 0.6
                            spacing: 12
                            Text { width: parent.width; wrapMode: Text.Wrap; color: Theme.dark ? "#F0E7D3" : "#1E2230"
                                   font.family: Theme.serif; font.pixelSize: 34; font.weight: Font.DemiBold; lineHeight: 1.2
                                   text: home.t("你的设备，自成一网；\n你的伙伴，先问后做。", "Your devices, one mesh.\nYour companion asks before it acts.") }
                            Text { width: parent.width; wrapMode: Text.Wrap; color: Theme.dark ? "#D6CCB6" : "#3A3428"
                                   font.pixelSize: Theme.body; lineHeight: 1.5
                                   text: home.t("secdogie 是一个只跑在你自己设备上的伙伴：没有中心服务器，每一步先过两道苏格拉底门，改不回来的事一定等你签名。",
                                                "secdogie is a companion that runs only on your own devices: no central server, every step through two Socratic gates, and nothing irreversible without your signature.") }
                        }
                    }
                    Text { text: home.t("架构", "Architecture"); color: Theme.text; font.family: Theme.serif; font.pixelSize: Theme.title
                           font.weight: Font.DemiBold; topPadding: 8 }
                    Row {
                        width: parent.width
                        spacing: Theme.gap
                        Repeater {
                            model: [
                                ["壹", home.t("你", "You"), home.t("签名口令只在你手里。高风险的事，任何模式下都要你亲自确认。",
                                                                 "The signing passphrase is yours alone. Anything high-risk needs you, in every mode.")],
                                ["贰", "Studio", home.t("这个应用：桌面上的伙伴，和你对话，替你配对设备，也是你签名的地方。",
                                                       "This app: the companion on your desktop. It talks with you, pairs your devices, and is where you sign.")],
                                ["叁", home.t("节点", "Node"), home.t("在后台做事的那部分：签名日志、两道门、决策模型。关掉 Studio 它就停。",
                                                                    "The part that works: a signed journal, two gates, the decision model. It stops when Studio closes.")],
                                ["肆", home.t("网格", "Mesh"), home.t("只由你的设备组成：浏览器里点同意，经 WebRTC 直连，两屏对码，主人签接纳书。",
                                                                    "Only your own devices: agree in a browser, link directly over WebRTC, compare codes, the owner signs an admission.")]
                            ]
                            Card {
                                required property var modelData
                                width: (aboutBody.width - 3 * Theme.gap) / 4
                                height: 200
                                title: modelData[1]
                                Text { anchors.right: parent.right; y: -36; text: modelData[0]; color: Theme.seal
                                       font.family: Theme.serif; font.pixelSize: 28; font.weight: Font.Bold }
                                Words { width: parent.width; text: modelData[2]; lineHeight: 1.5 }
                            }
                        }
                    }
                    Text { text: home.t("长处", "Strengths"); color: Theme.text; font.family: Theme.serif; font.pixelSize: Theme.title
                           font.weight: Font.DemiBold; topPadding: 8 }
                    Card {
                        width: parent.width
                        height: 72 + strengths.height
                        title: home.t("为什么是它", "Why it")
                        Column {
                            id: strengths
                            width: parent.width
                            spacing: 12
                            Repeater {
                                model: [
                                    home.t("只在你的设备上：没有中心服务器，没有公开节点，规则从不离开你的节点。",
                                           "Only your devices: no central server, no public nodes, and your rules never leave your nodes."),
                                    home.t("先问后做：每个新目标都过苏格拉底门，有疑问先问你一个问题。",
                                           "Ask before acting: every new goal goes through the Socratic gate, which asks you when in doubt."),
                                    home.t("高风险必须你签名：没有开关能关掉这一条。",
                                           "High-risk always needs your signature: no switch turns that off."),
                                    home.t("结构而非截屏：用无障碍树看界面，不读屏幕像素。",
                                           "Structure, not pixels: it reads the accessibility tree, never the picture on the screen."),
                                    home.t("处处可验：身份是 DID，日志签名成链，设备之间的每条消息都签名。",
                                           "Verifiable everywhere: DIDs for identity, a signed hash-chained journal, every message between devices signed.")
                                ]
                                Row {
                                    required property string modelData
                                    required property int index
                                    width: strengths.width
                                    spacing: 14
                                    Text { text: home.two(index + 1); color: Theme.seal; font.pixelSize: Theme.body; font.weight: Font.Bold
                                           font.features: ({ "tnum": 1 }) }
                                    Words { width: parent.width - 40; text: modelData; color: Theme.text }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    InkFlood {
        id: flood
        objectName: "inkFlood"
        onCovered: P.setTheme(home.nextTheme)
    }
}
