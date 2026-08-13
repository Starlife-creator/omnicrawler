"""JSONPath 在线验证对话框（批 A-1）。

用户配置 REST 数据源字段时：粘贴目标 API 的 JSON 样本 + 输入 JSONPath，
即时（防抖 300ms）验证语法并在样本上试运行，展示匹配数量与前若干条匹配值。
纯本地计算（自包含，无网络请求），验证范围与提取引擎实际支持的子集一致。
"""

from __future__ import annotations

import json
from typing import Any

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from ...core.jsonpath import describe_syntax, validate
from ..i18n import _

_SAMPLE_PLACEHOLDER = (
    "{\n"
    '  "data": {\n'
    '    "items": [\n'
    '      {"title": "Post One", "price": "12.5"},\n'
    '      {"title": "Post Two", "price": "30"}\n'
    "    ]\n"
    "  }\n"
    "}"
)


def _format_value(value: Any, limit: int = 200) -> str:
    """匹配值的展示格式（JSON 序列化 + 截断）。"""
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"


class JsonPathTestDialog(QDialog):
    """粘贴 JSON 样本即时验证 JSONPath 表达式。"""

    _DEBOUNCE_MS = 300

    def __init__(self, expression: str, parent: Any = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("JSONPath 在线验证"))
        self.setMinimumSize(560, 500)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(_("JSONPath 表达式：")))
        self._expression = QLineEdit(expression)
        self._expression.setPlaceholderText(_("例如：$.data.items[*].title"))
        layout.addWidget(self._expression)

        layout.addWidget(QLabel(_("JSON 样本（用于试运行验证）：")))
        self._sample = QTextEdit(_SAMPLE_PLACEHOLDER)
        self._sample.setPlaceholderText(_("粘贴目标 API 返回的 JSON 片段"))
        layout.addWidget(self._sample, stretch=1)

        self._validate_btn = QPushButton(_("验证"))
        self._validate_btn.clicked.connect(self._do_validate)
        layout.addWidget(self._validate_btn)

        self._result = QTextEdit()
        self._result.setReadOnly(True)
        self._result.setMinimumHeight(120)
        layout.addWidget(self._result)

        layout.addWidget(QLabel(_(describe_syntax())))

        buttons = QHBoxLayout()
        ok_btn = QPushButton(_("确定"))
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton(_("取消"))
        cancel_btn.clicked.connect(self.reject)
        buttons.addStretch()
        buttons.addWidget(ok_btn)
        buttons.addWidget(cancel_btn)
        layout.addLayout(buttons)

        # 即时验证（防抖）：表达式或样本改动后自动重验
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(self._DEBOUNCE_MS)
        self._debounce.timeout.connect(self._do_validate)
        self._expression.textChanged.connect(self._schedule_validate)
        self._sample.textChanged.connect(self._schedule_validate)

        self._do_validate()

    def _schedule_validate(self) -> None:
        self._debounce.start()

    def _do_validate(self) -> None:
        expression = self._expression.text().strip()
        result = validate(expression, self._sample.toPlainText())
        lines: list[str] = []
        if result.ok:
            if result.matches is None:
                lines.append(_("✓ 语法通过"))
            else:
                lines.append(_(f"✓ 语法通过，样本匹配 {result.matches} 条"))
                for index, value in enumerate(result.sample_values, 1):
                    lines.append(f"{index}. {_format_value(value)}")
                shown = len(result.sample_values)
                if result.matches > shown:
                    lines.append(_(f"... 共 {result.matches} 条，仅显示前 {shown} 条"))
        else:
            lines.append(_("✗ 验证未通过"))
            lines.append(result.error)
        self._result.setPlainText("\n".join(lines))

    def expression(self) -> str:
        """当前表达式（供调用方写回字段）。"""
        return self._expression.text().strip()


__all__ = ["JsonPathTestDialog"]
