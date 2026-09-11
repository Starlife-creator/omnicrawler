"""GUI 后台 worker 集合 —— 全部基于 :class:`BackgroundWorker`（长期债收敛 / S3.1.1）。

本模块取代历史上并存的两套 worker 实现：

- ``gui/background_workers.py``：组合根用的短任务 worker（站点识别 / 操作录制 / 小样本试跑）
- ``gui/async_workers.py``：数据密集任务 worker（CSV 加载与索引、JSONL 证据查找）

统一约定（由 :class:`BackgroundWorker` 提供）：

- 子类实现 :meth:`~BackgroundWorker.work`，在工作线程执行；
- **结果经带类型的专用信号回传**（覆写 :meth:`~BackgroundWorker._emit_result`），
  在复用统一生命周期（取消 / 失败 / 清理）的同时**不丢失信号类型信息**；
  未覆写时回落到通用 ``succeeded(object)``；
- 异常经 ``failed(str)`` 回传，不伪造成功；取消统一用 ``requestInterruption()``；
- **终态契约（每次运行恰好一个终态）**：结果信号（``succeeded`` 或覆写 :meth:`_emit_result`
  后的专用信号）／ ``failed`` ／ ``interrupted`` 三者**互斥且必居其一**。取消属**正常终态**
  （对应计划 §3.3 的 ``cancelling → cancelled``），调用方应据此复位进行中状态，而非当作错误；
- ``finished`` 信号可用于释放调用方持有的引用。

**关于本模块保留的三类「未接线」worker**：``JsonlLoadWorker`` / ``SqliteQueryWorker`` /
``TemplateCombineWorker`` 当前没有调用点，但它们**不是遗留垃圾，而是尚未接线的能力**，
各自对应一条仍然开放的文档化需求（见各类 docstring 中的出处）。删除它们等于删除需求
本身，因此一律保留并显式标注为「待接线」。

历史上 ``async_workers.AsyncWorkerManager`` 的线程簿记与 5 个便捷入口已删除，依据是：
其唯一职责「统一取消」已由视图侧实现等价替代——视图把 worker 挂进 MainWindow 的对象树
（``parent=self``，视图本身经布局 reparent 进主窗口），``MainWindow._background_threads()``
用 ``findChildren(QThread)`` 全覆盖，closeEvent 走延后关闭流程；且其便捷入口从未被任何
调用点使用（``_active_workers`` 恒空），关闭时的 ``cancel_all()`` 实为空操作。
相关依据与遗留风险已写入《优化方案.md》。
"""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from .background_worker import BackgroundWorker


