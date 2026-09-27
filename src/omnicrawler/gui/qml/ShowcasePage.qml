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

        // 剩余高度容器：空态卡与列表共用，互斥显示。
        // 用容器而不是给 ListView 写死 `parent.height - 150`：Column 里 positioner
        // 会把**不可见**项也计入布局，写死高度会让内容恒定溢出页面底部。
        Item {
            width: parent.width
            height: parent.height - y - parent.y

            // 空态：明确说明"没有可展示的本地条目"，并给可行动指引
            Rectangle {
                objectName: "showcaseEmptyState"
                anchors.fill: parent
                visible: ShowcaseModel.count === 0
                color: "transparent"
                border.color: VisualTokens.border
                border.width: 1
                radius: 10

                Text {
                    objectName: "showcaseEmptyHint"
                    anchors.centerIn: parent
                    width: parent.width - 32
                    horizontalAlignment: Text.AlignHCenter
                    text: I18n.empty_hint
                    color: VisualTokens.muted
                    font.pixelSize: 13
                    wrapMode: Text.WordWrap
                }
            }

            ListView {
                id: cards
                objectName: "showcaseList"

                anchors.fill: parent
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
}
