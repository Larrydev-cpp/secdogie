// The light Studio leaves behind when its home window is closed: a faint,
// breathing light in the corner of the screen.
//
// Most of the time that light is all there is. A whisper fades in beside it
// for a few seconds when it has something to say in passing. A card rises
// above it when the work needs you (one question, one signature, one thing it
// would like to remember) and fades away once answered. Hover the light and
// whatever it was holding for you comes up. Click it to open the home window
// again; right-click (or the Menu key) for its inner workings.
//
// While the home window is open, this window is hidden: what waits on you
// shows there instead. The design system is in studio/DESIGN.md; all timing
// and state come from the bridge `P` (secdogie_studio/app.py).
import QtQuick
import QtQuick.Window
import QtQuick.Controls
import "."

Window {
    id: win
    flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
    color: "transparent"
    title: "secdogie"
    visible: !P.homeOpen

    // ---- size: the light alone, a whisper, or a sheet above it -----------------------------------
    // The window is only as big as what it shows, so the rest of the desktop stays the person's.
    readonly property int pad: 20                         // room around every surface for its shadow
    readonly property int dock: pad + 84 + pad            // the light alone
    readonly property string want: (P.cardKey !== "" || P.innerOpen) ? "open"
                                   : (P.whisper !== "" ? "whisper" : "compact")
    property string shape: "compact"
    property real room: 0                                 // the height kept for the sheet above the light
    readonly property real need: Math.max(card.shownNow ? card.height : 0, inner.shownNow ? inner.height : 0)
    onWantChanged: {
        if (want === "open" || (want === "whisper" && shape === "compact")) { shape = want; shrink.stop() }
        else shrink.restart()                             // let the fade finish before the window shrinks
    }
    onNeedChanged: if (need >= room) room = need; else shrink.restart()
    Timer {
        id: shrink
        interval: (win.shape === "whisper" ? Theme.whisperOut : Theme.exit) + 80
        onTriggered: { win.shape = win.want; win.room = win.need }
    }
    Component.onCompleted: { Theme.still = P.still; shape = want; room = need }
    width: shape === "compact" ? dock : 468
    height: dock + (shape === "open" ? room + 8 : 0)
    x: Screen.virtualX + Screen.desktopAvailableWidth - width - 8
    y: Screen.virtualY + Screen.desktopAvailableHeight - height - 8

    Shortcut {                                            // Esc puts the inner workings away
        sequences: [StandardKey.Cancel]
        enabled: P.innerOpen
        onActivated: P.toggleInner()
    }

    // ---- the light -------------------------------------------------------------------------------
    Item {
        id: light
        width: 84
        height: 84
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.rightMargin: win.pad
        anchors.bottomMargin: win.pad
        activeFocusOnTab: true
        Accessible.role: Accessible.Button
        Accessible.name: "secdogie"
        Accessible.description: (P.held > 0 ? "有 " + P.held + " 件事在等你。" : "") + "点一下回到主界面，右键看看我的里面。"
        Keys.onReturnPressed: P.openHome()
        Keys.onEnterPressed: P.openHome()
        Keys.onSpacePressed: P.openHome()
        Keys.onMenuPressed: P.toggleInner()
        Orb {
            anchors.fill: parent
            size: 84
            mood: P.mood
            held: P.cardKey === "" ? P.held : 0
            lit: hoverLight.containsMouse || light.activeFocus
        }
        Rectangle {             // the keyboard is on the light
            visible: light.activeFocus
            anchors.centerIn: parent
            width: 56; height: 56; radius: 28
            color: "transparent"
            border.width: 2
            border.color: Theme.focus
        }
        MouseArea {
            id: hoverLight
            anchors.fill: parent
            hoverEnabled: true
            acceptedButtons: Qt.LeftButton | Qt.RightButton
            cursorShape: Qt.PointingHandCursor
            onEntered: P.hover()
            onClicked: (mouse) => mouse.button === Qt.RightButton ? P.toggleInner() : P.openHome()
        }
    }

    // ---- a whisper ---------------------------------------------------------------------------------
    Item {
        id: whisper
        property string shown: P.whisper          // keeps the last line while it fades out
        readonly property string current: P.whisper
        onCurrentChanged: if (current !== "") shown = current
        readonly property bool shownNow: P.whisper !== "" && P.cardKey === ""
        anchors.right: light.left
        anchors.rightMargin: 4
        anchors.verticalCenter: light.verticalCenter
        width: Math.min(300, words.implicitWidth + 32)
        height: words.implicitHeight + 20
        opacity: shownNow ? 1 : 0
        visible: opacity > 0.01
        Behavior on opacity {
            NumberAnimation {
                duration: whisper.shownNow ? Theme.whisperIn : Theme.whisperOut
                easing.type: whisper.shownNow ? Easing.OutExpo : Easing.InOutSine
            }
        }
        transform: Translate { x: (1 - whisper.opacity) * (Theme.still ? 0 : 8) }
        Accessible.role: Accessible.StaticText
        Accessible.name: shown
        Shadow { anchors.fill: parent; radius: Math.min(parent.height / 2, 18); strength: 0.8 }
        Rectangle {
            anchors.fill: parent
            radius: Math.min(height / 2, 18)
            color: Theme.card
            border.color: Theme.hairline
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
            color: P.whisperTone === "warm" ? Theme.warm : Theme.text
            font.family: Theme.serif                     // a whisper is the companion's voice
            font.pixelSize: Theme.voice - 1
            lineHeight: 1.35
        }
    }

    // ---- a card: one question, one signature, one memory, or hello ----------------------------------
    Sheet {
        id: card
        property var c: P.card                  // keeps the last card while it fades out
        readonly property string current: P.cardKey
        onCurrentChanged: if (P.card) { c = P.card; moment.reset() }
        readonly property bool shownNow: P.cardKey !== ""
        anchors.right: parent.right
        anchors.rightMargin: win.pad
        anchors.bottom: light.top
        anchors.bottomMargin: 8
        width: 428
        height: moment.implicitHeight + 48
        opacity: shownNow ? 1 : 0
        visible: opacity > 0.01
        Behavior on opacity {
            NumberAnimation {
                duration: card.shownNow ? Theme.enter : Theme.exit
                easing.type: card.shownNow ? Easing.OutExpo : Easing.InQuad
            }
        }
        transform: Translate { y: (1 - card.opacity) * Theme.rise }
        MomentCard { id: moment; x: 24; y: 24; width: parent.width - 48; c: card.c; restParagraphs: 3
                     onReadAll: P.openHome() }
    }

    // ---- inside: what it is, and the rules it keeps ---------------------------------------------------
    Sheet {
        id: inner
        readonly property bool shownNow: P.innerOpen && P.cardKey === ""
        anchors.right: parent.right
        anchors.rightMargin: win.pad
        anchors.bottom: light.top
        anchors.bottomMargin: 8
        width: 428
        height: 560
        opacity: shownNow ? 1 : 0
        visible: opacity > 0.01
        Behavior on opacity {
            NumberAnimation {
                duration: inner.shownNow ? Theme.enter : Theme.exit
                easing.type: inner.shownNow ? Easing.OutExpo : Easing.InQuad
            }
        }
        transform: Translate { y: (1 - inner.opacity) * Theme.rise }
        Accessible.role: Accessible.Grouping
        Accessible.name: "我的里面"
        property int tab: 0
        onShownNowChanged: if (shownNow) rulesEditor.load()

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
        CloseButton { anchors.right: parent.right; anchors.rightMargin: 14; y: 12; onClicked: P.toggleInner() }
        Rectangle { y: 56; width: parent.width; height: 1; color: Theme.hairline }

        Item {                                                // now
            visible: inner.tab === 0
            x: 24; y: 72; width: parent.width - 48; height: parent.height - 72 - 24
            Column {
                id: rows
                width: parent.width
                spacing: 4
                Repeater {
                    model: P.statusRows
                    Fact {
                        required property var modelData
                        width: rows.width
                        labelWidth: 96
                        label: modelData[0]
                        value: modelData[1]
                    }
                }
            }
            Rectangle { id: divider; anchors.top: rows.bottom; anchors.topMargin: 12; width: parent.width; height: 1
                        color: Theme.hairline }
            Text {
                anchors.top: divider.bottom
                anchors.topMargin: 12
                anchors.bottom: restBlock.top
                anchors.bottomMargin: 16
                width: parent.width
                readonly property bool empty: P.logText === ""
                text: empty ? "还没有什么可记的。打开主界面跟我说一句话，就会记在这里。" : P.logText
                textFormat: Text.PlainText
                color: empty ? Theme.faint : Theme.soft
                font.pixelSize: Theme.meta
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
                       color: Theme.faint; font.pixelSize: Theme.meta; wrapMode: Text.Wrap }
            }
        }

        RulesEditor {                                         // rules
            id: rulesEditor
            visible: inner.tab === 1
            x: 24; y: 72; width: parent.width - 48
        }
    }
}