class SiteInspectionWorker(BackgroundWorker):
    """站点智能识别后台任务。

    ``succeeded`` 载荷为 ``(report_dict, url)`` 元组；``failed`` 载荷为错误文本。
    """

    def __init__(
        self,
        url: str,
        intent: str = "",
        robots_fail_closed: bool = True,
        fetcher: Any | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.url = url
        self.intent = intent
        self.robots_fail_closed = robots_fail_closed
        self.fetcher = fetcher

    def work(self) -> Any:
        from ...sources.site_inspector import inspect_url
        from ...templates.template_catalog import bundled_template_catalog

        report = inspect_url(
            self.url,
            bundled_template_catalog(),
            intent=self.intent,
            robots_fail_closed=self.robots_fail_closed,
            fetcher=self.fetcher,
        ).to_dict()
        return report, self.url


class ActionRecorderWorker(BackgroundWorker):
    """网页操作录制后台任务。"""

    def __init__(self, url: str, output: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._url = url
        self._output = output

    def work(self) -> Any:
        from ...fetching.action_recorder import record_with_playwright

        return record_with_playwright(self._url, self._output)


class SampleRunWorker(BackgroundWorker):
    """小样本试跑后台任务（独立工作区，不改变正式任务断点）。"""

    def __init__(self, config_path: Path, pages: int = 3, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._config_path = config_path
        self._pages = pages

    def work(self) -> Any:
        from ...core.config import load_config
        from ...pipeline_ops.preflight import run_sample

        return run_sample(load_config(self._config_path), pages=self._pages)


class CsvLoadWorker(BackgroundWorker):
    """异步 CSV 加载。

    ``finished_loading`` 载荷为 ``(headers, sample_rows, present_counts, total_rows)``：
    表头、抽样行、各字段非空计数与**完整**行数。支持 ``sample_limit`` 限制抽样行数，
    避免大文件占用内存。
    """

    finished_loading = Signal(list, list, dict, int)

    def __init__(
        self,
        path: str | Path,
        *,
        sample_limit: int = 50_000,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._path = Path(path)
        self._sample_limit = sample_limit

    def work(self) -> tuple[list[str], list[dict[str, str]], dict[str, int], int]:
        with self._path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = list(reader.fieldnames or [])
            present = {name: 0 for name in headers}
            sample_rows: list[dict[str, str]] = []
            total = 0

            for row in reader:
                if self.isInterruptionRequested():
                    return headers, sample_rows, present, total
                total += 1
                for name in headers:
                    if str(row.get(name, "")).strip():
                        present[name] += 1
                if total <= self._sample_limit:
                    sample_rows.append(dict(row))

        return headers, sample_rows, present, total

    def _emit_result(self, result: Any) -> None:
        headers, sample_rows, present, total = result
        self.finished_loading.emit(headers, sample_rows, present, total)


class CsvIndexWorker(BackgroundWorker):
    """异步 CSV 索引。

    ``finished_indexing`` 载荷为 ``(headers, total_rows, file_size)``。供
    CsvStreamModel 流式加载使用。``max_rows`` 为 None 表示完整计数（B9 语义），
    >0 时提前停止扫描。
    """

    finished_indexing = Signal(list, int, float)

    def __init__(
        self,
        path: str | Path,
        parent: QObject | None = None,
        *,
        max_rows: int | None = None,
    ) -> None:
        super().__init__(parent)
        self._path = Path(path)
        # S3.1.21：接受 max_rows 参数（调用方传值不再 TypeError）；
        # None 表示完整计数（B9 语义），>0 时提前停止扫描
        self._max_rows = max_rows

    def work(self) -> tuple[list[str], int, float]:
        file_size = self._path.stat().st_size
        with self._path.open("r", encoding="utf-8-sig") as handle:
            reader = csv.reader(handle)
            first_row = next(reader, None)
            if first_row is None:
                return [], 0, float(file_size)
            headers = [str(h).strip() for h in first_row]
            # B9：完整计数（不再截断前 100000 行）；内存占用 O(1)
            row_count = 0
            for _ in reader:
                if self.isInterruptionRequested():
                    return headers, row_count, float(file_size)
                row_count += 1
                if self._max_rows is not None and row_count >= self._max_rows:
                    break
        return headers, row_count, float(file_size)

    def _emit_result(self, result: Any) -> None:
        headers, row_count, file_size = result
        self.finished_indexing.emit(headers, row_count, file_size)


class JsonlSearchWorker(BackgroundWorker):
    """异步 JSONL 证据查找。

    命中发 ``found(record_id, record)``；未命中（含文件不存在）发
    ``not_found(record_id)``。**未命中不是失败**，因此不占用 ``failed`` 信号。
    """

    found = Signal(str, dict)
    not_found = Signal(str)

    def __init__(
        self,
        jsonl_path: str | Path,
        record_id: str,
        *,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._path = Path(jsonl_path)
        self._record_id = record_id

    def work(self) -> tuple[str, dict[str, Any] | None]:
        if not self._path.is_file():
            return self._record_id, None
        with self._path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if self.isInterruptionRequested():
                    return self._record_id, None
                line = line.strip()
                if not line:
                    continue
                try:
                    candidate = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if str(candidate.get("record_id", "")) == self._record_id:
                    return self._record_id, candidate
        return self._record_id, None

    def _emit_result(self, result: Any) -> None:
        record_id, record = result
        if record is None:
            self.not_found.emit(record_id)
        else:
            self.found.emit(record_id, record)


# ---------------------------------------------------------------------------
# 以下三类曾是「已建成、当前未接线」的能力。2026-09-11 逐项做了**测量**，
# 结论不再是推测，而是可复核的定论（每类的 docstring 里带实测数字与出处）。
#
# 保留策略：**一律保留，不删除**。P0-1 首版曾以「零调用点」为由删除它们
# （见《审查记录》§6.2 之 6），那是一次方法论错误——「零调用点」不等于「不需要」。
# ---------------------------------------------------------------------------


class JsonlLoadWorker(BackgroundWorker):
    """异步 JSONL 加载（``finished_loading`` 载荷 ``(records, total_count)``）。

    **已接线（2026-09-11）**：``views/task_history.py`` 现在**按文件大小分流**——
    512 KiB 以内保持同步（维持「调用后即可读 ``_records``」的既有契约，4 处调用点
    与 3 个测试依赖它），超过则交给本类在后台解析。

    接线的实测依据：历史文件只增不减，同步逐行 ``json.loads`` 的耗时随行数线性增长——
    100 行 6.5 ms / 10k 行 29.5 ms / **100k 行 214.6 ms**，最后一种会让界面明显卡住，
    正是 ``audit-20260805/report_gui_core.md``「同步耗时操作阻塞 UI 线程」所指。
    """

    finished_loading = Signal(list, int)

    def __init__(
        self,
        path: str | Path,
        *,
        sample_limit: int = 10_000,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._path = Path(path)
        self._sample_limit = sample_limit

    def work(self) -> tuple[list[dict[str, Any]], int]:
        records: list[dict[str, Any]] = []
        total = 0
        with self._path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if self.isInterruptionRequested():
                    return records, total
                line = line.strip()
                if not line:
                    continue
                total += 1
                if total <= self._sample_limit:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return records, total

    def _emit_result(self, result: Any) -> None:
        records, total = result
        self.finished_loading.emit(records, total)


class SqliteQueryWorker(BackgroundWorker):
    """异步 SQLite 查询（``finished_query`` 载荷 ``(column_names, rows)``）。

    **当前未接线——测量后维持「暂缓」判定（2026-09-11）**：
    0.12.0 计划 §7 / W9 的门槛是「找到真实调用方与工作负载」。现在调用方确实存在
    （``gui/views/developer_inspector.py`` 在 GUI 线程里直接开 ``StateStore``
    读运行列表与时间线），于是对它做了负载测量——构造 **200 次运行 / 100k 条状态事件**：

    * ``list_runs(50)`` / ``list_runs(500)``：**0.3 / 0.5 ms**
    * ``run_events(run_id)``（502 条）：**0.7 ms**
    * ``run_stages(run_id)``：0.0 ms

    即真实工作负载下该路径不构成 UI 阻塞，因此**不接线**（保留本类作为路径实现就位）。
    若将来状态库规模或查询复杂度显著上升，应重新测量后再决定。
    """

    finished_query = Signal(list, list)

    def __init__(
        self,
        db_path: str | Path,
        query: str,
        *,
        params: tuple[Any, ...] = (),
        row_limit: int = 10_000,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._db_path = Path(db_path)
        self._query = query
        self._params = params
        self._row_limit = row_limit

    def work(self) -> tuple[list[str], list[dict[str, Any]]]:
        conn = sqlite3.connect(str(self._db_path), timeout=5.0, check_same_thread=False)
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(self._query, self._params)
            column_names = [desc[0] for desc in cursor.description] if cursor.description else []
            rows: list[dict[str, Any]] = []
            for i, row in enumerate(cursor):
                if self.isInterruptionRequested():
                    return column_names, rows
                if i >= self._row_limit:
                    break
                rows.append(dict(row))
            return column_names, rows
        finally:
            conn.close()

    def _emit_result(self, result: Any) -> None:
        column_names, rows = result
        self.finished_query.emit(column_names, rows)


class TemplateCombineWorker(BackgroundWorker):
    """异步模板组合（``finished_combining`` 载荷为合并后的配置对象）。

    **当前未接线——但这是测量后的结论，不是遗漏（2026-09-11）**：
    审计建议「模板发现走后台线程」，而 ``main.py`` 的 ``discover_templates(force=True)``
    与 ``bundled_template_catalog(...)`` 仍在主线程执行。实测其成本：

    * ``bundled_template_catalog()`` + ``discover()``：**76 个模板 / 0.4 ms**；
    * 用户模板目录按 50 个文件估算，量级仍在毫秒内。

    主线程成本低于一帧预算，异步化只会增加状态与竞态而无用户可感收益，故**不接线**。
    若将来模板规模或磁盘延迟显著变化（例如目录含数百个大文件或位于慢速网络盘），
    应重新测量后再决定。
    """

    finished_combining = Signal(object)

    def __init__(
        self,
        template_loader: Any,
        template_names: list[str],
        *,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._loader = template_loader
        self._names = template_names

    def work(self) -> Any:
        if self.isInterruptionRequested():
            return None
        return self._loader.combine(self._names)

    def _emit_result(self, result: Any) -> None:
        self.finished_combining.emit(result)
