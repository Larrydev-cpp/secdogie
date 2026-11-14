// The pages of the home window as one pill: the current page on a tonal
// lozenge of the primary colour, the others as plain words.
import QtQuick
import QtQuick.Controls
import "."

Rectangle {
    id: nav
    property var items: []                     // [{text, badge}]
    property int current: 0
    signal picked(int index)

    implicitWidth: row.implicitWidth + 8
    implicitHeight: 44
    radius: height / 2
    color: Theme.panel
    border.color: Theme.dark ? Theme.hairline : "transparent"
    Accessible.role: Accessible.PageTabList

    Row {
        id: row
        anchors.centerIn: parent
        spacing: 2
        Repeater {
            model: nav.items
            AbstractButton {
                id: tab
                required property var modelData
                required property int index
                readonly property bool here: nav.current === index
                implicitHeight: 36
                implicitWidth: label.implicitWidth + 32 + (badge.visible ? badge.width + 6 : 0)
                hoverEnabled: true
                focusPolicy: Qt.TabFocus
                Accessible.role: Accessible.PageTab
                Accessible.name: tab.modelData.text
                Accessible.selected: tab.here
                HoverHandler { cursorShape: Qt.PointingHandCursor }
                Keys.onReturnPressed: tab.animateClick()
                Keys.onSpacePressed: tab.animateClick()
                onClicked: nav.picked(index)
                background: Rectangle {
                    radius: height / 2
                    color: tab.here ? Theme.primaryContainer : (tab.hovered ? Theme.wash : "transparent")
                    border.color: tab.visualFocus ? Theme.focus : "transparent"
                    border.width: 2
                    Behavior on color { ColorAnimation { duration: Theme.touch } }
                }
                contentItem: Item {
                    Text {
                        id: label
                        anchors.centerIn: parent
                        anchors.horizontalCenterOffset: badge.visible ? -(badge.width + 6) / 2 : 0
                        text: tab.modelData.text
                        color: tab.here ? Theme.onPrimaryContainer : (tab.hovered ? Theme.text : Theme.soft)
                        font.pixelSize: 14
                        font.weight: Font.Medium
                        textFormat: Text.PlainText
                    }
                    Rectangle {
                        id: badge
                        visible: (tab.modelData.badge || 0) > 0
                        anchors.left: label.right
                        anchors.leftMargin: 6
                        anchors.verticalCenter: parent.verticalCenter
                        width: Math.max(18, n.implicitWidth + 10); height: 18; radius: 9
                        color: Theme.seal
                        Text { id: n; anchors.centerIn: parent; text: tab.modelData.badge || ""; color: Theme.sealText
                               font.pixelSize: 11; font.weight: Font.DemiBold; font.features: ({ "tnum": 1 }) }
                    }
                }
            }
        }
    }
}
