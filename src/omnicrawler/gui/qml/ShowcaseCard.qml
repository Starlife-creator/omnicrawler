// Q1 试点：市场橱窗卡片（可复用组件）
//
// 颜色**全部**来自 Python 侧注入的 VisualTokens 桥，QML 里不写任何裸色值
// —— 与 QWidget 侧"禁裸色值"守卫同一条纪律（见 gui/design_system.py）。

import QtQuick 2.15

Rectangle {
    id: card

    property string name: ""
    property string version: ""
    property string kinds: ""
    property string summary: ""

    implicitHeight: 78
    color: VisualTokens.surface
    border.color: VisualTokens.border
    border.width: 1
    radius: 10

    Column {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 6

        Row {
            spacing: 8

            Text {
                text: card.name
                color: VisualTokens.text
                font.pixelSize: 15
                font.bold: true
            }

            Text {
                text: card.version ? ("v" + card.version) : ""
                color: VisualTokens.muted
                font.pixelSize: 12
            }

            Text {
                text: card.kinds
                color: VisualTokens.primary
                font.pixelSize: 12
                font.bold: true
            }
        }

        Text {
            width: parent.width
            text: card.summary
            color: VisualTokens.muted
            font.pixelSize: 12
            elide: Text.ElideRight
            maximumLineCount: 2
            wrapMode: Text.WordWrap
        }
    }
}
