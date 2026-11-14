// The wallet: a stack of cards. The chosen card sits in front, whole; the
// others stand behind it in their own order, each showing only its top strip
// (its title and its figure), the way a phone's wallet stacks cards. Choosing
// a card in the stack brings it to the front.
//
// `cards` is a list of WalletCard settings; the delegates are kept while the
// list only changes its contents, so the art is not repainted on every tick.
import QtQuick
import "."

Item {
    id: wallet
    property var cards: []
    property int current: 0
    property real peek: 54
    signal picked(int index)
    readonly property int count: cards.length
    readonly property int front: Math.max(0, Math.min(current, count - 1))
    readonly property real cardH: width / Theme.cardRatio
    implicitHeight: count > 0 ? cardH + (count - 1) * peek : 0

    Repeater {
        model: wallet.count
        WalletCard {
            id: one
            required property int index
            readonly property var c: wallet.cards[index] || ({})
            readonly property bool atFront: index === wallet.front
            // the stack keeps its order; the front card steps out of it
            readonly property int slot: atFront ? wallet.count - 1 : (index < wallet.front ? index : index - 1)
            width: wallet.width
            height: wallet.cardH
            y: slot * wallet.peek
            z: slot
            Behavior on y { NumberAnimation { duration: Theme.shuffle; easing.type: Easing.OutExpo } }
            front: atFront
            kind: c.kind || "glaze"
            glaze: c.glaze || "jilan"
            key: c.key || ""
            panel: !!c.panel
            title: c.title || ""
            figure: c.figure || ""
            number: c.number || ""
            label: c.label || ""
            note: c.note || ""
            live: !!c.live
            code: c.code || ""
            stamped: !!c.stamped
            quarters: c.quarters || []
            now: c.now === undefined ? -1 : c.now
            Accessible.selected: atFront
            onClicked: wallet.picked(index)
        }
    }
}
