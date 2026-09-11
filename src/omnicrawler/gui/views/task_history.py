"""任务历史管理视图。

显示最近任务记录，支持重新加载配置和查看结果。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.view_base import BaseView
from ..core.workers import JsonlLoadWorker
from ..i18n import _
from ..widgets.toast import ToastManager

HISTORY_FILE = "work/task_history.jsonl"
DEFAULT_MAX_ENTRIES = 100
DEFAULT_MAX_DAYS = 30
# S3.2.1：内存有界上限（防超长文件全量驻留），显示/清理按 max_entries 截断
MAX_LOADED_RECORDS = 5000

#: 同步解析的大小上限。超过则改走后台线程——实测同步解析 10 万行约 **215 ms**
#: （10k 行 ≈ 30 ms），而历史文件只增不减。512 KiB 约合 4k 行（≈12 ms），
#: 低于一帧的预算，因此小文件继续同步以保持既有契约。
_SYNC_PARSE_MAX_BYTES = 512 * 1024


def _parse_history_file(fp: Path) -> list[dict[str, Any]]:
    """逐行解析历史文件；坏行跳过。同步与后台两条路径共用同一解析语义。"""
    records: list[dict[str, Any]] = []
    with open(fp, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


class TaskHistory(BaseView):
    """任务历史侧边栏。

    继承 `BaseView`：无障碍名/对象名由骨架统一提供，空态走统一状态区
    （`EmptyState`），不再自绘提示标签、不再写内联样式。

    Signals:
        load_config_requested: 请求加载历史配置 (config_path)。
        view_results_requested: 请求查看结果 (workspace)。
    """

    load_config_requested = Signal(str)  # config_path
    view_results_requested = Signal(str)  # workspace
    history_changed = Signal()

    def __init__(
        self,
        project_root: Path,
        parent: QWidget | None = None,
        *,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_days: int = DEFAULT_MAX_DAYS,
    ) -> None:
        super().__init__(
            accessible_name=_("历史任务"),
            object_name="taskHistory",
            parent=parent,
            margins=4,  # 侧边栏：紧凑内边距（原实现为 4px）
        )
        self._project_root = project_root
        # S3.2.1：history_max_entries 消费方——不再硬编码 100
        self._max_entries = max(1, int(max_entries))
        self._max_days = max(1, int(max_days))
        self._records: list[dict[str, Any]] = []
        # 后台加载句柄：必须持有引用，否则 QThread 会被 GC 掉（Qt 经典崩溃点）。
        # 生命周期由父子关系管理，**不要**显式 deleteLater（见 _load_history_in_background）。
        self._load_worker: JsonlLoadWorker | None = None
        # 加载期间又收到加载请求 → 结束后再读一次（避免并发 worker 与竞态）。
        self._reload_pending = False
        self.finish_setup()

    def build_ui(self, container: QWidget) -> None:
        """搭建面板；空态/内容切换交给 BaseView 的状态区。"""
        layout = QVBoxLayout(container)

        title = QLabel(_("📋 历史任务"))
        title.setObjectName("sectionTitle")  # 区块标题语义类（见 design_system）
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

    @property
    def history_path(self) -> Path:
        return self._project_root / HISTORY_FILE

    def load_history(self) -> None:
        """加载历史记录。

        2026-09-11 起**按文件大小分流**（实测依据见下）：

        * 小文件**同步**解析——保持调用方「调用后即可读 ``_records``」的既有契约
          （4 处调用点与 3 个测试都依赖它）；
        * 大文件交给 :class:`JsonlLoadWorker` **后台**解析——历史文件是只增不减的，
          实测同步解析 10 万行需 **215 ms**，足以让界面明显卡住，
          而这正是该 worker 被建成的原因（`audit-20260805/report_gui_core.md`
          「同步耗时操作阻塞 UI 线程」）。
        """
        if self._load_worker is not None:
            # 已有后台加载在跑：只记「待重载」，等它结束后再读一次（那时文件已写完）。
            # **不在这里清空 `_records`**——清空会丢掉「加载期间新增的记录」；
            # 也不打断/等待在跑的那个 worker：对已结束的 QThread 调 wait() 会直接崩
            # （Windows access violation，实测踩到过）。
            self._reload_pending = True
            return

        self._records = []
        self._list.clear()
        # A3：无记录（含文件不存在）时走统一空态；有记录则在末尾切回内容态
        self.show_empty(_("暂无历史任务"), _("完成一次任务后，这里会显示记录。"))

        fp = self.history_path
        if not fp.is_file():
            self.history_changed.emit()
            return

        try:
            too_big = fp.stat().st_size > _SYNC_PARSE_MAX_BYTES
        except OSError:
            self.history_changed.emit()
            return

        if too_big:
            self._load_history_in_background(fp)
            return

        try:
            records = _parse_history_file(fp)
        except Exception:
            self.history_changed.emit()
            return
        self._apply_records(records)

    def _load_history_in_background(self, fp: Path) -> None:
        """大文件走后台解析；完成后合并「加载期间新增的记录」再刷新界面。

        worker **挂在 self 上由父子关系管理生命周期**（不再显式 `deleteLater`）：
        显式删除会让 Python 侧残留一个指向已销毁 C++ 对象的引用，再次触碰即崩。
        """
        worker = JsonlLoadWorker(fp, parent=self)
        self._load_worker = worker
        worker.finished_loading.connect(self._on_history_loaded)
        worker.failed.connect(self._on_history_failed)
        worker.start()

    def _on_history_failed(self, _message: str) -> None:
        # 解析失败不阻塞：保持空态并通知关心历史的调用方。
        self._load_worker = None
        self._after_background_load()

    def _on_history_loaded(self, records: list[dict[str, Any]], _total: int) -> None:
        self._load_worker = None
        # 加载期间可能已有新记录写入（add_record）——按 task_id 合并，别覆盖掉它。
        merged = {str(record.get("task_id", "")): record for record in self._records}
        for record in records:
            merged.setdefault(str(record.get("task_id", "")), record)
        self._apply_records(list(merged.values()))
        self._after_background_load()

    def _after_background_load(self) -> None:
        """加载期间若有人再次请求（`_reload_pending`），此刻文件已写完，重读一次收敛。"""
        if not self._reload_pending:
            self.history_changed.emit()
            return
        self._reload_pending = False
        self.load_history()

    def _apply_records(self, records: list[dict[str, Any]]) -> None:
        """排序、截断并渲染记录（同步与后台路径共用，保证两条路径行为一致）。"""
        self._records = list(records)
        # 按时间倒序
        self._records.sort(key=lambda r: r.get("started_at", ""), reverse=True)
        # S3.2.1：内存有界（MAX_LOADED_RECORDS）；显示按 max_entries 截断，
        # 不截断 _records——update/重写不再误删文件中更旧的记录
        self._records = self._records[:MAX_LOADED_RECORDS]
        shown = self._records[: self._max_entries]

        self._list.clear()
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

        if self._list.count() == 0:
            self.show_empty(_("暂无历史任务"), _("完成一次任务后，这里会显示记录。"))
        else:
            self.show_content()
        self.history_changed.emit()

    def recent_records(self, limit: int = 4) -> list[dict[str, Any]]:
        """返回只读用途的最近任务快照，避免首页依赖内部列表。"""
        return [dict(record) for record in self._records[:max(0, limit)]]

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
        except Exception as exc:
            # A3：写入失败不再静默——运行仍继续，但用户能看到历史未落盘
            ToastManager.instance().error(_("写入历史记录失败：{0}").format(exc))

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
        except Exception as exc:
            # A3：更新失败不再静默
            ToastManager.instance().error(_("更新历史记录失败：{0}").format(exc))

        self.load_history()

    def _cleanup(self) -> None:
        """清理过期记录（按 days + max_entries，S3.2.1 消费 max_days/max_entries）。"""
        if not self._records:
            return
        # A3：清理为不可撤销操作，先确认再执行
        reply = QMessageBox.question(
            self, _("清理历史任务"),
            _("将清理 {0} 条历史记录（含过期的），此操作不可撤销。是否继续？").format(len(self._records)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

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
        removed = len(self._records) - len(new_records[: self._max_entries])
        self._records = new_records[: self._max_entries]

        # 重写文件
        fp = self.history_path
        try:
            with open(fp, "w", encoding="utf-8") as f:
                for r in self._records:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            ToastManager.instance().info(_("已清理 {0} 条历史记录").format(removed))
        except Exception as exc:
            ToastManager.instance().error(_("清理历史记录失败：{0}").format(exc))

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
