// Q1 试点：市场橱窗页（QML 侧）
//
// 范围（§11.3）：**只**做这一个新增页面，不动任何存量 QWidget 视图。
// 颜色一律取 Python 注入的 VisualTokens；数据来自 ShowcaseModel。

import QtQuick 2.15

Rectangle {
    id: page

    color: VisualTokens.canvas

    Column {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 14

        Text {
            text: I18n.title
            color: VisualTokens.text
            font.pixelSize: 26
            font.bold: true
        }

        Text {
            width: parent.width
            text: I18n.subtitle
            color: VisualTokens.muted
            font.pixelSize: 14
            wrapMode: Text.WordWrap
        }

        // 空态：明确说明"没有可展示的本地条目"，并给可行动指引
        Rectangle {
            width: parent.width
            height: 96
            visible: ShowcaseModel.count === 0
            color: "transparent"
            border.color: VisualTokens.border
            border.width: 1
            radius: 10

            Text {
                anchors.centerIn: parent
                width: parent.width - 32
                horizontalAlignment: Text.AlignHCenter
                text: I18n.emptyHint
                color: VisualTokens.muted
                font.pixelSize: 13
                wrapMode: Text.WordWrap
            }
        }

        ListView {
            id: cards

            width: parent.width
            height: parent.height - 150
            visible: ShowcaseModel.count > 0
            clip: true
            spacing: 10
            model: ShowcaseModel

            delegate: ShowcaseCard {
                width: cards.width
                name: model.name
                version: model.version
                kinds: model.kinds
                summary: model.summary
            }
        }
    }
}
