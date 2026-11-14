// Studio's home window. The overview is a wallet: this node, what waits on you
// and each of your devices are cards, drawn like bank cards, with the chosen
// card in front and the rest stacked behind it; beside the wallet, the page
// is set the way Google sets its pages (Google Sans, a display greeting, flat
// tonal sheets, pill buttons) and says what the front card holds. The other
// pages: the conversation, pairing your devices, the gates' rules, and what
// the project is. Two worlds, 墨 (ink) and 宣纸 (rice paper), changed by the
// switch at the top right with ink (or paper) flooding up the window.
//
// Opening Studio opens it. Closing it leaves the companion as a light in the
// corner of the screen (Presence.qml); clicking the light brings this window
// back. Before there is a profile, the whole window is the first meeting
// (Onboarding.qml). All state comes from the bridge `P` (secdogie_studio/app.py);
// the tokens are in Theme.qml.
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
        case "online": case "running": return Theme.good
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
    readonly property string shichenNames: "子丑寅卯辰巳午未申酉戌亥"
    function tail(did) { return did ? did.slice(-5) : "" }
    function hm(s) {                           // 4h 23m
        s = Math.max(0, s || 0)
        const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60)
        return h > 0 ? h + "h " + two(m) + "m" : m + "m"
    }
    function hereZh(s) {
        s = Math.max(0, s || 0)
        const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60)
        return h > 0 ? h + " 小时 " + m + " 分钟" : m + " 分钟"
    }
    function stateShort(d) {
        switch (d.state) {
        case "online": return t("在线 · 直连", "Online · direct")
        case "connecting": return t("连接中…", "Connecting…")
        case "refused": return t("被拒绝", "Refused")
        default: return t("不在线", "Offline")
        }
    }
    readonly property int onlineCount: (dev.devices || []).filter(d => d.state === "online").length
    // ---- the wallet: this node, what waits on you, each of your devices, an empty slot ----------
    function deviceCard(d) {
        return { what: "device", did: d.did, kind: "glaze", glaze: Theme.glazeFor(d.did), key: d.did, panel: true,
                 title: d.name || t("未命名的设备", "Unnamed device"),
                 figure: d.state === "online" && d.rtt !== null && d.rtt !== undefined ? d.rtt + " ms" : "",
                 note: stateShort(d), live: d.state === "online", number: tail(d.did),
                 label: d.owner ? t("主人", "Owner") : t("成员", "Member") }
    }
    readonly property var walletCards: {
        const cards = [{ what: "node", kind: "dots", title: "secdogie", figure: hm(node.uptime || 0),
                         number: tail(node.did), note: node.since || node.stateWords || "", live: node.state === "running",
                         quarters: P.dayQuarters || [], now: P.quarterNow === undefined ? -1 : P.quarterNow },
                       { what: "waiting", kind: "glaze", glaze: waitingCount > 0 ? "zhusha" : "dai", key: "waiting",
                         title: t("等你", "Waiting on you"), figure: "" + waitingCount,
                         note: waitingCount > 0 ? t(waitingCount + " 件事等你回答或签名",
                                                    waitingCount === 1 ? "1 thing to answer or sign" : waitingCount + " things to answer or sign")
                                                : t("现在没有事等你", "Nothing waits on you") }]
        for (const d of (dev.devices || []))
            cards.push(deviceCard(d))
        if ((dev.role || "owner") === "owner")
            cards.push({ what: "add", kind: "add", title: t("加一台设备", "Add a device"),
                         note: t("写一封请帖，在另一台设备上打开", "Write an invitation; open it on the other device") })
        return cards
    }
    property int walletPick: 0
    property int devPick: 0
    readonly property var picked: walletCards[Math.min(walletPick, walletCards.length - 1)] || ({})
    readonly property var pickedDevice: picked.what === "device"
                                        ? ((dev.devices || []).find(d => d.did === picked.did) || ({})) : ({})
    readonly property var deviceCards: walletCards.filter(c => c.what === "device" || c.what === "add")
    function summary() {
        const n = (dev.devices || []).length
        const hour = shichenNames.charAt(P.shichen || 0)
        if (Theme.lang === "en") {
            let s = "It's " + (P.clock || "") + ", the hour of " + hour + ". You've been at this computer " + hm(P.hereToday) + " today"
            s += waitingCount > 0 ? "; " + waitingCount + (waitingCount === 1 ? " thing waits" : " things wait") + " on you"
                                  : "; nothing waits on you"
            return s + (n > 0 ? "; " + onlineCount + " of " + n + " devices are online." : ".")
        }
        let s = hour + "时，" + (P.clock || "") + "。今天你在电脑前 " + hereZh(P.hereToday)
        s += waitingCount > 0 ? "，" + waitingCount + " 件事等你" : "，没有事等你"
        return s + (n > 0 ? "；" + n + " 台设备里 " + onlineCount + " 台在线。" : "。")
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
    component Act: AbstractButton {           // a pill: filled in the primary colour, or outlined
        id: act
        property bool primary: true
        property bool onSeal: false            // on a cinnabar surface
        hoverEnabled: true
        focusPolicy: Qt.TabFocus
        implicitHeight: 40
        implicitWidth: actLabel.implicitWidth + 48
        HoverHandler { cursorShape: Qt.PointingHandCursor }
        Keys.onReturnPressed: act.animateClick()
        Keys.onSpacePressed: act.animateClick()
        Accessible.role: Accessible.Button
        Accessible.name: act.text
        opacity: enabled ? 1 : 0.4
        background: Item {
            Rectangle {                        // the keyboard's ring, just outside the pill
                anchors.fill: parent
                anchors.margins: -3
                radius: height / 2
                color: "transparent"
                border.color: Theme.focus
                border.width: 2
                visible: act.visualFocus
            }
            Rectangle {
                anchors.fill: parent
                radius: height / 2
                color: act.onSeal ? (act.primary ? Theme.sealText : "transparent")
                                  : (act.primary ? Theme.primary : "transparent")
                border.color: act.primary ? "transparent" : (act.onSeal ? Theme.sealSoft : Theme.edge)
                border.width: 1
                Rectangle {                    // the state layer: hover and press
                    anchors.fill: parent
                    radius: parent.radius
                    color: act.primary && !act.onSeal ? Theme.onPrimary : Theme.primary
                    opacity: act.pressed ? 0.14 : (act.hovered ? 0.08 : 0)
                    Behavior on opacity { NumberAnimation { duration: Theme.touch } }
                }
            }
        }
        contentItem: Text {
            id: actLabel
            text: act.text
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            color: act.onSeal ? (act.primary ? Theme.seal : Theme.sealText)
                              : (act.primary ? Theme.onPrimary : Theme.primary)
            font.pixelSize: 14
            font.weight: Font.Medium
            textFormat: Text.PlainText
        }
    }
    component KV: Item {                       // a row of a list: what, and its value
        id: kv
        property string k
        property string v
        property bool mono: false
        property bool last: false
        width: parent ? parent.width : 300
        height: 46
        Text { anchors.verticalCenter: parent.verticalCenter; text: kv.k; color: Theme.soft; font.pixelSize: Theme.body
               textFormat: Text.PlainText }
        Text { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; width: parent.width - 132
               horizontalAlignment: Text.AlignRight; text: kv.v; color: Theme.text
               font.pixelSize: kv.mono ? 12.5 : Theme.body; font.family: kv.mono ? Theme.mono : Theme.sans
               elide: Text.ElideMiddle; textFormat: Text.PlainText; font.features: ({ "tnum": 1 }) }
        Rectangle { visible: !kv.last; anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.hairline }
    }
    component Surface: Rectangle {             // a page's surface: flat, tonal, generous corners
        radius: 28
        color: Theme.panel
        border.color: Theme.dark ? Theme.hairline : "transparent"
    }
    component Heading: Text {                  // a sheet's or a section's title
        color: Theme.text
        font.pixelSize: Theme.title
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
    }
    component Dot: Rectangle { width: 8; height: 8; radius: 4 }

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
                   font.pixelSize: 22; font.weight: Font.Medium; font.letterSpacing: -0.3 }
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
                width: langRow.implicitWidth + 8; height: 40; radius: 20
                color: Theme.panel; border.color: Theme.dark ? Theme.hairline : "transparent"
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
                            width: 36; height: 32
                            focusPolicy: Qt.TabFocus
                            Accessible.name: modelData[0] === "zh" ? "中文" : "English"
                            Accessible.role: Accessible.RadioButton
                            Accessible.checked: lb.on
                            onClicked: P.setLanguage(modelData[0])
                            HoverHandler { cursorShape: Qt.PointingHandCursor }
                            background: Rectangle { radius: 16; color: lb.on ? Theme.primaryContainer : "transparent"
                                                    border.color: lb.visualFocus ? Theme.focus : "transparent"; border.width: 2 }
                            contentItem: Text { text: lb.modelData[1]; horizontalAlignment: Text.AlignHCenter
                                                verticalAlignment: Text.AlignVCenter; font.pixelSize: 13
                                                font.weight: Font.Medium
                                                color: lb.on ? Theme.onPrimaryContainer : Theme.soft }
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
                    flood.play(dark ? "#111216" : "#F3F1EC", x, y)
                }
            }
            Rectangle {                                              // you
                anchors.verticalCenter: parent.verticalCenter
                width: 40; height: 40; radius: 20
                color: Theme.primaryContainer
                Text { anchors.centerIn: parent; text: home.you ? home.you.charAt(0) : home.t("你", "Y")
                       color: Theme.onPrimaryContainer; font.pixelSize: 16; font.weight: Font.Medium }
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

        // ==== 总览: the wallet, and what its front card holds =========================================
        Page {
            index: 0
            anchors.fill: parent
            Flickable {
                id: overview
                objectName: "overview"
                anchors.fill: parent
                contentHeight: Math.max(walletCol.y + walletCol.height + 40, side.y + side.height) + home.margin
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: Bar {}
                readonly property real cardW: Math.max(340, Math.min(460, (width - 2 * home.margin) * 0.37))

                Item {
                    id: walletCol
                    x: home.margin + 8
                    y: 22
                    width: overview.cardW
                    height: homeWallet.height
                    Wallet {
                        id: homeWallet
                        objectName: "homeWallet"
                        width: parent.width
                        height: implicitHeight
                        cards: home.walletCards
                        current: home.walletPick
                        peek: Math.max(40, Math.min(58, (overview.height - 70 - width / Theme.cardRatio)
                                                         / Math.max(1, home.walletCards.length - 1)))
                        onPicked: (i) => home.walletPick = i
                    }
                }

                Column {
                    id: side
                    x: walletCol.x + walletCol.width + 64
                    y: 10
                    width: overview.width - x - home.margin
                    spacing: 0

                    Text {                                                   // the greeting, as a page's display line
                        width: parent.width
                        text: (P.greeting || home.t("你好", "Hello")) + (home.you ? home.t("，", ", ") + home.you : "")
                        textFormat: Text.PlainText
                        color: Theme.text
                        font.pixelSize: Theme.display
                        font.letterSpacing: -0.6
                        elide: Text.ElideRight
                    }
                    Text {
                        width: parent.width
                        topPadding: 12
                        text: home.summary()
                        textFormat: Text.PlainText
                        wrapMode: Text.Wrap
                        color: Theme.soft
                        font.pixelSize: 16
                        lineHeight: 1.45
                    }
                    Item { width: 1; height: 28 }

                    Surface {
                        id: detail
                        width: parent.width
                        height: detailBody.implicitHeight + 64
                        Column {
                            id: detailBody
                            x: 32; y: 30
                            width: parent.width - 64

                            // -- this node --
                            Column {
                                visible: home.picked.what === "node"
                                width: parent.width
                                Heading { width: parent.width; text: home.t("这台电脑上的节点", "The node on this computer") }
                                Text { width: parent.width; topPadding: 8; wrapMode: Text.Wrap; textFormat: Text.PlainText
                                       text: home.node.work || home.t("现在没有事在做。", "Nothing under way.")
                                       color: Theme.soft; font.pixelSize: Theme.body; lineHeight: 1.4 }
                                Column {
                                    topPadding: 12
                                    spacing: 8
                                    Repeater {
                                        model: (home.node.recent || []).slice(0, 3)
                                        Row {
                                            required property var modelData
                                            spacing: 10
                                            Dot { anchors.verticalCenter: parent.verticalCenter
                                                  color: modelData.status === "done" ? Theme.good
                                                         : (modelData.status === "failed" ? Theme.sign : Theme.warm) }
                                            Text { text: modelData.title; color: Theme.text; font.pixelSize: Theme.body
                                                   elide: Text.ElideRight; width: detailBody.width - 30; textFormat: Text.PlainText }
                                        }
                                    }
                                }
                                Item { width: 1; height: 14 }
                                KV { k: home.t("状态", "State"); v: home.node.since || home.node.stateWords || "—" }
                                KV { k: home.t("角色", "Roles"); v: home.node.roles || "—" }
                                KV { k: home.t("连接", "Carriers"); v: home.node.carriers || "—" }
                                KV { k: home.t("钥匙", "Key"); v: home.node.did || "—"; mono: true }
                                KV { k: home.t("门的规则", "Gate rules"); last: true
                                     v: (home.node.rules || []).length > 0
                                        ? home.t((home.node.rules || []).length + " 份生效", (home.node.rules || []).length + " in force")
                                        : home.t("内置的门", "The built-in gates") }
                                Row {
                                    topPadding: 12
                                    spacing: 8
                                    Repeater { model: ["#8FE3C7", "#F2D173", "#F49C7C"]
                                               Dot { required property string modelData; color: modelData; width: 7; height: 7
                                                     anchors.verticalCenter: parent.verticalCenter } }
                                    Text { text: home.t("卡上亮着的点：今天你在电脑前的每一刻钟，颜色是那时的天色",
                                                        "Lit dots on the card: each quarter-hour you were here today, in that hour's sky")
                                           color: Theme.faint; font.pixelSize: Theme.meta; leftPadding: 4; textFormat: Text.PlainText
                                           width: detailBody.width - 40; elide: Text.ElideRight }
                                }
                                Row {
                                    topPadding: 22
                                    spacing: 10
                                    Act { text: home.t("去对话", "Open the conversation"); onClicked: home.page = 1 }
                                    Act { primary: false; text: home.t("编辑规则", "Edit the rules"); onClicked: home.page = 3 }
                                    Act { primary: false; text: home.t("复制钥匙", "Copy the key"); visible: !!home.node.did
                                          onClicked: P.copy(home.node.did) }
                                }
                            }

                            // -- what waits on you --
                            Column {
                                visible: home.picked.what === "waiting"
                                width: parent.width
                                Heading { width: parent.width; text: home.t("等你", "Waiting on you") }
                                Item { width: 1; height: 12; visible: !!P.homeCard }
                                Rectangle {
                                    visible: !!P.homeCard
                                    width: kindText.implicitWidth + 20; height: 26; radius: 8
                                    color: Theme.tinted(Theme.seal, Theme.dark ? 0.28 : 0.12)
                                    Text { id: kindText; anchors.centerIn: parent; text: P.homeCard ? P.homeCard.label : ""
                                           color: Theme.sign; font.pixelSize: Theme.meta; font.weight: Font.Medium }
                                }
                                Text { width: parent.width; topPadding: 14; wrapMode: Text.Wrap; textFormat: Text.PlainText
                                       text: P.homeCard ? P.homeCard.says
                                                        : home.t("现在没有事等你。要你回答或签名的事一来，这张卡就变成朱砂色。",
                                                                 "Nothing waits on you now. When something needs your answer or your signature, this card turns cinnabar.")
                                       color: P.homeCard ? Theme.text : Theme.soft
                                       font.family: P.homeCard ? Theme.serif : Theme.sans
                                       font.pixelSize: P.homeCard ? Theme.lead : Theme.body; lineHeight: 1.55 }
                                Item { width: 1; height: 10; visible: !!P.homeCard && (P.homeCard.details || []).length > 0 }
                                Repeater {
                                    model: P.homeCard ? P.homeCard.details || [] : []
                                    KV { required property var modelData; required property int index
                                         k: modelData[0]; v: modelData[1]; mono: /^[~\/]|:\/\//.test(modelData[1])
                                         last: index === (P.homeCard.details || []).length - 1 }
                                }
                                Text { visible: P.waiting > 0; topPadding: 10; textFormat: Text.PlainText
                                       text: home.t("答完这件，还有 " + P.waiting + " 件。", P.waiting + " more after this one.")
                                       color: Theme.faint; font.pixelSize: Theme.meta }
                                Row {
                                    topPadding: 22
                                    spacing: 10
                                    Act { text: P.homeCard ? home.t("去回答", "Answer it") : home.t("去对话", "Open the conversation")
                                          onClicked: home.page = 1 }
                                }
                            }

                            // -- one of your devices --
                            Column {
                                visible: home.picked.what === "device"
                                width: parent.width
                                Heading { width: parent.width; text: home.pickedDevice.name || home.t("未命名的设备", "Unnamed device") }
                                Text { topPadding: 6; text: home.stateWords(home.pickedDevice); color: home.stateColor(home.pickedDevice.state)
                                       font.pixelSize: Theme.body; textFormat: Text.PlainText }
                                Item { width: 1; height: 14 }
                                KV { k: home.t("身份", "Standing")
                                     v: home.pickedDevice.owner ? home.t("主人：能接纳新设备", "Owner: can admit devices")
                                                                : home.t("成员：由主人接纳", "Member: admitted by the owner") }
                                KV { k: home.t("路径", "Path")
                                     v: home.pickedDevice.state === "online" ? home.t("WebRTC 直连，逐条签名", "Direct over WebRTC, every message signed") : "—" }
                                KV { k: home.t("钥匙", "Key"); v: home.pickedDevice.did || "—"; mono: true; last: true }
                                Column {
                                    width: parent.width
                                    topPadding: 12
                                    spacing: 6
                                    Repeater {
                                        model: (home.dev.notes || []).filter(n => n.from === home.pickedDevice.did).slice(0, 3)
                                        Text { required property var modelData; width: detailBody.width; wrapMode: Text.Wrap
                                               text: "“" + modelData.text + "”"; color: Theme.soft; font.family: Theme.serif
                                               font.pixelSize: Theme.voice - 1; textFormat: Text.PlainText }
                                    }
                                }
                                Row {
                                    topPadding: 18
                                    spacing: 10
                                    Act { text: home.dev.online ? home.t("下线", "Go offline") : home.t("上线", "Go online")
                                          primary: !home.dev.online
                                          enabled: !!home.dev.ready && (home.dev.online || (home.dev.signal || "").length > 0)
                                          onClicked: home.dev.online ? P.goOffline() : P.goOnline(home.dev.signal) }
                                    Act { primary: false; text: home.t("管理设备", "Manage devices"); onClicked: home.page = 2 }
                                    Act { primary: false; text: home.t("复制钥匙", "Copy the key"); visible: !!home.pickedDevice.did
                                          onClicked: P.copy(home.pickedDevice.did) }
                                }
                            }

                            // -- an empty slot --
                            Column {
                                visible: home.picked.what === "add"
                                width: parent.width
                                spacing: 0
                                Heading { width: parent.width; text: home.t("加一台设备", "Add a device") }
                                Text { width: parent.width; topPadding: 8; wrapMode: Text.Wrap; textFormat: Text.PlainText
                                       color: Theme.soft; font.pixelSize: Theme.body; lineHeight: 1.45
                                       text: home.t("手机、平板、另一台电脑都可以。它只认你签过名的设备，不会有陌生人进来。",
                                                    "A phone, a tablet or another computer. Only devices you sign for get in; no stranger can.") }
                                Column {
                                    topPadding: 16
                                    spacing: 14
                                    Repeater {
                                        model: [home.t("写一封请帖（要你的签名口令）", "Write an invitation (it takes your signing passphrase)"),
                                                home.t("在另一台设备上打开它，点同意", "Open it on the other device and agree"),
                                                home.t("两边的六位数一样，就接纳", "If both show the same six digits, admit it")]
                                        Row {
                                            required property string modelData
                                            required property int index
                                            spacing: 14
                                            Rectangle { width: 28; height: 28; radius: 14; color: Theme.primaryContainer
                                                        Text { anchors.centerIn: parent; text: index + 1; color: Theme.onPrimaryContainer
                                                               font.pixelSize: 13; font.weight: Font.Medium } }
                                            Text { anchors.verticalCenter: parent.verticalCenter; text: modelData; color: Theme.text
                                                   font.pixelSize: Theme.body; textFormat: Text.PlainText }
                                        }
                                    }
                                }
                                Row {
                                    topPadding: 22
                                    Act { text: home.t("写请帖", "Write an invitation"); onClicked: home.page = 2 }
                                }
                            }
                        }
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
                font.pixelSize: Theme.headline
                font.letterSpacing: -0.3
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
                        radius: 20
                        color: Theme.panel
                        border.color: Theme.dark ? Theme.hairline : "transparent"
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
                        radius: 20
                        topRightRadius: msg.opens ? 6 : 20
                        color: Theme.primaryContainer
                        Accessible.role: Accessible.StaticText
                        Accessible.name: home.t("你：", "You: ") + msg.words
                        Text {
                            id: mineText
                            x: 15; y: 9
                            width: bubble.width - 30
                            text: msg.words
                            textFormat: Text.PlainText
                            wrapMode: Text.Wrap
                            color: Theme.onPrimaryContainer
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
                    radius: 24
                    color: Theme.panel
                    border.color: Theme.dark ? Theme.hairline : "transparent"
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
                    border.color: say.focused ? Theme.primary : (Theme.dark ? Theme.hairline : "transparent")
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
                contentHeight: devBody.height + home.margin + 8
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: Bar {}
                Item {
                    id: devBody
                    x: home.margin
                    y: 10
                    width: devPage.width - 2 * home.margin
                    readonly property real colW: (width - 24) / 2
                    height: Math.max(leftCol.height, rightCol.height)

                    Column {
                        id: leftCol
                        width: devBody.colW
                        spacing: 24

                        // -- invite a device --
                        Card {
                            id: inviteCard
                            width: parent.width
                            visible: (home.dev.role || "owner") === "owner"
                            height: 92 + inviteBody.height
                            title: home.t("请一台设备进来", "Bring in a device")
                            chip: home.pair.phase === "waiting" ? home.t("还剩 ", "") + Math.floor((home.pair.left || 0) / 60) + ":"
                                                                  + home.two((home.pair.left || 0) % 60) + home.t("", " left") : ""
                            Column {
                                id: inviteBody
                                width: parent.width
                                spacing: 12
                                // writing it
                                Column {
                                    visible: ["idle", "ended", undefined].indexOf(home.pair.phase) >= 0
                                    width: parent.width
                                    spacing: 12
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
                                    spacing: 20
                                    QrCode { visible: (home.pair.qr || []).length > 0; rows: home.pair.qr || []; width: 180; height: 180 }
                                    Column {
                                        width: parent.width - ((home.pair.qr || []).length > 0 ? 200 : 0)
                                        spacing: 12
                                        Words { width: parent.width
                                                text: (home.pair.qr || []).length > 0
                                                      ? home.t("用另一台设备扫这个二维码，或打开下面的链接。", "Scan this with the other device, or open the link below.")
                                                      : home.t("在另一台 Studio 的「用请帖加入」里粘贴这段请帖。", "Paste this invitation into \"Join with an invitation\" on the other Studio.") }
                                        Rectangle {
                                            width: parent.width; height: 64; radius: 12; color: Theme.well
                                            Text { anchors.fill: parent; anchors.margins: 10; wrapMode: Text.WrapAnywhere
                                                   text: home.pair.link || home.pair.code_text || ""; color: Theme.soft
                                                   font.pixelSize: 11; elide: Text.ElideRight; maximumLineCount: 3
                                                   font.family: Theme.mono }
                                        }
                                        Row {
                                            spacing: 10
                                            Act { text: home.t("复制", "Copy"); onClicked: P.copy(home.pair.link || home.pair.code_text || "") }
                                            Act { primary: false; text: home.t("收回", "Withdraw"); onClicked: P.withdrawInvite() }
                                        }
                                        Row {
                                            spacing: 8
                                            Dot { color: Theme.warm; anchors.verticalCenter: parent.verticalCenter }
                                            Words { text: home.t("等对方打开并点同意…", "Waiting for the other device to open it and agree…") }
                                        }
                                    }
                                }
                                // comparing the codes: the card being issued
                                Column {
                                    visible: home.pair.phase === "compare" || home.pair.phase === "admitting"
                                    width: parent.width
                                    spacing: 18
                                    Words { width: parent.width; color: Theme.text
                                            text: home.t("两块屏幕上的数字一样吗？一样就接纳；不一样说明有人在两台设备之间，取消即可。",
                                                         "Do both screens show the same number? If they match, admit it; if not, someone is between the two devices: cancel.") }
                                    WalletCard {
                                        width: Math.min(parent.width, 380); height: width / Theme.cardRatio
                                        focusPolicy: Qt.NoFocus; enabled: false
                                        glaze: Theme.glazeFor(home.pair.did || "new"); key: home.pair.did || "new"; panel: true
                                        title: home.pair.name || home.t("新设备", "New device")
                                        code: (home.pair.code || "").slice(0, 3) + " " + (home.pair.code || "").slice(3)
                                        number: home.tail(home.pair.did); label: home.t("待接纳", "Pending")
                                    }
                                    Row {
                                        spacing: 10
                                        Act { text: home.t("一样，接纳", "They match, admit"); enabled: home.pair.phase === "compare"
                                              onClicked: P.decidePairing(true) }
                                        Act { primary: false; text: home.t("不一样，取消", "They differ, cancel")
                                              enabled: home.pair.phase === "compare"; onClicked: P.decidePairing(false) }
                                    }
                                }
                                // done: the seal on the new card
                                Column {
                                    visible: home.pair.phase === "done"
                                    width: parent.width
                                    spacing: 18
                                    WalletCard {
                                        width: Math.min(parent.width, 380); height: width / Theme.cardRatio
                                        focusPolicy: Qt.NoFocus; enabled: false
                                        glaze: Theme.glazeFor(home.pair.did || "new"); key: home.pair.did || "new"; panel: true
                                        title: home.pair.name || home.t("新设备", "New device"); stamped: true
                                        note: home.t("已接纳", "Admitted"); number: home.tail(home.pair.did); label: home.t("成员", "Member")
                                    }
                                    Act { primary: false; text: home.t("再请一台", "Invite another"); onClicked: P.withdrawInvite() }
                                }
                            }
                        }

                        // -- join with an invitation --
                        Card {
                            width: parent.width
                            height: 92 + joinBody.height
                            title: home.t("用请帖加入", "Join with an invitation")
                            Column {
                                id: joinBody
                                width: parent.width
                                spacing: 12
                                Column {
                                    visible: ["idle", "ended", undefined].indexOf(home.join.phase) >= 0
                                    width: parent.width
                                    spacing: 12
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
                                        topPadding: 6
                                        spacing: 10
                                        Act { text: home.join.phase === "joining" ? home.t("正在连接…", "Connecting…") : home.t("同意加入", "Agree and join")
                                              enabled: home.join.phase === "card"; onClicked: P.agreeInvite() }
                                        Act { primary: false; text: home.t("不了", "No"); onClicked: P.resetJoin() }
                                    }
                                }
                                Column {
                                    visible: home.join.phase === "compare"
                                    width: parent.width
                                    spacing: 16
                                    Words { width: parent.width; color: Theme.text
                                            text: home.t("对照另一块屏幕上的六位数，在那边确认。", "Compare with the six digits on the other screen, and confirm there.") }
                                    WalletCard {
                                        width: Math.min(parent.width, 380); height: width / Theme.cardRatio
                                        focusPolicy: Qt.NoFocus; enabled: false
                                        glaze: Theme.glazeFor(home.node.did || "me"); key: home.node.did || "me"; panel: true
                                        title: home.dev.deviceName || home.t("这台电脑", "This computer")
                                        code: (home.join.code || "").slice(0, 3) + " " + (home.join.code || "").slice(3)
                                        number: home.tail(home.node.did); label: home.t("等接纳", "Awaiting")
                                    }
                                }
                                Words {
                                    visible: home.join.phase === "done"
                                    width: parent.width; color: Theme.good
                                    text: home.t("已加入「", "Joined \"") + (home.join.mesh || "") + home.t("」。在右边上线，就能见到你的其他设备。", "\". Go online on the right to meet your other devices.")
                                }
                            }
                        }
                    }

                    // -- my devices: the same wallet --
                    Column {
                        id: rightCol
                        x: devBody.colW + 24
                        width: devBody.colW
                        spacing: 24
                        Card {
                            width: parent.width
                            height: devCol.implicitHeight + 92
                            title: home.t("我的设备", "My devices")
                            chip: home.dev.online ? home.t("在线", "Online") : home.t("不在线", "Offline")
                            chipLive: !!home.dev.online
                            Column {
                                id: devCol
                                width: parent.width
                                spacing: 18
                                Words { width: parent.width
                                        text: !home.dev.ready ? home.t("这台电脑还不在网格里：先写一封请帖，或用请帖加入。",
                                                                      "This computer is not in a mesh yet: write an invitation, or join with one.")
                                                             : home.t("上线后，这台电脑和你其他在线的设备经 WebRTC 直连；每条消息都由发送的设备签名。关掉 Studio 就下线。",
                                                                      "Online, this computer links directly to your other online devices over WebRTC; every message is signed by the device that sent it. Close Studio and it goes offline.") }
                                Act { text: home.dev.online ? home.t("下线", "Go offline") : home.t("上线", "Go online")
                                      primary: !home.dev.online
                                      enabled: !!home.dev.ready && (home.dev.online || (home.dev.signal || sigUrl.text).length > 0)
                                      onClicked: home.dev.online ? P.goOffline() : P.goOnline(home.dev.signal || sigUrl.text) }
                                Item {
                                    width: parent.width
                                    height: devWallet.height + 26
                                    visible: home.deviceCards.length > 0
                                    Wallet {
                                        id: devWallet
                                        objectName: "devicesWallet"
                                        y: 8
                                        x: (parent.width - width) / 2
                                        width: Math.min(parent.width - 24, 400)
                                        height: implicitHeight
                                        cards: home.deviceCards
                                        current: home.devPick
                                        peek: 52
                                        onPicked: (i) => home.devPick = i
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
                                                     + home.t("：", ": ") + modelData.text
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
                    y: 10
                    width: about.width - 2 * home.margin
                    spacing: 0

                    Surface {                                                 // the hero: words at the left, a hand of cards
                        width: parent.width
                        height: 380
                        Column {
                            x: 52; y: 60
                            width: parent.width * 0.47
                            spacing: 18
                            Text { width: parent.width; wrapMode: Text.Wrap; color: Theme.text; textFormat: Text.PlainText
                                   font.pixelSize: 40; font.letterSpacing: -0.6; lineHeight: 1.12
                                   text: home.t("你的设备，自成一网。\n你的伙伴，先问后做。", "Your devices, one mesh.\nA companion that asks first.") }
                            Text { width: parent.width; wrapMode: Text.Wrap; color: Theme.soft; textFormat: Text.PlainText
                                   font.pixelSize: 16; lineHeight: 1.5
                                   text: home.t("secdogie 只跑在你自己的设备上：没有中心服务器，每一步先过两道苏格拉底门，改不回来的事一定等你签名。",
                                                "secdogie runs only on your own devices: no central server, every step through two Socratic gates, and nothing irreversible without your signature.") }
                            Row {
                                topPadding: 8
                                spacing: 10
                                Act { text: home.t("开始对话", "Start a conversation"); onClicked: home.page = 1 }
                                Act { primary: false; text: home.t("加一台设备", "Add a device"); onClicked: home.page = 2 }
                            }
                        }
                        Item {                                                // three cards, fanned like a hand
                            id: fan
                            x: parent.width * 0.55
                            y: 56
                            width: parent.width * 0.38
                            height: 280
                            readonly property real cw: Math.min(300, width * 0.86)
                            WalletCard { x: 0; y: 66; width: fan.cw; height: width / Theme.cardRatio; rotation: -11
                                         enabled: false; focusPolicy: Qt.NoFocus; front: false
                                         glaze: "qingci"; key: "tablet"; panel: true; title: home.t("平板", "Tablet")
                                         number: "J7hZ1"; label: home.t("成员", "Member"); note: home.t("在线 · 直连", "Online · direct"); live: true }
                            WalletCard { x: fan.width * 0.12; y: 30; width: fan.cw; height: width / Theme.cardRatio; rotation: -3
                                         enabled: false; focusPolicy: Qt.NoFocus; front: false
                                         glaze: "jilan"; key: "phone"; panel: true; title: home.t("手机", "Phone")
                                         number: "8Q3vN"; label: home.t("成员", "Member"); figure: "38 ms" }
                            WalletCard { x: fan.width * 0.24; y: 0; width: fan.cw; height: width / Theme.cardRatio; rotation: 6
                                         enabled: false; focusPolicy: Qt.NoFocus
                                         kind: "dots"; title: "secdogie"; figure: home.hm(home.node.uptime || 0)
                                         number: home.tail(home.node.did); quarters: P.dayQuarters || []; now: P.quarterNow === undefined ? -1 : P.quarterNow }
                        }
                        Text {                                                // the inscription, read top to bottom
                            visible: Theme.lang !== "en"
                            anchors.right: parent.right
                            anchors.rightMargin: 30
                            y: 40
                            width: 20
                            text: "吾之器物　自成一网"
                            wrapMode: Text.WrapAnywhere
                            color: Theme.faint
                            font.family: Theme.serif
                            font.pixelSize: 15
                            lineHeight: 1.05
                        }
                    }

                    Item { width: 1; height: 64 }
                    Text { text: home.t("它由四部分组成", "Four parts"); color: Theme.text; font.pixelSize: Theme.headline
                           font.letterSpacing: -0.3; textFormat: Text.PlainText }
                    Item { width: 1; height: 28 }
                    Row {
                        width: parent.width
                        spacing: 32
                        Repeater {
                            model: [
                                ["壹", home.t("你", "You"), home.t("签名口令只在你手里。高风险的事，任何模式下都要你亲自确认。",
                                                                 "The signing passphrase is yours alone. Anything high-risk needs you, in every mode.")],
                                ["贰", "Studio", home.t("这个应用：桌面上的伙伴，和你对话，替你配对设备，也是你签名的地方。",
                                                       "This app: the companion on your desktop. It talks with you, pairs your devices, and is where you sign.")],
                                ["叁", home.t("节点", "Node"), home.t("在后台做事的那部分：签名日志、两道门、决策模型。关掉 Studio 它就停。",
                                                                    "The part that works: a signed journal, two gates, the decision model. It stops when Studio closes.")],
                                ["肆", home.t("网格", "Mesh"), home.t("只由你的设备组成：点同意，经 WebRTC 直连，两屏对码，主人签接纳书。",
                                                                    "Only your own devices: agree, link directly over WebRTC, compare codes, the owner signs an admission.")]
                            ]
                            Column {
                                required property var modelData
                                width: (aboutBody.width - 3 * 32) / 4
                                spacing: 10
                                Rectangle { width: parent.width; height: 1; color: Theme.hairline }
                                Text { topPadding: 14; text: modelData[0]; color: Theme.seal; font.family: Theme.serif
                                       font.pixelSize: 34; font.weight: Font.Bold }
                                Text { text: modelData[1]; color: Theme.text; font.pixelSize: Theme.lead; font.weight: Font.Medium
                                       textFormat: Text.PlainText }
                                Text { width: parent.width; text: modelData[2]; color: Theme.soft; font.pixelSize: Theme.body
                                       wrapMode: Text.Wrap; lineHeight: 1.5; textFormat: Text.PlainText }
                            }
                        }
                    }

                    Item { width: 1; height: 64 }
                    Text { text: home.t("它擅长什么", "What it is good at"); color: Theme.text; font.pixelSize: Theme.headline
                           font.letterSpacing: -0.3; textFormat: Text.PlainText }
                    Item { width: 1; height: 24 }
                    Grid {
                        id: strengths
                        width: parent.width
                        columns: 2
                        columnSpacing: 48
                        rowSpacing: 20
                        Repeater {
                            model: [
                                home.t("只在你的设备上：没有中心服务器，没有公开节点，规则从不离开你的节点。",
                                       "Only your devices: no central server, no public nodes, and your rules never leave your nodes."),
                                home.t("先问后做：每个新目标都过苏格拉底门，有疑问先问你一个问题。",
                                       "Ask before acting: every new goal goes through the Socratic gate, which asks you when in doubt."),
                                home.t("高风险必须你签名：没有开关能关掉这一条。",
                                       "High-risk always needs your signature: no switch turns that off."),
                                home.t("结构而非像素：用无障碍树看界面，不读屏幕上的画面。",
                                       "Structure, not pixels: it reads the accessibility tree, never the picture on the screen."),
                                home.t("处处可验：身份是 DID，日志签名成链，设备之间的每条消息都签名。",
                                       "Verifiable everywhere: DIDs for identity, a signed hash-chained journal, every message between devices signed."),
                                home.t("开机不自启，不常驻：关掉 Studio，节点就停，什么也不在后台跑。",
                                       "Nothing at login, nothing left running: close Studio and the node stops.")
                            ]
                            Row {
                                required property string modelData
                                width: (strengths.width - 48) / 2
                                spacing: 14
                                Dot { y: 7; color: Theme.primary }
                                Text { width: parent.width - 24; text: modelData; color: Theme.text; font.pixelSize: Theme.body
                                       wrapMode: Text.Wrap; lineHeight: 1.5; textFormat: Text.PlainText }
                            }
                        }
                    }
                    Item { width: 1; height: 24 }
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
