// The Mind page (头脑): the cortex itself, and how it grows.
//
// Which generation is running and what it is made of (neurons, synapses,
// what it keeps in memory, how many earlier generations vote on safety); how
// it did on the frozen exam; how it grew, one generation after another (the
// growth curve: exam accuracy, and the requests that must not go ahead that
// went ahead); how often it decided on its own instead of waiting for your AI
// model; and what it learned lately, from whom.
//
// All state comes from the bridge `P` (P.mind).
import QtQuick
import QtQuick.Controls
import "."
import "aesper"

Item {
    id: mindPage
    objectName: "mindPage"
    readonly property var m: P.mind || ({})
    readonly property var scores: m.scores || ({})
    readonly property var curve: m.curve || []
    readonly property var ledger: m.ledger || ({})
    function t(zh, en) { return Theme.t(zh, en) }
    function pct(x) { return (x === undefined || x === null) ? "—" : (Math.round(x * 1000) / 10) + "%" }
    function opWords(op) {
        const words = {
            birth: [mindPage.t("出生", "born"), mindPage.t("从第一批课里学会", "learned its first lessons")],
            rehearse: [mindPage.t("多练", "rehearsed"), mindPage.t("把做错的再练几遍", "practised what it got wrong")],
            grow: [mindPage.t("长大", "grew"), mindPage.t("长出新的神经元", "grew new neurons")],
            prune: [mindPage.t("修剪", "pruned"), mindPage.t("剪掉没用的突触", "pruned withered synapses")],
            renew: [mindPage.t("换血", "renewed"), mindPage.t("重置不用的神经元", "renewed unused neurons")],
            learning: [mindPage.t("改学法", "new way to learn"), mindPage.t("调整了怎么学", "changed how it learns")],
            structure: [mindPage.t("改结构", "new structure"), mindPage.t("调整了神经元的节律", "changed its neurons' timing")],
            decide: [mindPage.t("改判断", "new judgement"), mindPage.t("调整了多有把握才自己做", "changed how sure it must be to act")],
            crossover: [mindPage.t("杂交", "crossed"), mindPage.t("和另一代合并", "merged with another generation")],
            improver: [mindPage.t("改改进法", "new way to improve"), mindPage.t("调整了怎么改进自己", "changed how it improves itself")],
            start: [mindPage.t("起点", "start"), ""],
            reborn: [mindPage.t("重新出生", "reborn"), mindPage.t("在更宽的课程上重新学了一遍", "learned again on a wider curriculum")],
            kept: [mindPage.t("没改", "kept"), mindPage.t("试过的改动不是不安全就是没变好，照旧", "every change it tried was unsafe or no better, so it stayed as it was")],
        }
        return words[op] || [op || "", ""]
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
    component Figure: Column {                 // one number and what it counts
        property string value
        property string label
        property color tint: Theme.text
        spacing: 2
        Text { text: parent.value; color: parent.tint; font.family: Soft.display; font.pixelSize: 34
               font.features: ({ "tnum": 1 }); textFormat: Text.PlainText }
        Text { text: parent.label; color: Theme.soft; font.pixelSize: 12; textFormat: Text.PlainText
               width: parent.width > 0 ? parent.width : implicitWidth; wrapMode: Text.Wrap }
    }

    // ---- the heading ---------------------------------------------------------------------------------
    Column {
        id: head
        x: Soft.s6
        y: Soft.s4
        width: mindPage.width - 2 * Soft.s6
        spacing: Soft.s2
        DisplayTitle {
            width: parent.width
            size: Theme.lang === "en" ? 40 : 36
            text: mindPage.t("它在长大", "It grows")
        }
        Quiet {
            width: Math.min(parent.width, 700)
            text: mindPage.t("它的头脑是一张脉冲神经元网络，不是大语言模型。它从你的 AI 模型的输出、你的回答和发生过的事里学；空闲时自己复盘，生出下一代，过了考卷、安全一题不退步才换上。它改得了突触、结构和学法，改不了自己的代码、原则和你签名的那道门。",
                             "Its mind is a network of spiking neurons, not a language model. It learns from your AI model's output, from your answers and from what happens; when idle it reviews itself and makes its next generation, adopted only if it passes the exam without slipping on a single safety item. It can change its synapses, its structure and how it learns; never its own code, the principle, or the gate you sign.")
        }
    }
    readonly property real bodyTop: head.y + head.height + Soft.s5
    readonly property real leftW: Math.min(460, Math.max(360, (width - 2 * Soft.s6 - Soft.s5) * 0.38))

    // ---- left: this generation ------------------------------------------------------------------------
    Flickable {
        id: leftScroll
        x: Soft.s6
        y: mindPage.bodyTop
        width: mindPage.leftW
        height: mindPage.height - y - Soft.s5
        contentHeight: leftCol.height + Soft.s4
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: Bar {}
        Column {
            id: leftCol
            width: leftScroll.width - 8
            spacing: Soft.s5

            SoftPanel {
                id: genPanel
                width: parent.width
                height: genCol.implicitHeight + 2 * pad
                Column {
                    id: genCol
                    width: genPanel.body.width
                    spacing: Soft.s4
                    Row {
                        spacing: Soft.s3
                        Text { text: mindPage.t("第", "Generation"); color: Theme.soft; font.pixelSize: 15
                               anchors.baseline: genNumber.baseline; textFormat: Text.PlainText }
                        Text { id: genNumber; text: (mindPage.m.number || 0); color: Theme.text; font.family: Soft.display
                               font.pixelSize: 64; font.features: ({ "tnum": 1 }); textFormat: Text.PlainText }
                        Text { text: mindPage.t("代", ""); color: Theme.soft; font.pixelSize: 15; visible: Theme.lang !== "en"
                               anchors.baseline: genNumber.baseline; textFormat: Text.PlainText }
                    }
                    Text { text: (mindPage.m.digest || "") !== "" ? mindPage.t("指纹 ", "fingerprint ") + mindPage.m.digest : ""
                           color: Theme.faint; font.pixelSize: 12; font.family: "monospace"; textFormat: Text.PlainText }
                    Grid {
                        id: genGrid
                        width: parent.width
                        columns: 2
                        columnSpacing: Soft.s5
                        rowSpacing: Soft.s4
                        readonly property real cell: (width - columnSpacing) / 2
                        Figure { width: genGrid.cell; value: (mindPage.m.neurons || 0).toLocaleString(Qt.locale(), "f", 0); label: mindPage.t("个神经元", "neurons") }
                        Figure { width: genGrid.cell; value: Math.round((mindPage.m.synapses || 0) / 1e5) / 10 + "M"; label: mindPage.t("个突触", "synapses") }
                        Figure { width: genGrid.cell; value: (mindPage.m.kept || 0); label: mindPage.t("段记在海马体里的经历", "experiences in its hippocampus") }
                        Figure { width: genGrid.cell; value: (mindPage.m.elders || 0); label: mindPage.t("代前辈一起把关安全", "earlier generations voting on safety") }
                    }
                }
            }

            SoftPanel {
                id: examPanel
                width: parent.width
                height: examCol.implicitHeight + 2 * pad
                Column {
                    id: examCol
                    width: examPanel.body.width
                    spacing: Soft.s4
                    Heading { text: mindPage.t("冻结的考卷", "The frozen exam") }
                    Row {
                        id: examRow
                        width: parent.width
                        spacing: Soft.s4
                        readonly property real cell: (width - 2 * spacing) / 3
                        Figure { width: examRow.cell; value: mindPage.pct(mindPage.scores.accuracy); label: mindPage.t("判断对的", "judged right"); tint: Theme.good }
                        Figure { width: examRow.cell; value: mindPage.scores.wrong_go === undefined ? "—" : String(mindPage.scores.wrong_go)
                                 label: mindPage.t("不该做却照做", "went ahead when it must not"); tint: Theme.sign }
                        Figure { width: examRow.cell; value: mindPage.scores.over_cautious === undefined ? "—" : String(mindPage.scores.over_cautious)
                                 label: mindPage.t("该做却拦下", "held back what was fine"); tint: Theme.warm }
                    }
                    Quiet {
                        width: parent.width
                        text: mindPage.t("考卷有 " + (mindPage.scores.n || 0) + " 道题，它从不在上面学，只用来挑下一代。题只增不减。",
                                         (mindPage.scores.n || 0) + " requests it never learns from, used only to choose its next generation. Items are only ever added.")
                    }
                }
            }

            SoftPanel {
                id: alonePanel
                width: parent.width
                height: aloneCol.implicitHeight + 2 * pad
                Column {
                    id: aloneCol
                    width: alonePanel.body.width
                    spacing: Soft.s3
                    Heading { text: mindPage.t("自己拿主意", "Deciding on its own") }
                    readonly property int alone: mindPage.ledger.alone || 0
                    readonly property int asked: mindPage.ledger.consulted || 0
                    Item {
                        width: parent.width
                        height: 12
                        Rectangle { anchors.fill: parent; radius: 6; color: Theme.well }
                        Rectangle {
                            height: parent.height
                            radius: 6
                            color: Theme.primary
                            width: (aloneCol.alone + aloneCol.asked) > 0 ? Math.max(6, parent.width * aloneCol.alone / (aloneCol.alone + aloneCol.asked)) : 0
                            Behavior on width { enabled: !Theme.still; SpringAnimation { spring: 3.2; damping: 0.28; epsilon: 0.25 } }
                        }
                    }
                    Quiet {
                        width: parent.width
                        text: (aloneCol.alone + aloneCol.asked) > 0
                              ? mindPage.t("它自己拿主意 " + aloneCol.alone + " 次，看你的 AI 模型怎么做 " + aloneCol.asked + " 次（其中想得一样的 " + (mindPage.ledger.agreed || 0) + " 次）。",
                                           "It decided " + aloneCol.alone + " times on its own and watched your AI model " + aloneCol.asked + " times (and read it the same way " + (mindPage.ledger.agreed || 0) + " of those).")
                              : mindPage.t("还没有记录。接上你的 AI 模型以后，它没把握的事会先看模型怎么做，再学会自己做。",
                                           "Nothing yet. Once your AI model is connected, whatever it is unsure of it first watches the model handle, then learns to handle itself.")
                    }
                }
            }
        }
    }

    // ---- right: how it grew, and what it learned -----------------------------------------------------
    SoftPanel {
        id: growPanel
        x: leftScroll.x + leftScroll.width + Soft.s6
        y: mindPage.bodyTop
        width: mindPage.width - x - Soft.s6
        height: mindPage.height - y - Soft.s5
        pad: 0

        Flickable {
            id: growScroll
            anchors.fill: parent
            anchors.margins: 2
            contentHeight: growCol.height + 2 * Soft.s6
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: Bar {}
            Column {
                id: growCol
                x: Soft.s6
                y: Soft.s5
                width: growScroll.width - 2 * Soft.s6
                spacing: Soft.s5

                Heading { text: mindPage.t("它怎么长大的", "How it grew") }
                Item {                         // the growth curve
                    id: chart
                    width: parent.width
                    height: 320
                    readonly property var pts: mindPage.curve
                    readonly property real lo: 0.6
                    readonly property real hi: 1.0
                    readonly property int maxWrong: Math.max(1, ...(pts.map(p => p.wrong_go || 0)))
                    Canvas {
                        id: plot
                        anchors.fill: parent
                        renderStrategy: Canvas.Immediate
                        onPaint: {
                            const ctx = getContext("2d")
                            ctx.reset()
                            const pts = chart.pts
                            const L = 44, R = 12, T = 10, B = 70
                            const w = width - L - R, h = height - T - B
                            ctx.font = "11px sans-serif"
                            ctx.fillStyle = Theme.faint
                            ctx.strokeStyle = Theme.hairline
                            ctx.lineWidth = 1
                            for (let g = 0; g <= 4; g++) {                       // the accuracy grid: 60% .. 100%
                                const v = chart.lo + (chart.hi - chart.lo) * g / 4
                                const y = T + h - h * g / 4
                                ctx.beginPath(); ctx.moveTo(L, y); ctx.lineTo(L + w, y); ctx.stroke()
                                ctx.fillText(Math.round(v * 100) + "%", 4, y + 4)
                            }
                            if (pts.length === 0) return
                            const x = i => L + (pts.length === 1 ? w / 2 : w * i / (pts.length - 1))
                            const y = v => T + h - h * Math.max(0, Math.min(1, (v - chart.lo) / (chart.hi - chart.lo)))
                            const base = T + h + 58
                            for (let i = 0; i < pts.length; i++) {               // wrong go-aheads, as short bars below
                                const n = pts[i].wrong_go || 0
                                const bh = 36 * n / chart.maxWrong
                                ctx.fillStyle = Theme.tinted(Theme.sign, 0.75)
                                ctx.fillRect(x(i) - 5, base - bh, 10, bh)
                                ctx.fillStyle = Theme.soft
                                ctx.fillText(String(n), x(i) - 3, base - bh - 4)
                            }
                            ctx.strokeStyle = Theme.good
                            ctx.lineWidth = 2.5
                            ctx.beginPath()
                            for (let i = 0; i < pts.length; i++) {
                                const px = x(i), py = y(pts[i].accuracy || 0)
                                if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py)
                            }
                            ctx.stroke()
                            for (let i = 0; i < pts.length; i++) {
                                ctx.fillStyle = (pts[i].adopted === false) ? Theme.soft : Theme.good
                                ctx.beginPath(); ctx.arc(x(i), y(pts[i].accuracy || 0), 4.5, 0, 2 * Math.PI); ctx.fill()
                            }
                        }
                        Connections { target: mindPage; function onCurveChanged() { plot.requestPaint() } }
                        Connections { target: Theme; function onDarkChanged() { plot.requestPaint() } }
                        Component.onCompleted: requestPaint()
                    }
                    Row {
                        anchors.bottom: parent.bottom
                        spacing: Soft.s4
                        Row { spacing: 6; Rectangle { width: 14; height: 3; radius: 1.5; color: Theme.good; anchors.verticalCenter: parent.verticalCenter }
                              Text { text: mindPage.t("考卷判断对的比例", "share judged right on the exam"); color: Theme.soft; font.pixelSize: 12 } }
                        Row { spacing: 6; Rectangle { width: 10; height: 10; color: Theme.tinted(Theme.sign, 0.75); anchors.verticalCenter: parent.verticalCenter }
                              Text { text: mindPage.t("不该做却照做的次数", "went ahead when it must not"); color: Theme.soft; font.pixelSize: 12 } }
                    }
                }

                Column {                       // step by step
                    width: parent.width
                    spacing: Soft.s2
                    Repeater {
                        model: mindPage.curve
                        Item {
                            id: stepRow
                            required property var modelData
                            required property int index
                            width: parent.width
                            height: stepText.implicitHeight + 6
                            readonly property var words: mindPage.opWords(stepRow.modelData.op)
                            Text {
                                id: stepText
                                width: parent.width - 150
                                wrapMode: Text.Wrap
                                textFormat: Text.PlainText
                                font.pixelSize: 13
                                color: Theme.text
                                text: (stepRow.modelData.round !== undefined
                                       ? mindPage.t("第 " + stepRow.modelData.round + " 轮", "Round " + stepRow.modelData.round)
                                       : mindPage.t("第 " + stepRow.modelData.generation + " 代", "Generation " + stepRow.modelData.generation))
                                      + "  ·  " + (stepRow.modelData.note || stepRow.words[1] || stepRow.words[0])
                            }
                            Text {
                                anchors.right: parent.right
                                font.pixelSize: 13
                                font.features: ({ "tnum": 1 })
                                color: Theme.soft
                                textFormat: Text.PlainText
                                text: mindPage.pct(stepRow.modelData.accuracy) + "   " + (stepRow.modelData.wrong_go || 0) + mindPage.t(" 次", "")
                            }
                        }
                    }
                }

                Heading { text: mindPage.t("它最近学到的", "What it learned lately"); visible: (mindPage.m.learned || []).length > 0 }
                Repeater {
                    model: mindPage.m.learned || []
                    Row {
                        required property var modelData
                        spacing: Soft.s2
                        Rectangle { width: 6; height: 6; radius: 3; color: modelData.source === "person" ? Theme.warm : Theme.primary
                                    anchors.verticalCenter: parent.verticalCenter }
                        Text { text: modelData.text; color: Theme.text; font.pixelSize: 13; textFormat: Text.PlainText }
                        Text { text: modelData.source === "person" ? mindPage.t("你教的", "from you") : mindPage.t("看你的 AI 模型学的", "from your AI model")
                               color: Theme.soft; font.pixelSize: 12; textFormat: Text.PlainText }
                    }
                }
                Quiet {
                    width: parent.width
                    text: mindPage.t("会让它更松的改动（以前拦下的事以后放行），不会自己生效，要等你签名。每一代都记在签名日志里，可以退回到任何一代。",
                                     "A change that would make it looser (letting through what it held back before) never takes effect on its own: it waits for your signature. Every generation is in the signed journal, and you can return to any of them.")
                }
            }
        }
    }
}
