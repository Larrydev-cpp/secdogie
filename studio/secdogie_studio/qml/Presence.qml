// The companion: a faint, breathing light in the corner of the screen.
//
// Most of the time that light is all there is. A whisper fades in beside it
// for a few seconds when it has something to say in passing. A card rises
// above it when the work needs you (one question, one signature, one thing it
// would like to remember) and fades away once answered. Hover the light and
// whatever it was holding for you comes up. Click it to tell it something;
// right-click for its inner workings.
//
// Night, soft light: deep ink, warm white text, no alarm red. All timing and
// state come from the bridge `P` (secdogie_studio/app.py); this file only draws.
import QtQuick
import QtQuick.Window
import QtQuick.Controls

Window {
    id: win
    flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
    color: "transparent"
    title: "secdogie"
    visible: true

    // ---- palette ---------------------------------------------------------------------------
    QtObject {
        id: ink
        readonly property color text: "#ECE6DC"
        readonly property color soft: "#A9A7B3"
        readonly property color faint: "#6F7282"
        readonly property color card: Qt.rgba(0.070, 0.078, 0.106, 0.95)
        readonly property color raised: Qt.rgba(1, 1, 1, 0.035)
        readonly property color line: Qt.rgba(1, 1, 1, 0.075)
        readonly property color calm: "#8FB6FF"
        readonly property color think: "#B9A7FF"
        readonly property color warm: "#F2C27F"
        readonly property color sign: "#F3A983"
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

    // ---- size: the light alone, a whisper, or a card ----------------------------------------
    readonly property string want: (P.cardKey !== "" || P.innerOpen || P.sayOpen) ? "open"
                                   : (P.whisper !== "" ? "whisper" : "compact")
    property string shape: "compact"
    onWantChanged: {
        if (want === "open" || (want === "whisper" && shape === "compact")) { shape = want; shrink.stop() }
        else shrink.restart()                       // let the fade finish before the window shrinks
    }
    Timer { id: shrink; interval: 650; onTriggered: win.shape = win.want }
    Component.onCompleted: shape = want             // a cold start may open straight onto a card
    width: shape === "compact" ? 168 : 468
    height: shape === "open" ? 720 : 168
    x: Screen.virtualX + Screen.desktopAvailableWidth - width - 8
    y: Screen.virtualY + Screen.desktopAvailableHeight - height - 8

    // ---- small parts ---------------------------------------------------------------------------
    component Pill: Rectangle {
        id: pill
        property string text
        property bool primary: false
        property bool live: true
        property color tint: win.accent
        signal clicked
        implicitWidth: label.implicitWidth + 30
        implicitHeight: 34
        radius: 17
        opacity: live ? 1 : 0.32
        color: primary ? Qt.rgba(tint.r, tint.g, tint.b, hover.containsMouse && live ? 0.26 : 0.16)
                       : (hover.containsMouse && live ? Qt.rgba(1, 1, 1, 0.07) : "transparent")
        border.color: primary ? Qt.rgba(tint.r, tint.g, tint.b, 0.55) : Qt.rgba(1, 1, 1, 0.15)
        Behavior on color { ColorAnimation { duration: 160 } }
        Behavior on opacity { NumberAnimation { duration: 220 } }
        Text {
            id: label
            anchors.centerIn: parent
            text: pill.text
            color: pill.primary ? Qt.lighter(pill.tint, 1.15) : ink.text
            font.pixelSize: 14
        }
        MouseArea {
            id: hover
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: pill.live ? Qt.PointingHandCursor : Qt.ArrowCursor
            onClicked: if (pill.live) pill.clicked()
        }
    }

    component Field: Item {
        id: field
        property alias text: input.text
        property string hint
        property bool secret: false
        signal accepted
        implicitHeight: 38
        TextInput {
            id: input
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            color: ink.text
            font.pixelSize: 15
            echoMode: field.secret ? TextInput.Password : TextInput.Normal
            passwordCharacter: "•"
            selectionColor: Qt.rgba(win.accent.r, win.accent.g, win.accent.b, 0.35)
            selectByMouse: true
            clip: true
            onAccepted: field.accepted()
        }
        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: field.hint
            visible: input.text.length === 0
            color: ink.faint
            font.pixelSize: 14
        }
        Rectangle {
            anchors.bottom: parent.bottom
            width: parent.width
            height: 1
            color: input.activeFocus ? Qt.rgba(win.accent.r, win.accent.g, win.accent.b, 0.6) : Qt.rgba(1, 1, 1, 0.12)
            Behavior on color { ColorAnimation { duration: 200 } }
        }
    }

    component Sheet: Rectangle {
        // the shared look of every card: deep ink, a hairline, a soft lift
        radius: 22
        color: ink.card
        border.color: ink.line
        Rectangle {          // a faint top light, so the card reads as a surface, not a hole
            anchors.fill: parent
            radius: parent.radius
            gradient: Gradient {
                GradientStop { position: 0.0; color: Qt.rgba(1, 1, 1, 0.045) }
                GradientStop { position: 0.35; color: "transparent" }
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
        anchors.rightMargin: 42
        anchors.bottomMargin: 42
        property color tint: win.accent
        Behavior on tint { ColorAnimation { duration: 1200; easing.type: Easing.InOutSine } }
        property real breath: 0
        readonly property int period: P.mood === "waiting" ? 1700 : (P.mood === "thinking" ? 2600 : 4200)
        SequentialAnimation on breath {
            loops: Animation.Infinite
            NumberAnimation { from: 0; to: 1; duration: orb.period; easing.type: Easing.InOutSine }
            NumberAnimation { from: 1; to: 0; duration: orb.period; easing.type: Easing.InOutSine }
        }
        opacity: P.mood === "rest" ? (hoverOrb.containsMouse ? 0.95 : 0.5 + 0.18 * breath) : 0.95
        Behavior on opacity { NumberAnimation { duration: 600 } }

        Repeater {
            model: [0.018, 0.026, 0.036, 0.05, 0.07, 0.1, 0.15, 0.24, 0.38]
            Rectangle {
                required property real modelData
                required property int index
                anchors.centerIn: parent
                width: 84 - index * 7.5 + orb.breath * (9 - index)
                height: width
                radius: width / 2
                color: Qt.rgba(orb.tint.r, orb.tint.g, orb.tint.b, modelData * (0.8 + 0.2 * orb.breath))
            }
        }
        Rectangle {
            anchors.centerIn: parent
            width: 16 + orb.breath * 2
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
    Rectangle {
        id: whisper
        property string shown: P.whisper          // keeps the last line while it fades out
        readonly property string current: P.whisper
        onCurrentChanged: if (current !== "") shown = current
        readonly property bool shownNow: P.whisper !== "" && P.cardKey === ""
        anchors.right: orb.left
        anchors.rightMargin: 2
        anchors.verticalCenter: orb.verticalCenter
        width: Math.min(300, words.implicitWidth + 34)
        height: words.implicitHeight + 20
        radius: Math.min(height / 2, 18)
        color: ink.card
        border.color: ink.line
        opacity: shownNow ? 1 : 0
        visible: opacity > 0.01
        Behavior on opacity { NumberAnimation { duration: whisper.shownNow ? 520 : 1300; easing.type: Easing.OutCubic } }
        transform: Translate { x: (1 - whisper.opacity) * 10 }
        Text {
            id: words
            anchors.centerIn: parent
            width: Math.min(implicitWidth, 266)
            text: whisper.shown
            wrapMode: Text.Wrap
            color: P.whisperTone === "warm" ? ink.warm : ink.text
            font.pixelSize: 14
            lineHeight: 1.25
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
        anchors.rightMargin: 22
        anchors.bottom: orb.top
        anchors.bottomMargin: 6
        width: 424
        height: body.implicitHeight + 44
        opacity: shownNow ? 1 : 0
        visible: opacity > 0.01
        Behavior on opacity { NumberAnimation { duration: card.shownNow ? 460 : 340; easing.type: card.shownNow ? Easing.OutCubic : Easing.InCubic } }
        transform: Translate { y: (1 - card.opacity) * 16 }

        property bool guard: true            // a sign card cannot be approved in its first 0.8 s
        Timer { id: guardTimer; interval: 800; onTriggered: card.guard = false }
        function reset() { guard = true; guardTimer.restart(); free.text = ""; pass1.text = ""; pass2.text = "" }

        readonly property string kind: c ? c.kind : ""
        readonly property color tint: kind === "sign" ? ink.sign : (kind === "memory" ? ink.warm : ink.calm)

        Column {
            id: body
            x: 24; y: 22
            width: parent.width - 48
            spacing: 16

            Row {
                spacing: 9
                Rectangle { width: 7; height: 7; radius: 3.5; color: card.tint; anchors.verticalCenter: parent.verticalCenter }
                Text { text: card.c ? card.c.label : ""; color: ink.soft; font.pixelSize: 12; font.letterSpacing: 1.6 }
            }
            Text {
                width: parent.width
                text: card.c ? card.c.says : ""
                wrapMode: Text.Wrap
                color: ink.text
                font.pixelSize: 17
                lineHeight: 1.35
            }
            Column {                                  // what a signature commits to, or why
                width: parent.width
                spacing: 6
                visible: !!card.c && card.c.details.length > 0
                Repeater {
                    model: card.c ? card.c.details : []
                    Row {
                        required property var modelData
                        width: body.width
                        spacing: 12
                        Text { visible: modelData[0] !== ""; width: 64; text: modelData[0]; color: ink.faint; font.pixelSize: 12 }
                        Text {
                            width: modelData[0] !== "" ? parent.width - 76 : parent.width
                            text: modelData[1]; wrapMode: Text.WrapAnywhere
                            color: modelData[0] === "" ? ink.soft : ink.text
                            font.pixelSize: modelData[0] === "" ? 14 : 13
                            lineHeight: 1.3
                        }
                    }
                }
            }
            Field {
                id: free
                width: parent.width
                visible: card.kind === "ask" && !!card.c && card.c.freeText
                hint: "或者直接告诉我……"
                onAccepted: if (text.length) P.answer(card.c.id, text)
            }
            Field { id: pass1; width: parent.width; secret: true; visible: card.kind === "sign" || card.kind === "welcome"
                    hint: card.kind === "welcome" ? "设一个只属于你的口令（至少 8 位）" : "你的签名口令" }
            Field { id: pass2; width: parent.width; secret: true; visible: card.kind === "welcome"; hint: "再输一次" }
            Flow {
                width: parent.width
                spacing: 10
                Repeater {
                    model: card.kind === "ask" && card.c ? card.c.options : []
                    Pill { required property string modelData; required property int index
                           text: modelData; primary: index === 0; onClicked: P.answer(card.c.id, modelData) }
                }
                Pill { visible: card.kind === "sign"; text: "签名同意"; primary: true; tint: ink.sign
                       live: !!card.c && !card.guard && card.c.canApprove && pass1.text.length > 0 && !P.working
                       onClicked: P.approve(card.c.id, pass1.text) }
                Pill { visible: card.kind === "sign"; text: "先不"; onClicked: P.deny(card.c.id) }
                Pill { visible: card.kind === "memory"; text: "记住吧"; primary: true; tint: ink.warm
                       live: !!card.c && card.c.canApprove; onClicked: P.remember(card.c.id, true) }
                Pill { visible: card.kind === "memory"; text: "别记"; onClicked: P.remember(card.c.id, false) }
                Pill { visible: card.kind === "welcome"; text: "就这样"; primary: true; live: !P.working
                       onClicked: P.welcome(pass1.text, pass2.text) }
            }
            Text {
                width: parent.width
                visible: text.length > 0
                text: P.working ? P.working + "……" : P.feedback
                color: P.working ? ink.soft : ink.warm
                font.pixelSize: 13
                wrapMode: Text.Wrap
            }
        }
    }

    // ---- tell it something -------------------------------------------------------------------------
    Sheet {
        id: talk
        readonly property bool shownNow: P.sayOpen && P.cardKey === ""
        anchors.right: parent.right
        anchors.rightMargin: 22
        anchors.bottom: orb.top
        anchors.bottomMargin: 6
        width: 424
        height: 118
        opacity: shownNow ? 1 : 0
        visible: opacity > 0.01
        Behavior on opacity { NumberAnimation { duration: 360; easing.type: Easing.OutCubic } }
        transform: Translate { y: (1 - talk.opacity) * 12 }
        onShownNowChanged: if (shownNow) { say.text = ""; say.forceActiveFocus() }   // the person clicked the light
        Column {
            x: 24; y: 22; width: parent.width - 48; spacing: 12
            Text { text: "想让我做什么？"; color: ink.soft; font.pixelSize: 12; font.letterSpacing: 1.6 }
            Field { id: say; width: parent.width; hint: "说一句话，回车"; onAccepted: P.say(text) }
        }
    }

    // ---- inside: what it is, and the rules it keeps ---------------------------------------------------
    Sheet {
        id: inner
        readonly property bool shownNow: P.innerOpen && P.cardKey === ""
        anchors.right: parent.right
        anchors.rightMargin: 22
        anchors.bottom: orb.top
        anchors.bottomMargin: 6
        width: 424
        height: 568
        opacity: shownNow ? 1 : 0
        visible: opacity > 0.01
        Behavior on opacity { NumberAnimation { duration: 380; easing.type: Easing.OutCubic } }
        transform: Translate { y: (1 - inner.opacity) * 12 }
        property int tab: 0
        onShownNowChanged: if (shownNow && rules.text === "") rules.text = P.rulesText

        Row {
            id: tabs
            x: 24; y: 20; spacing: 20
            Repeater {
                model: ["此刻", "规则"]
                Text {
                    required property string modelData
                    required property int index
                    text: modelData
                    color: inner.tab === index ? ink.text : ink.faint
                    font.pixelSize: 14
                    font.letterSpacing: 1.2
                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: inner.tab = index }
                }
            }
        }
        Text {
            anchors.right: parent.right; anchors.rightMargin: 22; y: 18
            text: "×"; color: ink.faint; font.pixelSize: 18
            MouseArea { anchors.fill: parent; anchors.margins: -8; cursorShape: Qt.PointingHandCursor; onClicked: P.toggleInner() }
        }

        Column {                                              // now
            visible: inner.tab === 0
            x: 24; y: 58; width: parent.width - 48; spacing: 10
            Repeater {
                model: P.statusRows
                Row {
                    required property var modelData
                    spacing: 12
                    Text { width: 92; text: modelData[0]; color: ink.faint; font.pixelSize: 12 }
                    Text { width: 268; text: modelData[1]; color: ink.text; font.pixelSize: 12; wrapMode: Text.WrapAnywhere }
                }
            }
            Rectangle { width: parent.width; height: 1; color: ink.line }
            Text {
                width: parent.width; height: 220
                text: P.logText; clip: true; wrapMode: Text.WrapAnywhere
                color: ink.soft; font.pixelSize: 12; lineHeight: 1.3
                verticalAlignment: Text.AlignBottom
            }
            Pill { text: "让它休息"; onClicked: P.rest() }
        }

        Column {                                              // rules
            visible: inner.tab === 1
            x: 24; y: 58; width: parent.width - 48; spacing: 10
            Rectangle {
                width: parent.width; height: 196; radius: 12
                color: ink.raised; border.color: ink.line
                ScrollView {
                    anchors.fill: parent; anchors.margins: 10
                    TextArea {
                        id: rules
                        color: ink.text; font.pixelSize: 12
                        font.family: Qt.platform.os === "osx" ? "Menlo"
                                     : (Qt.platform.os === "windows" ? "Consolas" : "monospace")
                        selectionColor: Qt.rgba(win.accent.r, win.accent.g, win.accent.b, 0.35)
                        background: null; wrapMode: TextEdit.NoWrap
                    }
                }
            }
            Field { id: sample; width: parent.width; hint: "试一句话，看门会怎么回应" }
            Row {
                spacing: 10
                Pill { text: "试一试"; onClicked: P.tryRules(rules.text, sample.text) }
            }
            Field { id: rulesPass; width: parent.width; secret: true; hint: "你的签名口令" }
            Pill { text: "签名并注入"; primary: true; live: rulesPass.text.length > 0 && !P.working
                   onClicked: { P.pushRules(rules.text, rulesPass.text); rulesPass.text = "" } }
            Text { width: parent.width; text: P.working ? P.working + "……" : P.rulesNote; wrapMode: Text.Wrap
                   color: ink.soft; font.pixelSize: 13; lineHeight: 1.3; visible: text.length > 0 }
            Text { width: parent.width; text: P.inForce; wrapMode: Text.Wrap; color: ink.faint; font.pixelSize: 12 }
        }
    }
}
