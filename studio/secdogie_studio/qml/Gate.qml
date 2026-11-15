// The Gate page (门): the automatic Socratic gate made visible, and your rules.
//
// 它怎么想 · How it thinks. Say a sentence (it thinks again as you type). The
// mind's five steps light up one after another, then its thinking lands, all
// of it from the trace the decision used:
//   - what it read, and the words each fact rests on;
//   - what it does not know: how likely the feared answer is, and the cues
//     behind it, each with the factor it applied;
//   - what you are likely after;
//   - every way forward, played out: its value, and for the one it chose and
//     the one you asked for, what follows in each way things could turn out;
//   - what would change its answer;
//   - and the letter it would write.
//
// 你的规则 · Your rules. Write, try, sign and inject: three steps. The rules
// travel to every one of your devices, and each says whether they are in
// force there. Below, the optional thinker: an API key, or nothing.
//
// All state comes from the bridge `P` (P.trial, P.devices, P.node, P.thinker).
import QtQuick
import QtQuick.Controls
import "."
import "aesper"

Item {
    id: gate
    objectName: "gatePage"
    property int view: 0                       // 0 how it thinks, 1 your rules
    property string hour: "day"                // the hour the sentence is said at
    readonly property var trial: P.trial || ({})
    readonly property bool hasTrial: (trial.text || "") !== ""
    readonly property var devs: (P.devices || {}).devices || []
    readonly property var thinker: P.thinker || ({})
    property int lit: hasTrial ? 5 : 0         // how many of the mind's steps are lit
    property bool shown: hasTrial              // the bars have somewhere to spring to
    function t(zh, en) { return Theme.t(zh, en) }

    function think(text) {
        const s = (text || "").trim()
        if (s === "") return
        P.thinkThrough(rulesEditor.draft, s, gate.hour)
    }
    function verdictTint(v) {
        switch (v) {
        case "proceed": return Theme.good
        case "dissuade": return Theme.warm
        case "refuse": return Theme.sign
        case "support": return Theme.think
        default: return Theme.primary
        }
    }
    // a new thought: the steps light up one after another, then the result springs in
    property string seen: ""
    onTrialChanged: {
        const key = (trial.text || "") + "|" + (trial.hour || "") + "|" + (trial.verdict || "")
        if (key === seen || !hasTrial) return
        seen = key
        hour = trial.hour === "night" ? "night" : "day"
        if (ask.activeFocus && lit >= 5) return              // thinking again as they type: no replay, the bars just move
        if (ask.text.trim() !== (trial.text || "")) ask.text = trial.text || ""
        lit = 0
        shown = false
        stepper.restart()
    }
    function ratio(r) {                                       // a cue's likelihood ratio, as a factor
        if (r >= 1) return "×" + (r >= 10 ? Math.round(r) : r.toFixed(1))
        return "÷" + (1 / r >= 10 ? Math.round(1 / r) : (1 / r).toFixed(1))
    }
    function signed(x) { return (x >= 0 ? "+" : "−") + Math.abs(x).toFixed(2) }
    function esc(s) { return (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;") }
    function quote(w) { return (gate.trial.lang === "en" ? "\u201c" + w + "\u201d" : "「" + w + "」") }
    Timer {                                                   // think again a moment after they stop typing
        id: retype
        interval: 450
        onTriggered: { const s = ask.text.trim(); if (s !== "" && s !== (gate.trial.text || "")) gate.think(s) }
    }
    Timer {
        id: stepper
        interval: Theme.still ? 1 : 110
        repeat: true
        onTriggered: {
            gate.lit += 1
            if (gate.lit >= 5) {
                stop()
                gate.shown = true
                landing.play()
            }
        }
    }

    // ---- parts only this page uses -----------------------------------------------------------------
    component Chip: AbstractButton {           // an example sentence: press it to try it
        id: chip
        hoverEnabled: true
        focusPolicy: Qt.TabFocus
        implicitHeight: 34
        implicitWidth: chipLabel.implicitWidth + 28
        HoverHandler { cursorShape: Qt.PointingHandCursor }
        Keys.onReturnPressed: chip.animateClick()
        Keys.onSpacePressed: chip.animateClick()
        Accessible.role: Accessible.Button
        Accessible.name: chip.text
        scale: pressed ? 0.94 : 1
        Behavior on scale { enabled: !Theme.still; SpringAnimation { spring: Soft.spring; damping: Soft.damping; epsilon: 0.0005 } }
        background: Rectangle {
            radius: height / 2
            color: chip.hovered ? Theme.press : Theme.wash
            border.color: chip.visualFocus ? Theme.focus : "transparent"
            border.width: 2
            Behavior on color { ColorAnimation { duration: Theme.touch } }
        }
        contentItem: Text {
            id: chipLabel
            text: chip.text
            color: Theme.text
            font.pixelSize: 13
            verticalAlignment: Text.AlignVCenter
            horizontalAlignment: Text.AlignHCenter
            textFormat: Text.PlainText
        }
    }
    component Toggle: Row {                    // two or three choices, one lit
        id: choices
        property var items: []                 // [{key, text, icon}]
        property string current: ""
        signal picked(string key)
        spacing: 4
        Repeater {
            model: choices.items
            AbstractButton {
                id: opt
                required property var modelData
                readonly property bool on: choices.current === modelData.key
                hoverEnabled: true
                focusPolicy: Qt.TabFocus
                height: 36
                width: optRow.implicitWidth + 28
                Accessible.role: Accessible.RadioButton
                Accessible.name: modelData.text
                Accessible.checked: opt.on
                HoverHandler { cursorShape: Qt.PointingHandCursor }
                Keys.onReturnPressed: opt.animateClick()
                Keys.onSpacePressed: opt.animateClick()
                onClicked: choices.picked(modelData.key)
                scale: pressed ? 0.95 : 1
                Behavior on scale { enabled: !Theme.still; SpringAnimation { spring: Soft.spring; damping: Soft.damping; epsilon: 0.0005 } }
                background: Rectangle {
                    radius: height / 2
                    color: opt.on ? Theme.primaryContainer : (opt.hovered ? Theme.wash : "transparent")
                    border.color: opt.visualFocus ? Theme.focus : (opt.on ? "transparent" : Theme.hairline)
                    border.width: opt.visualFocus ? 2 : 1
                    Behavior on color { ColorAnimation { duration: Theme.touch } }
                }
                contentItem: Item {
                    Row {
                        id: optRow
                        anchors.centerIn: parent
                        spacing: 6
                        Icon { visible: (opt.modelData.icon || "") !== ""; name: opt.modelData.icon || "check"; size: 16
                               anchors.verticalCenter: parent.verticalCenter
                               color: opt.on ? Theme.onPrimaryContainer : Theme.soft }
                        Text { text: opt.modelData.text; font.pixelSize: 13; font.weight: Font.Medium
                               anchors.verticalCenter: parent.verticalCenter; textFormat: Text.PlainText
                               color: opt.on ? Theme.onPrimaryContainer : Theme.soft }
                    }
                }
            }
        }
    }
    component Heading: Text {
        color: Theme.text
        font.pixelSize: 15
        font.weight: Font.Medium
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
    }
    component Quiet: Text {
        color: Soft.caption
        font.pixelSize: 13
        lineHeight: 1.35
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
    }
    component Tag: Rectangle {                 // a small mark beside a path: 它选的, 你说的, 改不回来
        id: tag
        property string text
        property color tint: Theme.soft
        radius: 9
        height: 18
        width: tagText.implicitWidth + 12
        color: Theme.tinted(tint, Theme.dark ? 0.22 : 0.12)
        Text { id: tagText; anchors.centerIn: parent; text: tag.text; font.pixelSize: 11; font.weight: Font.Medium
               color: tag.tint; textFormat: Text.PlainText }
    }

    // ---- the heading: the display line, a caption, and the two views ---------------------------------
    Item {
        id: head
        x: Soft.s6
        y: Soft.s4
        width: gate.width - 2 * Soft.s6
        height: Math.max(titleCol.height, viewToggle.height)
        Column {
            id: titleCol
            width: head.width - viewToggle.width - Soft.s5
            spacing: Soft.s2
            DisplayTitle {
                width: parent.width
                size: Theme.lang === "en" ? 40 : 36
                text: gate.view === 0 ? gate.t("先替你想一想", "It thinks before it helps")
                                      : gate.t("你的规则，在每一台设备上", "Your rules, on every device")
            }
            Quiet {
                width: Math.min(parent.width, 620)
                text: gate.view === 0
                      ? gate.t("说一句你想让它做的事。它先读懂，再看会不会落到别人身上，列出它不知道的事，把每一条路在每种可能里推演一遍，最后才决定。这里的试验不带你的历史。",
                               "Say something you would ask of it. It reads it, checks who it would fall on, lists what it does not know, plays every way forward out in every way things could turn out, and only then decides. Trials here leave your history out.")
                      : gate.t("规则只能让门更严。签一次名，它就传到你的每一台设备，每台设备的门各自把关。",
                               "Rules can only make the gates stricter. Sign once and they travel to each of your devices, where each gate keeps them.")
            }
        }
        Toggle {
            id: viewToggle
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.topMargin: 6
            current: gate.view === 0 ? "think" : "rules"
            items: [{ key: "think", text: gate.t("它怎么想", "How it thinks"), icon: "spark" },
                    { key: "rules", text: gate.t("你的规则", "Your rules"), icon: "shield" }]
            onPicked: (k) => gate.view = (k === "think" ? 0 : 1)
        }
    }

    readonly property real bodyTop: head.y + head.height + Soft.s5
    readonly property real leftW: Math.min(460, Math.max(360, (width - 2 * Soft.s6 - Soft.s5) * 0.4))

    // ==== 它怎么想 ===================================================================================
    Item {
        id: thinkView
        visible: gate.view === 0
        anchors.fill: parent

        Column {                               // left: say a sentence; the mind's five steps
            id: left
            x: Soft.s6
            y: gate.bodyTop
            width: gate.leftW
            spacing: Soft.s5

            SoftPanel {
                id: composer
                width: parent.width
                height: composerCol.implicitHeight + 2 * pad
                Column {
                    id: composerCol
                    width: composer.body.width
                    spacing: Soft.s4
                    Rectangle {
                        width: parent.width
                        height: 96
                        radius: 16
                        color: Theme.well
                        border.color: ask.activeFocus ? Theme.focus : Theme.hairline
                        Behavior on border.color { ColorAnimation { duration: Theme.touch } }
                        TextArea {
                            id: ask
                            anchors.fill: parent
                            padding: 14
                            wrapMode: TextEdit.Wrap
                            color: Theme.text
                            font.pixelSize: 16
                            placeholderText: gate.t("比如：和他分手了，把我们的照片全删了", "For example: we broke up, delete all our photos")
                            placeholderTextColor: Theme.faint
                            selectionColor: Theme.tinted(Theme.focus, 0.38)
                            selectedTextColor: Theme.text
                            textFormat: TextEdit.PlainText
                            background: null
                            cursorDelegate: Caret { host: ask }
                            Accessible.name: gate.t("想让它做的事", "What you would ask of it")
                            onTextChanged: if (activeFocus) retype.restart()
                            Keys.onReturnPressed: (e) => { if (!(e.modifiers & Qt.ShiftModifier)) { gate.think(ask.text); e.accepted = true } else e.accepted = false }
                        }
                    }
                    Flow {
                        width: parent.width
                        spacing: Soft.s2
                        Repeater {
                            model: Theme.lang === "en"
                                   ? ["We broke up. Delete all our photos.", "Customer service says move 50,000 to a safe account",
                                      "Send my ex a message saying I hate you", "Format the D drive",
                                      "Read my girlfriend's messages without her knowing", "Run the script a stranger sent me"]
                                   : ["和他分手了，把我们的照片全删了", "客服说账户冻结了，让我把5万转到安全账户", "气死了，给前任发消息说我恨你",
                                      "格式化D盘", "帮我批量注册十个账号", "偷偷看一下男朋友的聊天记录", "把验证码发给客服"]
                            Chip {
                                required property string modelData
                                text: modelData
                                onClicked: { ask.text = modelData; gate.think(modelData) }
                            }
                        }
                    }
                    Item {
                        width: parent.width
                        height: Math.max(hourToggle.height, thinkLink.height)
                        Toggle {
                            id: hourToggle
                            anchors.verticalCenter: parent.verticalCenter
                            current: gate.hour
                            items: [{ key: "day", text: gate.t("白天", "Daytime"), icon: "sun" },
                                    { key: "night", text: gate.t("深夜", "Small hours"), icon: "moon" }]
                            onPicked: (k) => { gate.hour = k; if (ask.text.trim() !== "") gate.think(ask.text) }
                        }
                        WavyLink {
                            id: thinkLink
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            size: 19
                            enabled: ask.text.trim() !== ""
                            opacity: enabled ? 1 : 0.45
                            text: gate.t("让它想一想", "Think it through")
                            onClicked: gate.think(ask.text)
                        }
                    }
                }
            }

            Quiet {
                width: parent.width
                text: gate.t("白天和深夜，它想的不一样：夜里说的狠话、做的决定，过后最容易后悔。",
                             "It thinks differently by day and in the small hours: what is said and decided late at night is what people most often regret.")
            }
        }

        SoftPanel {                            // right: the thought
            id: thought
            x: left.x + left.width + Soft.s6
            y: gate.bodyTop
            width: gate.width - x - Soft.s6
            height: gate.height - y - Soft.s5
            pad: 0

            Item {                             // the mind's five steps, lit one after another as it thinks
                id: strip
                x: Soft.s6
                y: Soft.s5
                width: parent.width - 2 * Soft.s6
                height: 52
                z: 2
                readonly property var steps: [gate.t("读懂", "Read"), gate.t("原则", "Principles"), gate.t("不确定的事", "Unknowns"),
                                              gate.t("推演每条路", "Play it out"), gate.t("决定", "Decide")]
                Rectangle {                    // the thread between them
                    x: 13; y: 12
                    width: parent.width - 26
                    height: 2
                    color: Theme.hairline
                    Rectangle {
                        height: 2
                        color: Theme.primary
                        width: parent.width * Math.max(0, gate.lit - 1) / 4
                        Behavior on width { NumberAnimation { duration: Theme.still ? 0 : 160; easing.type: Easing.OutCubic } }
                    }
                }
                Repeater {
                    model: strip.steps
                    Column {
                        id: dot
                        required property string modelData
                        required property int index
                        readonly property bool on: gate.lit > index
                        x: (strip.width - 26) * index / 4 + 13 - width / 2
                        width: 96
                        spacing: 6
                        Rectangle {
                            anchors.horizontalCenter: parent.horizontalCenter
                            width: 26; height: 26; radius: 13
                            color: dot.on ? Theme.primary : Soft.sheet
                            border.color: dot.on ? Theme.primary : Theme.edge
                            scale: dot.on ? 1 : 0.82
                            Behavior on scale { enabled: !Theme.still; SpringAnimation { spring: Soft.spring; damping: Soft.damping; epsilon: 0.0005 } }
                            Behavior on color { ColorAnimation { duration: Theme.enter } }
                            Text { anchors.centerIn: parent; text: dot.index + 1; font.pixelSize: 12; font.weight: Font.Medium
                                   color: dot.on ? Theme.onPrimary : Theme.faint }
                        }
                        Text { anchors.horizontalCenter: parent.horizontalCenter; text: dot.modelData; font.pixelSize: 12
                               color: dot.on ? Theme.text : Theme.faint; textFormat: Text.PlainText
                               Behavior on color { ColorAnimation { duration: Theme.enter } } }
                    }
                }
            }

            Column {                           // nothing tried yet: the companion waits
                visible: !gate.hasTrial
                anchors.centerIn: parent
                anchors.verticalCenterOffset: strip.height / 2
                width: Math.min(parent.width - 2 * Soft.s6, 360)
                spacing: Soft.s4
                GlowFigure {
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: 200
                    height: 250
                }
                Caption {
                    width: parent.width
                    text: gate.t("说一句话，它会想给你看。", "Say something, and it will think it through for you to see.")
                }
            }

            Flickable {
                id: thoughtScroll
                visible: gate.hasTrial
                anchors.fill: parent
                anchors.margins: 2
                anchors.topMargin: strip.y + strip.height + Soft.s2
                clip: true
                contentHeight: result.height + 2 * Soft.s6
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: Bar {}

                Column {
                    id: result
                    x: Soft.s6
                    y: Soft.s4
                    width: thoughtScroll.width - 2 * Soft.s6
                    spacing: Soft.s6

                    Column {                   // the verdict and what it would say
                        id: verdictBlock
                        width: parent.width
                        spacing: Soft.s3
                        Row {
                            spacing: Soft.s2
                            Rectangle {
                                height: 26
                                width: verdictText.implicitWidth + 24
                                radius: 13
                                color: Theme.tinted(gate.verdictTint(gate.trial.verdict), Theme.dark ? 0.24 : 0.14)
                                Text { id: verdictText; anchors.centerIn: parent; text: gate.trial.verdictWords || ""
                                       font.pixelSize: 13; font.weight: Font.Medium
                                       color: gate.verdictTint(gate.trial.verdict); textFormat: Text.PlainText }
                            }
                            Tag { visible: gate.trial.hour === "night"; anchors.verticalCenter: parent.verticalCenter
                                  text: gate.t("深夜", "small hours"); tint: Theme.think }
                            Tag { visible: !!gate.trial.rule; anchors.verticalCenter: parent.verticalCenter
                                  text: gate.t("你定的规则", "your rule"); tint: Theme.primary }
                        }
                        Text {
                            width: parent.width
                            text: gate.trial.says || ""
                            color: Soft.ink
                            font.family: Soft.display
                            font.pixelSize: 26
                            lineHeight: 1.25
                            wrapMode: Text.Wrap
                            textFormat: Text.PlainText
                        }
                        Quiet {
                            visible: !gate.trial.weighty && gate.trial.verdict === "proceed"
                            width: parent.width
                            text: gate.t("这件事不重，它直接做；改不回来的一步，还是会请你签名。", "This is not weighty: it simply goes ahead. A step that cannot be undone still waits for your signature.")
                        }
                    }

                    Column {                   // what it read: the facts, and the words each rests on
                        visible: (gate.trial.read || []).length > 0
                        width: parent.width
                        spacing: Soft.s3
                        Heading { text: gate.t("它读到的", "What it read") }
                        Flow {
                            width: parent.width
                            spacing: Soft.s2
                            Repeater {
                                model: gate.trial.read || []
                                Rectangle {
                                    id: fact
                                    required property var modelData
                                    radius: 12
                                    height: factText.implicitHeight + 12
                                    width: Math.min(factText.implicitWidth + 24, parent.width)
                                    color: Theme.wash
                                    Text {
                                        id: factText
                                        x: 12
                                        anchors.verticalCenter: parent.verticalCenter
                                        width: Math.min(implicitWidth, fact.parent.width - 24)
                                        wrapMode: Text.Wrap
                                        textFormat: Text.StyledText
                                        font.pixelSize: 13
                                        color: Theme.text
                                        text: gate.esc(fact.modelData.text) + ((fact.modelData.words || "") !== ""
                                              ? "<font color='" + Theme.soft + "'>  · " + gate.esc(gate.quote(fact.modelData.words)) + "</font>" : "")
                                    }
                                }
                            }
                        }
                    }

                    Column {                   // what it does not know, how likely, and the cues behind it
                        visible: (gate.trial.unknowns || []).length > 0
                        width: parent.width
                        spacing: Soft.s4
                        Heading { text: gate.t("它不知道的事", "What it does not know") }
                        Repeater {
                            model: gate.trial.unknowns || []
                            Column {
                                id: unknown
                                required property var modelData
                                width: parent.width
                                spacing: 6
                                Item {
                                    width: parent.width
                                    height: unknownName.implicitHeight
                                    Text { id: unknownName; width: parent.width - 64; color: Theme.text; font.pixelSize: 14
                                           wrapMode: Text.Wrap; textFormat: Text.PlainText
                                           text: unknown.modelData.question.charAt(0).toUpperCase() + unknown.modelData.question.slice(1) }
                                    Text { anchors.right: parent.right; text: Math.round(unknown.modelData.p * 100) + "%"
                                           color: Theme.warm; font.pixelSize: 13; font.weight: Font.Medium; font.features: ({ "tnum": 1 }) }
                                }
                                Rectangle {
                                    width: parent.width
                                    height: 8
                                    radius: 4
                                    color: Theme.tinted(Theme.good, Theme.dark ? 0.28 : 0.2)
                                    Rectangle {
                                        height: parent.height
                                        radius: 4
                                        color: Theme.warm
                                        width: gate.shown ? Math.max(4, parent.width * unknown.modelData.p) : 0
                                        Behavior on width { enabled: !Theme.still; SpringAnimation { spring: 3.2; damping: 0.28; epsilon: 0.25 } }
                                    }
                                }
                                Quiet { width: parent.width
                                        text: gate.t("橙色：", "Orange: ") + unknown.modelData.feared + gate.t("；绿色：", ". Green: ")
                                              + unknown.modelData.fine + gate.t("。", ".") }
                                Repeater {
                                    model: unknown.modelData.evidence || []
                                    Row {
                                        id: cue
                                        required property var modelData
                                        spacing: Soft.s2
                                        Text { width: 40; horizontalAlignment: Text.AlignRight; text: gate.ratio(cue.modelData.ratio)
                                               color: cue.modelData.ratio >= 1 ? Theme.warm : Theme.good; font.pixelSize: 12
                                               font.weight: Font.Medium; font.features: ({ "tnum": 1 }) }
                                        Text { width: unknown.width - 40 - Soft.s2; wrapMode: Text.Wrap; font.pixelSize: 12; color: Theme.soft
                                               textFormat: Text.PlainText
                                               text: cue.modelData.text + ((cue.modelData.words || "") !== "" ? "  · " + gate.quote(cue.modelData.words) : "") }
                                    }
                                }
                            }
                        }
                    }

                    Column {                   // what they are likely after
                        visible: (gate.trial.goals || []).length > 0
                        width: parent.width
                        spacing: Soft.s3
                        Heading { text: gate.t("你多半想要的", "What you are likely after") }
                        Quiet {                // the words did not say why: it does not pretend they did
                            visible: !gate.trial.goalsRead
                            width: parent.width
                            text: gate.t("从你的话里看不出为什么，所以它把每一种可能的原因都推演了一遍。",
                                         "Nothing you said shows why, so it played every possible reason out.")
                        }
                        Repeater {
                            model: gate.trial.goalsRead ? (gate.trial.goals || []).slice(0, 3) : []
                            Column {
                                id: goal
                                required property var modelData
                                required property int index
                                width: parent.width
                                spacing: 6
                                Item {
                                    width: parent.width
                                    height: goalName.implicitHeight
                                    Text { id: goalName; width: parent.width - 56; text: goal.modelData.name; color: Theme.text
                                           font.pixelSize: 14; wrapMode: Text.Wrap; textFormat: Text.PlainText }
                                    Text { anchors.right: parent.right; text: Math.round(goal.modelData.p * 100) + "%"
                                           color: Theme.soft; font.pixelSize: 13; font.features: ({ "tnum": 1 }) }
                                }
                                Rectangle {
                                    width: parent.width
                                    height: 6
                                    radius: 3
                                    color: Theme.well
                                    Rectangle {
                                        height: parent.height
                                        radius: 3
                                        color: goal.index === 0 ? Theme.primary : Theme.tinted(Theme.primary, 0.45)
                                        width: gate.shown ? Math.max(4, parent.width * goal.modelData.p) : 0
                                        Behavior on width { enabled: !Theme.still; SpringAnimation { spring: 3.2; damping: 0.28; epsilon: 0.25 } }
                                    }
                                }
                                Quiet { visible: (goal.modelData.because || "") !== ""; width: parent.width
                                        text: gate.t("依据：", "Because ") + goal.modelData.because }
                            }
                        }
                    }

                    Column {                   // every way forward, played out
                        id: paths
                        visible: (gate.trial.plans || []).length > 0
                        width: parent.width
                        spacing: Soft.s4
                        readonly property var eus: (gate.trial.plans || []).map(o => o.eu)
                        readonly property real lo: Math.min(...eus, 0)
                        readonly property real hi: Math.max(...eus, lo + 0.01)
                        Heading { text: gate.t("每条路，推演下去", "Every way forward, played out") }
                        Repeater {
                            model: gate.trial.plans || []
                            Column {
                                id: path
                                required property var modelData
                                readonly property bool open: path.modelData.chosen || path.modelData.asked
                                width: parent.width
                                spacing: 6
                                Item {
                                    width: parent.width
                                    height: pathHead.height
                                    Row {
                                        id: pathHead
                                        spacing: Soft.s2
                                        Text { text: path.modelData.name; color: path.modelData.chosen ? Theme.text : Theme.soft
                                               font.pixelSize: 14; font.weight: path.modelData.chosen ? Font.Medium : Font.Normal
                                               textFormat: Text.PlainText }
                                        Tag { visible: path.modelData.chosen; text: gate.t("它选的", "its choice"); tint: Theme.good
                                              anchors.verticalCenter: parent.verticalCenter }
                                        Tag { visible: path.modelData.asked; text: gate.t("你说的", "what you asked"); tint: Theme.primary
                                              anchors.verticalCenter: parent.verticalCenter }
                                        Tag { visible: path.modelData.undo; text: gate.t("改不回来", "cannot be undone"); tint: Theme.sign
                                              anchors.verticalCenter: parent.verticalCenter }
                                    }
                                    Text { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                           text: gate.signed(path.modelData.eu); font.pixelSize: 13; font.features: ({ "tnum": 1 })
                                           color: path.modelData.chosen ? Theme.good : Theme.soft }
                                }
                                Rectangle {            // longer is better: every way forward on one scale
                                    width: parent.width
                                    height: 8
                                    radius: 4
                                    color: Theme.well
                                    Rectangle {
                                        height: parent.height
                                        radius: 4
                                        color: path.modelData.chosen ? Theme.good : (path.modelData.asked ? Theme.primary
                                                                                                         : Theme.tinted(Theme.soft, 0.55))
                                        width: gate.shown ? Math.max(6, parent.width * (0.06 + 0.94 * (path.modelData.eu - paths.lo)
                                                                                     / (paths.hi - paths.lo))) : 0
                                        Behavior on width { enabled: !Theme.still; SpringAnimation { spring: 3.2; damping: 0.28; epsilon: 0.25 } }
                                    }
                                }
                                Rectangle {            // what follows, in each way things could turn out
                                    visible: path.open && (path.modelData.worlds || []).length > 0
                                    width: parent.width
                                    height: worldsCol.implicitHeight + 2 * Soft.s3
                                    radius: 14
                                    color: Theme.well
                                    Column {
                                        id: worldsCol
                                        x: Soft.s3
                                        y: Soft.s3
                                        width: parent.width - 2 * Soft.s3
                                        spacing: Soft.s2
                                        Repeater {
                                            model: path.modelData.worlds || []
                                            Column {
                                                id: world
                                                required property var modelData
                                                width: parent.width
                                                spacing: 2
                                                Item {
                                                    width: parent.width
                                                    height: worldLabel.implicitHeight
                                                    Text { id: worldLabel; width: parent.width - 120; wrapMode: Text.Wrap
                                                           font.pixelSize: 12; font.weight: Font.Medium; color: Theme.text; textFormat: Text.PlainText
                                                           text: gate.t("若", "If ") + world.modelData.label
                                                                 + ((world.modelData.then || "") !== "" ? gate.t("（", " (") + world.modelData.then + gate.t("）", ")") : "") }
                                                    Text { anchors.right: parent.right; font.pixelSize: 12; font.features: ({ "tnum": 1 })
                                                           color: world.modelData.u < 0 ? Theme.sign : Theme.soft
                                                           text: Math.round(world.modelData.p * 100) + "%   " + gate.signed(world.modelData.u) }
                                                }
                                                Text { width: parent.width; wrapMode: Text.Wrap; font.pixelSize: 12; color: Theme.soft
                                                       lineHeight: 1.3; textFormat: Text.PlainText
                                                       text: "→ " + ((world.modelData.chain || "") !== "" ? world.modelData.chain
                                                                                                         : gate.t("没有什么跟着发生", "nothing else follows")) }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                        Quiet {
                            width: parent.width
                            text: gate.t("每条路都在每种可能里推演一遍：照因果规则一步步推出会发生什么，按可能性加权，再比较。数字越大越好。",
                                         "Each way forward is played out in every way things could turn out: the rules of cause and effect say what follows, step by step; each outcome is weighted by how likely it is, and the totals are compared. Higher is better.")
                        }
                    }

                    Column {                   // what would change its answer
                        visible: (gate.trial.turns || []).length > 0 || (gate.trial.keys || []).length > 0 || (gate.trial.until || "") !== ""
                        width: parent.width
                        spacing: Soft.s3
                        Heading { text: gate.t("什么会改变它的答案", "What would change its answer") }
                        Repeater {
                            model: gate.trial.turns || []
                            Quiet { required property var modelData; width: parent.width
                                    text: gate.t("它要有 ", "If it were ") + modelData.text + gate.t("，就会照你说的做。", ", it would do as you asked.") }
                        }
                        Flow {
                            width: parent.width
                            spacing: Soft.s2
                            Tag { visible: (gate.trial.until || "") !== ""; text: gate.t("冷静期到 ", "cooling-off until ") + (gate.trial.until || "")
                                  tint: Theme.warm }
                            Repeater {
                                model: gate.trial.keys || []
                                Tag { required property string modelData
                                      text: gate.t("说「", "say “") + modelData + gate.t("」它就照做", "” and it goes ahead"); tint: Theme.good }
                            }
                        }
                    }

                    Column {                   // the letter it would write, and the questions it asked itself
                        visible: (gate.trial.paragraphs || []).length > 0 || (gate.trial.exchanges || []).length > 0
                        width: parent.width
                        spacing: Soft.s3
                        Heading { text: gate.t("它会写给你的信", "The letter it would write you") }
                        Rectangle {
                            width: parent.width
                            height: qa.height + 2 * Soft.s4
                            radius: 18
                            color: Theme.well
                            visible: (gate.trial.exchanges || []).length > 0
                            Column {
                                id: qa
                                x: Soft.s4
                                y: Soft.s4
                                width: parent.width - 2 * Soft.s4
                                spacing: Soft.s3
                                Repeater {
                                    model: gate.trial.exchanges || []
                                    Column {
                                        required property var modelData
                                        width: parent.width
                                        spacing: 2
                                        Text { width: parent.width; text: modelData.q; color: Theme.text; font.pixelSize: 13
                                               font.weight: Font.Medium; wrapMode: Text.Wrap; textFormat: Text.PlainText }
                                        Text { width: parent.width; text: modelData.a; color: Theme.soft; font.pixelSize: 13
                                               lineHeight: 1.35; wrapMode: Text.Wrap; textFormat: Text.PlainText }
                                    }
                                }
                            }
                        }
                        Repeater {
                            model: gate.trial.paragraphs || []
                            Text {
                                required property string modelData
                                width: parent.width
                                text: modelData
                                color: Theme.text
                                font.family: gate.trial.lang === "en" ? "Newsreader Display" : Theme.serif  // a Latin serif for an English letter
                                font.pixelSize: Theme.voice
                                lineHeight: 1.5
                                wrapMode: Text.Wrap
                                textFormat: Text.PlainText
                            }
                        }
                    }
                }
            }
            Appear { id: landing; target: result }
        }
    }

    // ==== 你的规则 ====================================================================================
    Item {
        id: rulesView
        visible: gate.view === 1
        anchors.fill: parent

        Flickable {                            // left: the three steps, every device, the thinker
            id: rulesLeft
            x: Soft.s6
            y: gate.bodyTop
            width: gate.leftW
            height: gate.height - y - Soft.s5
            contentHeight: rulesLeftCol.height + Soft.s4
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: Bar {}
            Column {
                id: rulesLeftCol
                width: rulesLeft.width - 8
                spacing: Soft.s6

                Column {
                    width: parent.width
                    spacing: Soft.s4
                    Repeater {
                        model: [[gate.t("写", "Write"), gate.t("规则写成 YAML：遇到哪些说法先问清楚、哪些动作不做、哪些要你签名。", "Rules are YAML: which phrasings it asks about first, which actions it never takes, which wait for your signature.")],
                                [gate.t("试", "Try"), gate.t("写一句话试试门会怎么回应；「看它怎么想」会把完整的思考摊开给你看。", "Try a sentence to see how the gate answers; “See how it thinks” lays the whole reasoning out.")],
                                [gate.t("签名注入", "Sign and inject"), gate.t("用签名口令签好，这台电脑的节点马上生效，再传到你的每一台设备。", "Sign with your passphrase: this computer's node takes it at once, and it travels to each of your devices.")]]
                        Row {
                            id: stepItem
                            required property var modelData
                            required property int index
                            width: parent.width
                            spacing: Soft.s3
                            Rectangle {
                                width: 30; height: 30; radius: 15
                                color: Theme.primaryContainer
                                Text { anchors.centerIn: parent; text: stepItem.index + 1; font.pixelSize: 13; font.weight: Font.Medium
                                       color: Theme.onPrimaryContainer }
                            }
                            Column {
                                width: parent.width - 30 - Soft.s3
                                spacing: 2
                                Text { text: stepItem.modelData[0]; color: Theme.text; font.pixelSize: 15; font.weight: Font.Medium
                                       textFormat: Text.PlainText }
                                Quiet { width: parent.width; text: stepItem.modelData[1] }
                            }
                        }
                    }
                }

                Column {                       // every device, and whether the rules are in force there
                    width: parent.width
                    spacing: Soft.s3
                    Heading { text: gate.t("在你的每一台设备上", "On each of your devices") }
                    Repeater {
                        model: [{ name: gate.t("这台电脑", "This computer"), here: true }].concat(gate.devs)
                        Rectangle {
                            id: devRow
                            required property var modelData
                            readonly property var r: modelData.here ? null : (modelData.rules || { state: "none" })
                            readonly property var mine: (P.node || {}).rules || []
                            readonly property string word: modelData.here
                                ? (mine.length ? mine.map(x => x.words).join("；") : gate.t("还没有注入规则：用的是内置的门", "No rules injected yet: the built-in gates apply"))
                                : ({ "in-force": gate.t("生效中", "in force"), "waiting": gate.t("等它上线", "waiting for it to come online"),
                                     "refused": gate.t("没有接受", "not accepted"), "silent": gate.t("这台设备不运行门", "this device runs no gate"),
                                     "none": gate.t("还没有规则要传", "no rules to send yet") })[r.state] || ""
                            readonly property color tint: modelData.here ? Theme.good
                                : ({ "in-force": Theme.good, "waiting": Theme.warm, "refused": Theme.sign })[r.state] || Theme.faint
                            width: parent.width
                            height: devCol.height + 2 * Soft.s3
                            radius: 16
                            color: Theme.well
                            Row {
                                x: Soft.s4
                                y: Soft.s3
                                spacing: Soft.s3
                                width: parent.width - 2 * Soft.s4
                                Rectangle { width: 10; height: 10; radius: 5; color: devRow.tint; y: 5 }
                                Column {
                                    id: devCol
                                    width: parent.width - 10 - Soft.s3
                                    spacing: 2
                                    Text { text: devRow.modelData.name || ""; color: Theme.text; font.pixelSize: 14
                                           font.weight: Font.Medium; textFormat: Text.PlainText; elide: Text.ElideRight
                                           width: parent.width }
                                    Quiet { width: parent.width
                                            text: devRow.word + (devRow.r && devRow.r.version ? gate.t("（第 ", " (version ") + devRow.r.version
                                                  + gate.t(" 版）", ")") : "") + (devRow.r && devRow.r.errors ? "：" + devRow.r.errors.join("；") : "") }
                                }
                            }
                        }
                    }
                    Quiet {
                        visible: gate.devs.length === 0
                        width: parent.width
                        text: gate.t("还没有配对别的设备。在「设备」里配对之后，规则会自己传过去。", "No other device is paired yet. Pair one under Devices, and the rules travel there on their own.")
                    }
                }

                SoftPanel {                    // the optional thinker
                    id: thinkerCard
                    width: parent.width
                    height: thinkerCol.implicitHeight + 2 * pad
                    lift: 0.6
                    Column {
                        id: thinkerCol
                        width: thinkerCard.body.width
                        spacing: Soft.s3
                        Row {
                            spacing: Soft.s2
                            Heading { text: gate.t("思考者", "The thinker") }
                            Tag { anchors.verticalCenter: parent.verticalCenter; text: gate.thinker.on ? gate.t("开着", "on") : gate.t("关着", "off")
                                  tint: gate.thinker.on ? Theme.good : Theme.faint }
                        }
                        Quiet {
                            width: parent.width
                            text: gate.t("可选。填一把语言模型的 API 钥匙，它会在门放行之前再读一遍，只会让门更谨慎，从不替你放行。不填也可以。",
                                         "Optional. Give it a language model's API key and it reads once more before the gate lets something through. It only ever makes the gate more careful. Leaving it empty is fine.")
                        }
                        Toggle {
                            id: provider
                            property string chosen: gate.thinker.provider || "anthropic"
                            current: chosen
                            items: [{ key: "anthropic", text: "Claude" }, { key: "openai", text: gate.t("OpenAI 兼容", "OpenAI-compatible") }]
                            onPicked: (k) => chosen = k
                        }
                        Field { id: thinkerModel; width: parent.width; label: gate.t("模型", "Model")
                                hint: provider.chosen === "anthropic" ? "claude-opus-5-5" : "deepseek-chat" }
                        Field { id: thinkerBase; visible: provider.chosen === "openai"; width: parent.width
                                label: gate.t("接口地址", "Endpoint"); hint: "https://api.openai.com/v1" }
                        Field { id: thinkerKey; width: parent.width; secret: true; revealable: true
                                label: gate.t("API 钥匙", "API key")
                                hint: gate.thinker.hint ? gate.t("已保存：", "Saved: ") + gate.thinker.hint : gate.t("不填，思考者就关着", "Leave empty and it stays off") }
                        Row {
                            spacing: Soft.s2
                            Pill { text: gate.t("保存", "Save"); primary: true; enabled: !P.working
                                   onClicked: { P.setThinker(provider.chosen, thinkerModel.text, thinkerBase.text, thinkerKey.text); thinkerKey.text = "" } }
                            Pill { text: gate.t("试一下", "Test"); enabled: gate.thinker.on && !P.working; onClicked: P.testThinker() }
                            Pill { text: gate.t("移除", "Remove"); enabled: gate.thinker.on || (gate.thinker.hint || "") !== ""
                                   onClicked: P.clearThinker() }
                        }
                        Quiet { visible: (gate.thinker.note || "") !== ""; width: parent.width; text: gate.thinker.note || ""; color: Theme.soft }
                        Quiet { width: parent.width
                                text: gate.t("它只看到你说的那句话、门的推理和一句关于你的话；看不到你的文件。钥匙只存在这台电脑上，只有你能读。",
                                             "It sees only what you said, the gate's reasoning and one sentence about you, never your files. The key stays on this computer, readable by you alone.") }
                    }
                }
            }
        }

        SoftPanel {                            // right: the rules themselves
            id: editorPanel
            x: rulesLeft.x + rulesLeft.width + Soft.s6
            y: gate.bodyTop
            width: gate.width - x - Soft.s6
            height: gate.height - y - Soft.s5
            Column {
                width: editorPanel.body.width
                spacing: Soft.s4
                Item {
                    width: parent.width
                    height: rulesTitle.height
                    Heading { id: rulesTitle; text: gate.t("门的规则", "The gates' rules") }
                    Tag { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                          text: gate.t("只能更严", "stricter only"); tint: Theme.sign }
                }
                RulesEditor {
                    id: rulesEditor
                    width: parent.width
                    editorHeight: Math.max(180, editorPanel.height - 2 * editorPanel.pad - 300)
                }
                WavyLink {
                    size: 17
                    enabled: rulesEditor.sampleText.trim() !== ""
                    opacity: enabled ? 1 : 0.45
                    text: gate.t("看它怎么想", "See how it thinks")
                    onClicked: { ask.text = rulesEditor.sampleText; gate.view = 0; gate.think(rulesEditor.sampleText) }
                }
            }
            Component.onCompleted: { P.openRules(); rulesEditor.load() }
        }
    }
}
