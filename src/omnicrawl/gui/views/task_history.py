"""任务历史管理视图。

显示最近任务记录，支持重新加载配置和查看结果。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..i18n import _

HISTORY_FILE = "work/task_history.jsonl"
DEFAULT_MAX_ENTRIES = 100
DEFAULT_MAX_DAYS = 30
# S3.2.1：内存有界上限（防超长文件全量驻留），显示/清理按 max_entries 截断
MAX_LOADED_RECORDS = 5000


class TaskHistory(QWidget):
    """任务历史侧边栏。

    Signals:
        load_config_requested: 请求加载历史配置 (config_path)。
        view_results_requested: 请求查看结果 (workspace)。
    """

    load_config_requested = pyqtSignal(str)  # config_path
    view_results_requested = pyqtSignal(str)  # workspace

    def __init__(
        self,
        project_root: Path,
        parent: QWidget | None = None,
        *,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_days: int = DEFAULT_MAX_DAYS,
    ) -> None:
        super().__init__(parent)
        self._project_root = project_root
        # S3.2.1：history_max_entries 消费方——不再硬编码 100
        self._max_entries = max(1, int(max_entries))
        self._max_days = max(1, int(max_days))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        title = QLabel(_("📋 历史任务"))
        title.setStyleSheet("font-weight: bold; font-size: 13px;")
        layout.addWidget(title)

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self._list)

        # 按钮
        btn_layout = QHBoxLayout()

        load_btn = QPushButton(_("重新加载配置"))
        load_btn.clicked.connect(self._load_selected)
        btn_layout.addWidget(load_btn)

        view_btn = QPushButton(_("查看结果"))
        view_btn.clicked.connect(self._view_results)
        btn_layout.addWidget(view_btn)

        clear_btn = QPushButton(_("清理"))
        clear_btn.clicked.connect(self._cleanup)
        btn_layout.addWidget(clear_btn)

        layout.addLayout(btn_layout)

        self._records: list[dict[str, Any]] = []

    @property
    def history_path(self) -> Path:
        return self._project_root / HISTORY_FILE

    def load_history(self) -> None:
        """加载历史记录。"""
        self._records = []
        self._list.clear()

        fp = self.history_path
        if not fp.is_file():
            return

        try:
            with open(fp, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            record = json.loads(line)
                            self._records.append(record)
                        except json.JSONDecodeError:
                            continue
        except Exception:
            return

        # 按时间倒序
        self._records.sort(key=lambda r: r.get("started_at", ""), reverse=True)
        # S3.2.1：内存有界（MAX_LOADED_RECORDS）；显示按 max_entries 截断，
        # 不截断 _records——update/重写不再误删文件中更旧的记录
        self._records = self._records[:MAX_LOADED_RECORDS]
        shown = self._records[: self._max_entries]

        for record in shown:
            time_str = record.get("started_at", "?")[:19]
            name = record.get("project_name", "?")
            status = record.get("status", "?")
            status_icon = {"finished": "✅", "error": "❌", "running": "⏳"}.get(status, "⬜")
            text = f"{status_icon} {time_str}  {name}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, record)
            item.setToolTip(json.dumps(record, ensure_ascii=False, indent=2))
            self._list.addItem(item)

    def add_record(self, task_id: str, project_name: str, config_path: str,
                   workspace: str, status: str = "running") -> None:
        """添加新的历史记录。"""
        record = {
            "task_id": task_id,
            "project_name": project_name,
            "config_path": config_path,
            "workspace": workspace,
            "status": status,
            "started_at": datetime.now().isoformat(),
            "finished_at": None,
        }

        self._records.insert(0, record)

        # 写入文件
        fp = self.history_path
        fp.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(fp, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

        self.load_history()

    def update_record(self, task_id: str, status: str) -> None:
        """更新任务状态。"""
        for record in self._records:
            if record.get("task_id") == task_id:
                record["status"] = status
                record["finished_at"] = datetime.now().isoformat()
                break

        # 重写文件
        fp = self.history_path
        try:
            with open(fp, "w", encoding="utf-8") as f:
                for r in self._records:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
        except Exception:
            pass

        self.load_history()

    def _cleanup(self) -> None:
        """清理过期记录（按 days + max_entries，S3.2.1 消费 max_days/max_entries）。"""
        cutoff = datetime.now() - timedelta(days=self._max_days)
        new_records = []
        for r in self._records:
            started = r.get("started_at", "")
            try:
                dt = datetime.fromisoformat(started)
                if dt >= cutoff:
                    new_records.append(r)
            except (ValueError, TypeError):
                new_records.append(r)

        # 限制条目数
        self._records = new_records[: self._max_entries]

        # 重写文件
        fp = self.history_path
        try:
            with open(fp, "w", encoding="utf-8") as f:
                for r in self._records:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
        except Exception:
            pass

        self.load_history()

    def _load_selected(self) -> None:
        """加载选中任务配置。"""
        item = self._list.currentItem()
        if not item:
            return
        record = item.data(Qt.ItemDataRole.UserRole)
        config_path = record.get("config_path", "")
        if config_path and Path(config_path).is_file():
            self.load_config_requested.emit(config_path)
        else:
            QMessageBox.information(self, _("提示"), _("配置文件已不存在"))

    def _view_results(self) -> None:
        """查看选中任务结果。"""
        item = self._list.currentItem()
        if not item:
            return
        record = item.data(Qt.ItemDataRole.UserRole)
        workspace = record.get("workspace", "")
        if workspace:
            self.view_results_requested.emit(workspace)

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        """双击加载配置。"""
        self._load_selected()
