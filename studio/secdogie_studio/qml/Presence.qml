// The companion: a faint, breathing light in the corner of the screen.
//
// Most of the time that light is all there is. A whisper fades in beside it
// for a few seconds when it has something to say in passing. A card rises
// above it when the work needs you (one question, one signature, one thing it
// would like to remember) and fades away once answered. Hover the light and
// whatever it was holding for you comes up. Click it to tell it something;
// right-click (or the Menu key) for its inner workings.
//
// Night, soft light: deep ink, warm white text, no alarm red. The design
// system (tokens, type scale, motion, states) is written down in
// studio/DESIGN.md. All timing and state come from the bridge `P`
// (secdogie_studio/app.py); this file only draws.
import QtQuick
import QtQuick.Window
import QtQuick.Controls

Window {
    id: win
    flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
    color: "transparent"
    title: "secdogie"
    visible: true

    // ---- tokens -----------------------------------------------------------------------------
    QtObject {
        id: ink
        // the surface every sheet is made of, and the marks on it
        readonly property color card: Qt.rgba(0.071, 0.078, 0.106, 0.96)
        readonly property color well: Qt.rgba(1, 1, 1, 0.04)        // an inset area: the rules editor
        readonly property color hairline: Qt.rgba(1, 1, 1, 0.08)    // edges and dividers that carry nothing
        readonly property color edge: Qt.rgba(1, 1, 1, 0.34)        // a control's boundary: 3:1 on the card
        readonly property color wash: Qt.rgba(1, 1, 1, 0.06)        // a quiet control, hovered
        readonly property color press: Qt.rgba(1, 1, 1, 0.11)       // ... and pressed
        // text, tinted warm rather than grey (on the card: 14.7, 9.0 and 5.5 to 1)
        readonly property color text: "#ECE6DC"
        readonly property color soft: "#BDB5AA"
        readonly property color faint: "#948C82"
        // moods; calm is also where the keyboard is
        readonly property color calm: "#8FB6FF"
        readonly property color think: "#B9A7FF"
        readonly property color warm: "#F2C27F"
        readonly property color sign: "#F3A983"
        readonly property color focus: calm
    }
    QtObject {
        id: typo                      // three steps on a 1.2 scale
        readonly property int meta: 12
        readonly property int body: 14
        readonly property int lead: 17
    }
    QtObject {
        id: motion
        readonly property bool still: P.still                    // the person asked for less motion
        readonly property int enter: still ? 120 : 240
        readonly property int exit: still ? 90 : 160              // leaving takes two thirds of arriving
        readonly property int touch: 120                          // hover and press
        readonly property int whisperIn: still ? 120 : 320
        readonly property int whisperOut: still ? 400 : 1100      // let go of slowly: it may be mid-read
        readonly property real rise: still ? 0 : 12               // how far a sheet travels as it fades in
    }

    function moodColor(mood) {
        switch (mood) {
        case "thinking": return ink.think
        case "waiting": return ink.warm
        case "signing": return ink.sign
        default: return ink.calm
        }
    }
    readonly property color accent: moodColor(P.mood)

    // ---- size: the light alone, a whisper, or a sheet above it -----------------------------------
    // The window is only as big as what it shows, so the rest of the desktop stays the person's.
    readonly property int pad: 20                         // room around every surface for its shadow
    readonly property int dock: pad + 84 + pad            // the light alone
    readonly property string want: (P.cardKey !== "" || P.innerOpen || P.sayOpen) ? "open"
                                   : (P.whisper !== "" ? "whisper" : "compact")
    property string shape: "compact"
    property real room: 0                                 // the height kept for the sheet above the light
    readonly property real need: Math.max(card.shownNow ? card.height : 0, talk.shownNow ? talk.height : 0,
                                          inner.shownNow ? inner.height : 0)
    onWantChanged: {
        if (want === "open" || (want === "whisper" && shape === "compact")) { shape = want; shrink.stop() }
        else shrink.restart()                             // let the fade finish before the window shrinks
    }
    onNeedChanged: if (need >= room) room = need; else shrink.restart()
    Timer {
        id: shrink
        interval: (win.shape === "whisper" ? motion.whisperOut : motion.exit) + 80
        onTriggered: { win.shape = win.want; win.room = win.need }
    }
    Component.onCompleted: { shape = want; room = need }  // a cold start may open straight onto a card
    width: shape === "compact" ? dock : 468
    height: dock + (shape === "open" ? room + 8 : 0)
    x: Screen.virtualX + Screen.desktopAvailableWidth - width - 8
    y: Screen.virtualY + Screen.desktopAvailableHeight - height - 8

    Shortcut {                                            // Esc puts away what the person opened
        sequences: [StandardKey.Cancel]
        enabled: P.sayOpen || P.innerOpen
        onActivated: P.sayOpen ? P.toggleSay() : P.toggleInner()
    }

    TextEdit { id: clipboard; visible: false; textFormat: TextEdit.PlainText }
    function copy(s) { clipboard.text = s; clipboard.selectAll(); clipboard.copy(); clipboard.text = "" }

    // ---- parts ---------------------------------------------------------------------------------
    component Shadow: Item {
        // depth, not a halo: six soft layers, each a little wider and a little lower
        id: shadow
        property real radius: 20
        property real strength: 1
        Repeater {
            model: 6
            Rectangle {
                required property int index
                readonly property real spread: index * 2.5
                readonly property real drop: 3 + index * 1.5
                anchors.fill: parent
                anchors.leftMargin: -spread
                anchors.rightMargin: -spread
                anchors.topMargin: drop - spread
                anchors.bottomMargin: -drop - spread
                radius: shadow.radius + spread
                color: Qt.rgba(0, 0, 0, 0.055 * shadow.strength)
            }
        }
    }

    component Sheet: Item {
        // the one surface every card is made of: deep ink, a hairline, a soft lift
        id: sheet
        default property alias content: face.data
        property real radius: 20
        Shadow { anchors.fill: parent; radius: sheet.radius }
        Rectangle {
            id: face
            anchors.fill: parent
            radius: sheet.radius
            color: ink.card
            border.color: ink.hairline
            Rectangle {          // a faint top light, so the sheet reads as a surface, not a hole
                anchors.fill: parent
                radius: parent.radius
                gradient: Gradient {
                    GradientStop { position: 0.0; color: Qt.rgba(1, 1, 1, 0.04) }
                    GradientStop { position: 0.3; color: "transparent" }
                }
            }
        }
    }

    component Ring: Rectangle {
        // where the keyboard is; shown only when focus came from the keyboard
        property real round: 0
        anchors.fill: parent
        anchors.margins: -4
        radius: round + 4
        color: "transparent"
        border.width: 2
        border.color: ink.focus
    }

    component Pill: Button {
        id: pill
        property bool primary: false
        property color tint: win.accent
        hoverEnabled: true
        implicitHeight: 40
        leftPadding: 20
        rightPadding: 20
        topPadding: 0
        bottomPadding: 0
        font.pixelSize: typo.body
        Keys.onReturnPressed: pill.animateClick()
        Keys.onEnterPressed: pill.animateClick()
        HoverHandler { cursorShape: Qt.PointingHandCursor }
        contentItem: Text {
            text: pill.text
            textFormat: Text.PlainText
            font: pill.font
            color: pill.primary ? Qt.lighter(pill.tint, 1.12) : ink.text
            opacity: pill.enabled ? 1 : 0.4
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            radius: height / 2
            opacity: pill.enabled ? 1 : 0.4
            color: pill.primary ? Qt.rgba(pill.tint.r, pill.tint.g, pill.tint.b,
                                          pill.down ? 0.34 : (pill.hovered ? 0.25 : 0.16))
                                : (pill.down ? ink.press : (pill.hovered ? ink.wash : "transparent"))
            border.color: pill.primary ? Qt.rgba(pill.tint.r, pill.tint.g, pill.tint.b, 0.62) : ink.edge
            Behavior on color { ColorAnimation { duration: motion.touch } }
            Ring { visible: pill.visualFocus; round: parent.radius }
        }
    }

    component Link: AbstractButton {
        // a quiet way somewhere else: text that underlines when you reach it
        id: link
        hoverEnabled: true
        implicitHeight: 32
        padding: 0
        font.pixelSize: typo.body
        Keys.onReturnPressed: link.animateClick()
        Keys.onEnterPressed: link.animateClick()
        HoverHandler { cursorShape: Qt.PointingHandCursor }
        contentItem: Text {
            text: link.text
            textFormat: Text.PlainText
            font.pixelSize: link.font.pixelSize
            font.underline: link.hovered
            color: link.hovered || link.visualFocus ? ink.text : ink.soft
            verticalAlignment: Text.AlignVCenter
        }
        background: Item { Ring { visible: link.visualFocus; round: 6 } }
    }

    component Close: AbstractButton {
        id: close
        text: "收起"                                   // its accessible name; the cross is drawn
        hoverEnabled: true
        implicitWidth: 36
        implicitHeight: 36
        padding: 0
        Keys.onReturnPressed: close.animateClick()
        Keys.onEnterPressed: close.animateClick()
        HoverHandler { cursorShape: Qt.PointingHandCursor }
        background: Rectangle {
            radius: width / 2
            color: close.down ? ink.press : (close.hovered ? ink.wash : "transparent")
            Behavior on color { ColorAnimation { duration: motion.touch } }
            Ring { visible: close.visualFocus; round: parent.radius }
        }
        contentItem: Item {
            Repeater {                                 // two 1.5 px strokes, the weight of the text beside it
                model: [45, -45]
                Rectangle {
                    required property real modelData
                    anchors.centerIn: parent
                    width: 14
                    height: 1.5
                    radius: 0.75
                    rotation: modelData
                    antialiasing: true
                    color: close.hovered || close.visualFocus ? ink.text : ink.soft
                }
            }
        }
    }

    component Tab: AbstractButton {
        id: tab
        property bool current: false
        hoverEnabled: true
        implicitHeight: 44
        padding: 0
        font.pixelSize: typo.body
        Accessible.role: Accessible.PageTab
        Accessible.selected: tab.current
        Keys.onReturnPressed: tab.animateClick()
        Keys.onEnterPressed: tab.animateClick()
        HoverHandler { cursorShape: Qt.PointingHandCursor }
        contentItem: Text {
            text: tab.text
            textFormat: Text.PlainText
            font.pixelSize: tab.font.pixelSize
            color: tab.current || tab.hovered ? ink.text : ink.soft
            verticalAlignment: Text.AlignVCenter
        }
        background: Item {
            Rectangle {                                // the open tab's mark
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 6
                width: parent.width
                height: 2
                radius: 1
                color: ink.calm
                opacity: tab.current ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: motion.touch } }
            }
            Ring { visible: tab.visualFocus; round: 6 }
        }
    }

    component Caret: Rectangle {
        // the text cursor, in the focus colour, blinking at the platform's pace
        id: caret
        property Item host
        property bool lit: true
        width: 2
        color: ink.focus
        visible: !!host && host.cursorVisible
        opacity: lit ? 1 : 0
        Timer {
            id: blink
            interval: Math.max(250, (Qt.styleHints.cursorFlashTime || 1000) / 2)
            repeat: true
            running: caret.visible && Qt.styleHints.cursorFlashTime !== 0
            onRunningChanged: caret.lit = true
            onTriggered: caret.lit = !caret.lit
        }
        Connections {
            target: caret.host
            function onCursorPositionChanged() { caret.lit = true; blink.restart() }
        }
    }

    component Bar: ScrollBar {
        id: bar
        padding: 2
        contentItem: Rectangle {
            implicitWidth: 4
            implicitHeight: 4
            radius: 2
            color: Qt.rgba(1, 1, 1, bar.pressed ? 0.42 : (bar.hovered ? 0.32 : 0.2))
            opacity: bar.size < 1.0 ? 1 : 0
        }
        background: null
    }

    component Field: FocusScope {
        // one line to type into: a label when it needs one, an example while it is empty
        id: field
        property alias text: input.text
        property string label
        property string hint
        property bool secret: false
        signal accepted
        implicitHeight: column.implicitHeight
        Column {
            id: column
            width: parent.width
            spacing: 6
            Text {
                visible: field.label !== ""
                text: field.label
                textFormat: Text.PlainText
                color: ink.soft
                font.pixelSize: typo.meta
            }
            TextField {
                id: input
                focus: true
                width: parent.width
                implicitHeight: 40
                leftPadding: 0
                rightPadding: 0
                topPadding: 0
                bottomPadding: 0
                verticalAlignment: TextInput.AlignVCenter
                color: ink.text
                placeholderText: field.hint
                placeholderTextColor: ink.faint
                selectionColor: Qt.rgba(ink.focus.r, ink.focus.g, ink.focus.b, 0.38)
                selectedTextColor: ink.text
                font.pixelSize: typo.body
                echoMode: field.secret ? TextInput.Password : TextInput.Normal
                passwordCharacter: "•"
                inputMethodHints: field.secret ? (Qt.ImhSensitiveData | Qt.ImhNoPredictiveText | Qt.ImhNoAutoUppercase)
                                               : Qt.ImhNone
                selectByMouse: true
                Accessible.name: field.label !== "" ? field.label : field.hint
                Accessible.passwordEdit: field.secret
                onAccepted: field.accepted()
                cursorDelegate: Caret { host: input }
                background: Item {
                    Rectangle {
                        anchors.bottom: parent.bottom
                        width: parent.width
                        height: input.activeFocus ? 2 : 1
                        color: input.activeFocus ? ink.focus : (input.hovered ? ink.soft : ink.edge)
                        Behavior on color { ColorAnimation { duration: motion.touch } }
                    }
                }
            }
        }
    }

    // ---- the light -------------------------------------------------------------------------------
    Item {
        id: orb
        width: 84
        height: 84
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.rightMargin: win.pad
        anchors.bottomMargin: win.pad
        activeFocusOnTab: true
        Accessible.role: Accessible.Button
        Accessible.name: "secdogie"
        Accessible.description: (P.held > 0 ? "有 " + P.held + " 件事在等你。" : "") + "点一下跟我说话，右键看看我的里面。"
        Keys.onReturnPressed: P.toggleSay()
        Keys.onEnterPressed: P.toggleSay()
        Keys.onSpacePressed: P.toggleSay()
        Keys.onMenuPressed: P.toggleInner()

        property color tint: win.accent
        Behavior on tint { ColorAnimation { duration: motion.still ? 300 : 1200; easing.type: Easing.InOutSine } }
        property real breath: 0
        readonly property real b: motion.still ? 0.5 : breath          // a still light rests half-lit
        readonly property int period: P.mood === "waiting" ? 1700 : (P.mood === "thinking" ? 2600 : 4200)
        SequentialAnimation on breath {                               // the one authored motion
            running: !motion.still
            loops: Animation.Infinite
            NumberAnimation { from: 0; to: 1; duration: orb.period; easing.type: Easing.InOutSine }
            NumberAnimation { from: 1; to: 0; duration: orb.period; easing.type: Easing.InOutSine }
        }
        opacity: P.mood === "rest" ? (hoverOrb.containsMouse || orb.activeFocus ? 0.95 : 0.5 + 0.18 * b) : 0.95
        Behavior on opacity { NumberAnimation { duration: 400 } }

        Repeater {
            model: [0.018, 0.026, 0.036, 0.05, 0.07, 0.1, 0.15, 0.24, 0.38]
            Rectangle {
                required property real modelData
                required property int index
                anchors.centerIn: parent
                width: 84 - index * 7.5 + orb.b * (9 - index)
                height: width
                radius: width / 2
                color: Qt.rgba(orb.tint.r, orb.tint.g, orb.tint.b, modelData * (0.8 + 0.2 * orb.b))
            }
        }
        Rectangle {
            anchors.centerIn: parent
            width: 16 + orb.b * 2
            height: width
            radius: width / 2
            gradient: Gradient {
                GradientStop { position: 0.0; color: Qt.lighter(orb.tint, 1.5) }
                GradientStop { position: 1.0; color: orb.tint }
            }
        }
        Rectangle {             // a quiet mark: something is held for you
            visible: P.held > 0 && P.cardKey === ""
            width: 7; height: 7; radius: 3.5
            color: ink.warm
            x: parent.width - 18; y: 14
        }
        Rectangle {             // the keyboard is on the light
            visible: orb.activeFocus
            anchors.centerIn: parent
            width: 56; height: 56; radius: 28
            color: "transparent"
            border.width: 2
            border.color: ink.focus
        }
        MouseArea {
            id: hoverOrb
            anchors.fill: parent
            hoverEnabled: true
            acceptedButtons: Qt.LeftButton | Qt.RightButton
            cursorShape: Qt.PointingHandCursor
            onEntered: P.hover()
            onClicked: (mouse) => mouse.button === Qt.RightButton ? P.toggleInner() : P.toggleSay()
        }
    }

    // ---- a whisper ---------------------------------------------------------------------------------
    Item {
        id: whisper
        property string shown: P.whisper          // keeps the last line while it fades out
        readonly property string current: P.whisper
        onCurrentChanged: if (current !== "") shown = current
        readonly property bool shownNow: P.whisper !== "" && P.cardKey === ""
        anchors.right: orb.left
        anchors.rightMargin: 4
        anchors.verticalCenter: orb.verticalCenter
        width: Math.min(300, words.implicitWidth + 32)
        height: words.implicitHeight + 20
        opacity: shownNow ? 1 : 0
        visible: opacity > 0.01
        Behavior on opacity {
            NumberAnimation {
                duration: whisper.shownNow ? motion.whisperIn : motion.whisperOut
                easing.type: whisper.shownNow ? Easing.OutExpo : Easing.InOutSine
            }
        }
        transform: Translate { x: (1 - whisper.opacity) * (motion.still ? 0 : 8) }
        Accessible.role: Accessible.StaticText
        Accessible.name: shown
        Shadow { anchors.fill: parent; radius: Math.min(parent.height / 2, 18); strength: 0.8 }
        Rectangle {
            anchors.fill: parent
            radius: Math.min(height / 2, 18)
            color: ink.card
            border.color: ink.hairline
        }
        Text {
            id: words
            anchors.centerIn: parent
            width: Math.min(implicitWidth, 268)
            text: whisper.shown
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            maximumLineCount: 4
            elide: Text.ElideRight
            color: P.whisperTone === "warm" ? ink.warm : ink.text
            font.pixelSize: typo.body
            lineHeight: 1.3
        }
    }

    // ---- a card: one question, one signature, one memory, or hello ----------------------------------
    Sheet {
        id: card
        property var c: P.card                  // keeps the last card while it fades out
        readonly property string current: P.cardKey
        onCurrentChanged: if (P.card) { c = P.card; reset() }
        Component.onCompleted: if (P.card) reset()
        readonly property bool shownNow: P.cardKey !== ""
        anchors.right: parent.right
        anchors.rightMargin: win.pad
        anchors.bottom: orb.top
        anchors.bottomMargin: 8
        width: 428
        height: body.implicitHeight + 48
        opacity: shownNow ? 1 : 0
        visible: opacity > 0.01
        Behavior on opacity {
            NumberAnimation {
                duration: card.shownNow ? motion.enter : motion.exit
                easing.type: card.shownNow ? Easing.OutExpo : Easing.InQuad
            }
        }
        transform: Translate { y: (1 - card.opacity) * motion.rise }
        Accessible.role: Accessible.Grouping
        Accessible.name: c ? c.label : ""

        property bool guard: true            // a sign card cannot be approved in its first 0.8 s
        Timer { id: guardTimer; interval: 800; onTriggered: card.guard = false }
        function reset() { guard = true; guardTimer.restart(); free.text = ""; pass1.text = ""; pass2.text = "" }
        function approve() {
            if (!approveButton.enabled) return
            P.approve(c.id, pass1.text)
            pass1.text = ""
        }

        readonly property string kind: c ? c.kind : ""
        readonly property color tint: kind === "sign" ? ink.sign : (kind === "memory" ? ink.warm : ink.calm)

        Column {
            id: body
            x: 24; y: 24
            width: parent.width - 48
            spacing: 16

            Text {                                    // what it says is the card's heading
                width: parent.width
                text: card.c ? card.c.says : ""
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                maximumLineCount: 12
                elide: Text.ElideRight
                color: ink.text
                font.pixelSize: typo.lead
                font.weight: Font.Medium
                lineHeight: 1.35
            }
            Column {                                  // what a signature commits to, or why
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
                        width: body.width
                        spacing: 12
                        Text {
                            visible: !detail.plain
                            width: 56
                            topPadding: 2
                            text: detail.modelData[0]
                            textFormat: Text.PlainText
                            color: ink.faint
                            font.pixelSize: typo.meta
                        }
                        Text {
                            width: detail.plain ? parent.width : parent.width - 68
                            text: detail.modelData[1]
                            textFormat: Text.PlainText
                            wrapMode: detail.plain ? Text.Wrap : Text.WrapAnywhere
                            color: detail.refusal ? ink.warm : (detail.plain ? ink.soft : ink.text)
                            font.pixelSize: typo.body
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
                onAccepted: if (!P.working) P.welcome(pass1.text, pass2.text)
            }
            Flow {
                width: parent.width
                spacing: 8
                Repeater {
                    model: card.kind === "ask" && card.c ? card.c.options : []
                    Pill { required property string modelData; required property int index
                           text: modelData; primary: index === 0; onClicked: P.answer(card.c.id, modelData) }
                }
                Pill { id: approveButton; visible: card.kind === "sign"; text: "签名同意"; primary: true; tint: ink.sign
                       enabled: !!card.c && !card.guard && card.c.canApprove && pass1.text.length > 0 && !P.working
                       onClicked: card.approve() }
                Pill { visible: card.kind === "sign"; text: "这次不签"; onClicked: P.deny(card.c.id) }
                Pill { visible: card.kind === "memory"; text: "记住吧"; primary: true; tint: ink.warm
                       enabled: !!card.c && card.c.canApprove; onClicked: P.remember(card.c.id, true) }
                Pill { visible: card.kind === "memory"; text: "别记"; onClicked: P.remember(card.c.id, false) }
                Pill { visible: card.kind === "welcome"; text: "生成钥匙"; primary: true; enabled: !P.working
                       onClicked: P.welcome(pass1.text, pass2.text) }
            }
            Row {                                     // or say it in your own words
                width: parent.width
                spacing: 12
                visible: card.kind === "ask" && !!card.c && card.c.freeText
                Field {
                    id: free
                    width: parent.width - reply.width - 12
                    hint: "或者直接告诉我……"
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
                color: P.working ? ink.soft : ink.warm
                font.pixelSize: typo.body
                lineHeight: 1.3
                wrapMode: Text.Wrap
            }
        }
    }

    // ---- tell it something -------------------------------------------------------------------------
    Sheet {
        id: talk
        readonly property bool shownNow: P.sayOpen && P.cardKey === ""
        anchors.right: parent.right
        anchors.rightMargin: win.pad
        anchors.bottom: orb.top
        anchors.bottomMargin: 8
        width: 428
        height: talkBody.implicitHeight + 40
        opacity: shownNow ? 1 : 0
        visible: opacity > 0.01
        Behavior on opacity {
            NumberAnimation {
                duration: talk.shownNow ? motion.enter : motion.exit
                easing.type: talk.shownNow ? Easing.OutExpo : Easing.InQuad
            }
        }
        transform: Translate { y: (1 - talk.opacity) * motion.rise }
        Accessible.role: Accessible.Grouping
        Accessible.name: "想让我做什么？"
        onShownNowChanged: if (shownNow) { say.text = ""; say.forceActiveFocus() }   // the person clicked the light
        Column {
            id: talkBody
            x: 24; y: 24; width: parent.width - 48; spacing: 12
            Text { text: "想让我做什么？"; color: ink.text; font.pixelSize: typo.lead; font.weight: Font.Medium }
            Row {
                width: parent.width
                spacing: 12
                Field { id: say; width: parent.width - hand.width - 12; hint: "比如：帮我整理这周的周报"
                        onAccepted: P.say(text) }
                Pill { id: hand; text: "交给我"; primary: true; enabled: say.text.trim().length > 0
                       onClicked: P.say(say.text) }
            }
            Link { text: "看看我的里面"; onClicked: P.toggleInner() }
        }
    }

    // ---- inside: what it is, and the rules it keeps ---------------------------------------------------
    Sheet {
        id: inner
        readonly property bool shownNow: P.innerOpen && P.cardKey === ""
        anchors.right: parent.right
        anchors.rightMargin: win.pad
        anchors.bottom: orb.top
        anchors.bottomMargin: 8
        width: 428
        height: 560
        opacity: shownNow ? 1 : 0
        visible: opacity > 0.01
        Behavior on opacity {
            NumberAnimation {
                duration: inner.shownNow ? motion.enter : motion.exit
                easing.type: inner.shownNow ? Easing.OutExpo : Easing.InQuad
            }
        }
        transform: Translate { y: (1 - inner.opacity) * motion.rise }
        Accessible.role: Accessible.Grouping
        Accessible.name: "我的里面"
        property int tab: 0
        onShownNowChanged: if (shownNow && rules.text === "") rules.text = P.rulesText

        Row {
            x: 24; y: 8; spacing: 24
            Repeater {
                model: ["此刻", "规则"]
                Tab {
                    required property string modelData
                    required property int index
                    text: modelData
                    current: inner.tab === index
                    onClicked: inner.tab = index
                }
            }
        }
        Close { anchors.right: parent.right; anchors.rightMargin: 14; y: 12; onClicked: P.toggleInner() }
        Rectangle { y: 56; width: parent.width; height: 1; color: ink.hairline }

        Item {                                                // now
            id: nowPanel
            visible: inner.tab === 0
            x: 24; y: 72; width: parent.width - 48; height: parent.height - 72 - 24
            Column {
                id: rows
                width: parent.width
                spacing: 4
                Repeater {
                    model: P.statusRows
                    Row {
                        id: status
                        required property var modelData
                        readonly property bool copyable: String(modelData[1]).indexOf("did:") === 0
                        width: rows.width
                        spacing: 12
                        Text {
                            width: 96; height: 28
                            verticalAlignment: Text.AlignVCenter
                            text: status.modelData[0]
                            textFormat: Text.PlainText
                            color: ink.faint
                            font.pixelSize: typo.meta
                        }
                        AbstractButton {                      // a key's DID: click to copy it
                            id: value
                            property bool copied: false
                            width: parent.width - 108; height: 28
                            padding: 0
                            enabled: status.copyable
                            hoverEnabled: true
                            text: status.modelData[1]
                            Accessible.name: status.copyable ? "复制" + status.modelData[0] : text
                            Keys.onReturnPressed: value.animateClick()
                            HoverHandler { cursorShape: Qt.PointingHandCursor }
                            Timer { id: copiedFor; interval: 1600; onTriggered: value.copied = false }
                            onClicked: { win.copy(text); copied = true; copiedFor.restart() }
                            contentItem: Item {
                                Text {
                                    anchors.left: parent.left
                                    anchors.right: note.visible ? note.left : parent.right
                                    anchors.rightMargin: note.visible ? 8 : 0
                                    height: parent.height
                                    verticalAlignment: Text.AlignVCenter
                                    text: value.text
                                    textFormat: Text.PlainText
                                    elide: Text.ElideMiddle
                                    color: ink.text
                                    font.pixelSize: typo.body
                                    font.features: ({ "tnum": 1 })
                                }
                                Text {
                                    id: note
                                    anchors.right: parent.right
                                    height: parent.height
                                    verticalAlignment: Text.AlignVCenter
                                    visible: value.enabled && (value.hovered || value.visualFocus || value.copied)
                                    text: value.copied ? "已复制" : "复制"
                                    color: value.copied ? ink.calm : ink.soft
                                    font.pixelSize: typo.meta
                                }
                            }
                            background: Item { Ring { visible: value.visualFocus; round: 6 } }
                        }
                    }
                }
            }
            Rectangle { id: divider; anchors.top: rows.bottom; anchors.topMargin: 12; width: parent.width; height: 1
                        color: ink.hairline }
            Text {
                anchors.top: divider.bottom
                anchors.topMargin: 12
                anchors.bottom: restBlock.top
                anchors.bottomMargin: 16
                width: parent.width
                readonly property bool empty: P.logText === ""
                text: empty ? "还没有什么可记的。点一下我，说一句话，就会记在这里。" : P.logText
                textFormat: Text.PlainText
                color: empty ? ink.faint : ink.soft
                font.pixelSize: typo.meta
                lineHeight: 1.45
                wrapMode: Text.Wrap
                clip: true
                // read from the top; once it overflows, keep the newest line in view
                verticalAlignment: contentHeight > height ? Text.AlignBottom : Text.AlignTop
            }
            Column {
                id: restBlock
                anchors.bottom: parent.bottom
                width: parent.width
                spacing: 8
                Pill { text: "让我休息"; onClicked: P.rest() }
                Text { width: parent.width; text: "窗口和节点会一起停下。再打开 Studio，我就醒了。"
                       color: ink.faint; font.pixelSize: typo.meta; wrapMode: Text.Wrap }
            }
        }

        Column {                                              // rules
            visible: inner.tab === 1
            x: 24; y: 72; width: parent.width - 48; spacing: 16
            Rectangle {
                width: parent.width; height: 200; radius: 12
                color: ink.well
                border.color: rules.activeFocus ? ink.focus : ink.hairline
                Behavior on border.color { ColorAnimation { duration: motion.touch } }
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
                        color: ink.text
                        font.pixelSize: typo.meta
                        font.family: Qt.platform.os === "osx" ? "Menlo"
                                     : (Qt.platform.os === "windows" ? "Consolas" : "monospace")
                        selectionColor: Qt.rgba(ink.focus.r, ink.focus.g, ink.focus.b, 0.38)
                        selectedTextColor: ink.text
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
                   wrapMode: Text.Wrap; color: ink.soft; font.pixelSize: typo.body; lineHeight: 1.3
                   visible: text.length > 0 }
            Text { width: parent.width; text: P.inForce; textFormat: Text.PlainText; wrapMode: Text.Wrap
                   color: ink.faint; font.pixelSize: typo.meta }
        }
    }
}
