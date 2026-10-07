"""Explicit candidate selection and field differences for reviewed records."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...services.reprocess_review import ReprocessReview
from ...state import StateStore
from ..i18n import _


class ReprocessReviewDialog(QDialog):
    def __init__(self, database: Path, record_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.database, self.record_id = database, record_id
        self.result_record: dict[str, Any] | None = None
        self.setWindowTitle(_("确认重提取候选"))
        self.setAccessibleName(_("重提取候选复核"))
        self.resize(840, 620)
        with StateStore(database) as state:
            self.snapshot = ReprocessReview(state).load(record_id)
        layout = QVBoxLayout(self)
        notice = QLabel(_("请确认候选属于这条原记录。接受将替换整条记录，包括移除候选中缺失的字段；拒绝保留当前值。不会自动新增、删除记录或发送通知。已有导出文件需重新导出。"))
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.candidates = QComboBox()
        self.candidates.setAccessibleName(_("对应的重提取候选"))
        self.candidates.addItem(_("请选择对应候选，不按位置自动匹配"), None)
        used = {decision.get("candidate_index") for decision in self.snapshot["candidate"].get("decisions", {}).values()}
        for index, record in enumerate(self.snapshot["candidate"]["records"]):
            if index not in used:
                preview = json.dumps(record["data"], ensure_ascii=False)[:180]
                self.candidates.addItem(f"{index + 1} · {preview}", index)
        layout.addWidget(self.candidates)
        self.differences = QTableWidget(0, 3)
        self.differences.setHorizontalHeaderLabels([_("字段"), _("当前记录"), _("候选值")])
        self.differences.setAccessibleName(_("当前记录与候选的字段差异"))
        self.differences.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.differences.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.differences)
        self.evidence = QTextEdit()
        self.evidence.setReadOnly(True)
        self.evidence.setAccessibleName(_("候选来源与字段证据"))
        layout.addWidget(self.evidence)
        self.reason = QLineEdit()
        self.reason.setAccessibleName(_("复核理由"))
        self.reason.setPlaceholderText(_("填写接受或拒绝的理由"))
        layout.addWidget(self.reason)
        buttons = QHBoxLayout()
        self.accept_candidate = QPushButton(_("接受所选候选"))
        self.reject_candidate = QPushButton(_("拒绝，保留当前记录"))
        cancel = QPushButton(_("取消"))
        for button in (self.accept_candidate, self.reject_candidate, cancel):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.candidates.currentIndexChanged.connect(self._render)
        self.reason.textChanged.connect(self._enable_actions)
        self.accept_candidate.clicked.connect(lambda: self._resolve(False))
        self.reject_candidate.clicked.connect(lambda: self._resolve(True))
        cancel.clicked.connect(self.reject)
        self._render()

    def _enable_actions(self) -> None:
        has_reason = bool(self.reason.text().strip())
        self.accept_candidate.setEnabled(has_reason and self.candidates.currentData() is not None)
        self.reject_candidate.setEnabled(has_reason)

    def _render(self) -> None:
        index = self.candidates.currentData()
        selected = self.snapshot["candidate"]["records"][index] if index is not None else None
        current = self.snapshot["data"]
        proposed = selected["data"] if selected else {}
        names = sorted(set(current) | set(proposed))
        self.differences.setRowCount(len(names))
        for row, name in enumerate(names):
            for column, value in enumerate((name, json.dumps(current[name], ensure_ascii=False) if name in current else _("字段不存在"),
                                           json.dumps(proposed[name], ensure_ascii=False) if name in proposed else _("字段不存在"))):
                self.differences.setItem(row, column, QTableWidgetItem(value))
        self.evidence.setPlainText(json.dumps(selected, ensure_ascii=False, indent=2) if selected else _("选择候选后显示其来源与证据。"))
        self._enable_actions()

    def _resolve(self, reject: bool) -> None:
        try:
            with StateStore(self.database) as state:
                self.result_record = ReprocessReview(state).resolve(
                    self.record_id, self.snapshot["token"], candidate_index=None if reject else self.candidates.currentData(),
                    reason=self.reason.text().strip(),
                )
        except (ValueError, KeyError, sqlite3.Error) as exc:
            QMessageBox.warning(self, _("未保存复核"), str(exc))
            self.accept_candidate.setEnabled(False)
            self.reject_candidate.setEnabled(False)
            return
        self.accept()
