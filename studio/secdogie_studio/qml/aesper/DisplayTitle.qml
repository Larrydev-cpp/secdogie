// The display line: a serif set large and tight (Newsreader Display for Latin,
// the platform's serif for Chinese), in near-black ink on paper, paper-white at night.
import QtQuick
import ".."

Text {
    property real size: Soft.hero
    color: Soft.ink
    font.family: Soft.display
    font.pixelSize: size
    font.weight: Font.Normal
    font.letterSpacing: Theme.lang === "en" ? -size * 0.028 : size * 0.02
    lineHeight: Theme.lang === "en" ? 0.98 : 1.18
    wrapMode: Text.Wrap
    textFormat: Text.PlainText
}
